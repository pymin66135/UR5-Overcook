"""保存每局游戏事件、配置、摘要与实验元信息。"""
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

class EpisodeLog:
    def __init__(self, env, output, mode, metadata=None):
        """
        @brief  创建本轮事件、配置与结果日志文件。
        @param  env 当前游戏环境对象。
        @param  output 数据输出路径；None 时使用配置中的默认位置。
        @param  mode 游戏运行模式名称。
        @param  metadata 要写入结果的本局模型、条件和参与者等元信息。
        @retval None 打开事件记录流并保存配置。
        """
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        self.base = output / f"forced_coop_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
        self.stream = self.base.with_suffix(".events.jsonl").open("w", encoding="utf-8")
        self.index, self.started, self.mode = 0, time.perf_counter(), mode
        self.metadata = metadata or {}
        self.base.with_suffix(".config.json").write_text(json.dumps({"config": env.config,
            "human_role": env.human_role, "mode": mode, "metadata": self.metadata}, indent=2), encoding="utf-8")
        self.flush(env)

    def flush(self, env):
        """
        @brief  把环境中新追加的事件写入 JSONL 记录。
        @param  env 当前游戏环境对象。
        @retval None 更新已写事件索引；记录流已关闭时跳过。
        """
        if self.stream.closed:
            return
        for event in env.events[self.index:]:
            self.stream.write(json.dumps(event) + "\n")
        self.index = len(env.events)
        self.stream.flush()

    def close(self, env, reason):
        """
        @brief  收尾事件并保存游戏摘要和 CSV 索引。
        @param  env 当前游戏环境对象。
        @param  reason 停止、故障或日志收尾原因字符串。
        @retval None 已关闭时直接返回。
        """
        if self.stream.closed:
            return
        env.emit("session_closed", reason=reason)
        self.flush(env)
        summary = {**env.summary(), "mode": self.mode, "metadata": self.metadata, "close_reason": reason,
                   "wall_seconds": time.perf_counter() - self.started}
        summary["pause_wall_seconds"] = getattr(env, "pause_wall_seconds", 0.0) + (
            time.perf_counter() - env.pause_started_perf if getattr(env, "pause_started_perf", None) is not None else 0.0)
        summary["pause_events"] = sum(e["event"] == "pause_changed" for e in env.events)
        if hasattr(env, "diagnostics"):
            summary["diagnostics"] = env.diagnostics.summary()
        self.base.with_suffix(".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if self.metadata:
            row = {"episode": self.base.name, "mode": self.mode,
                   **{k: self.metadata.get(k) for k in ("participant", "session", "condition", "trial", "policy_seed", "checkpoint_sha256")},
                   "close_reason": reason, "complete": env.done, "score": env.score,
                   "orders": summary["completed_orders"], "game_seconds": summary["game_seconds"],
                   "wall_seconds": summary["wall_seconds"], "failures": summary["failures"]}
            for actor, metrics in enumerate(summary["agents"]):
                row.update({f"p{actor}_{key}": value for key, value in metrics.items()})
            table = self.base.parent / "episodes.csv"
            exists = table.exists()
            with table.open("a", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=row.keys())
                if not exists:
                    writer.writeheader()
                writer.writerow(row)
        self.stream.close()
        print(f"Saved: {self.base}.summary.json", flush=True)
