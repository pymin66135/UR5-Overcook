"""一次性导出已有可信本地模型；保留推理权重与来源，不进行训练。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bootstrap import activate
activate()
import torch
from ur5game.game.policy import digest, KIND, ACTION_DIM


def file_hash(path):
    """
    @brief  计算源模型或模型清单的 SHA-256 校验值。
    @param  path 目标配置、记录或模型文件路径。
    @retval str 十六进制文件摘要。
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(manifest_path, destination):
    """
    @brief  从已校验的本地模型导出推理权重和模型清单。
    @param  manifest_path 经过校验的源模型清单文件路径。
    @param  destination 模型导出目标目录。
    @retval None 写入 low/high 权重与 manifest.json。
    @note   只读取已知可信的本地训练产物；目标文件存在时拒绝覆盖。
    """
    source = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    scene = json.loads((ROOT / "configs/scene.json").read_text(encoding="utf-8"))
    if not source.get("qualified") or source["config_digest"] != digest(scene):
        raise ValueError("Source model qualification does not match this scene")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "qualified": True, "config_digest": digest(scene),
              "source_manifest_sha256": file_hash(manifest_path),
              "kind": "ur5_overcooked_inference_v1"}
    for level in ("low", "high"):
        entry = source[level]
        path = Path(entry["checkpoint"])
        if file_hash(path) != entry["sha256"]:
            raise ValueError(f"{level}: source checkpoint hash changed")
        # 仅处理原工程的已校验本地训练产物。导出后运行使用 weights_only=True。
        payload = torch.load(path, map_location="cpu", weights_only=False)
        if payload["kind"] != KIND or payload["action_dim"] != ACTION_DIM or payload["digest"] != digest(scene):
            raise ValueError(f"{level}: checkpoint scene/type mismatch")
        converted = {key: payload[key] for key in
                     ("kind", "config", "digest", "dimension", "hidden", "action_dim", "policy_states")}
        converted["source"] = {"sha256": entry["sha256"], "iteration": payload["iteration"],
                               "training_environment_steps": payload["steps"],
                               "initialization": payload["settings"].get("warmstart", {})}
        output = destination / f"{level}.pt"
        if output.exists():
            raise FileExistsError(output)
        torch.save(converted, output)
        report[level] = {"file": output.name, "sha256": file_hash(output),
                         "source_sha256": entry["sha256"],
                         "qualification": {key: entry[key] for key in ("training_steps", "selection") if key in entry}}
        print(f"Exported {level}: {output.name}; {output.stat().st_size} bytes")
    with (destination / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "models")
    args = parser.parse_args()
    export(args.source_manifest, args.output)
