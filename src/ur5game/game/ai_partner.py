"""high/low 已有模型的加载与动作推理，模型参数保持只读。"""
import json
from pathlib import Path
import torch

from .policy import Policy, KIND, ACTION_DIM, digest, file_hash, encode, apply_actions


def load_checkpoint(path, scene):
    """
    @brief  校验并加载两名玩家的只读模型权重。
    @param  path 目标配置、记录或模型文件路径。
    @param  scene 地图、配方和游戏时序配置字典。
    @retval tuple (两个 Policy 模型, 模型载荷字典)。
    """
    payload = torch.load(path, map_location="cpu", weights_only=True)
    if (payload.get("kind") != KIND or payload.get("action_dim") != ACTION_DIM
            or payload.get("digest") != digest(scene)
            or payload.get("digest") != digest(payload["config"])
            or len(payload.get("policy_states", [])) != 2):
        raise ValueError("Model type, scene or policy pair mismatch")
    policies = [Policy(payload["dimension"], payload["hidden"]) for _ in range(2)]
    for model, state in zip(policies, payload["policy_states"]):
        model.load_state_dict(state)
        model.eval()
        model.requires_grad_(False)
    return policies, payload


class AIPartner:
    experiment_mode = True
    log_mode = "ur5_interactive_experience"
    both = False
    controlled = {1}

    def __init__(self, manifest, scene, level="high", seed=41, deterministic=False):
        """
        @brief  选择 high/low 模型，检查哈希并初始化推理状态。
        @param  manifest 模型清单文件路径。
        @param  scene 地图、配方和游戏时序配置字典。
        @param  level AI 模型等级，取 low 或 high。
        @param  seed 游戏及模型动作采样的随机种子；None 时采用入口配置。
        @param  deterministic True 使用最高概率动作，False 按概率随机采样。
        @retval None 完成只读模型加载。
        """
        torch.set_num_threads(1)
        manifest = Path(manifest)
        report = json.loads(manifest.read_text(encoding="utf-8"))
        if (report.get("kind") != "ur5_overcooked_inference_v1" or not report.get("qualified")
                or report.get("config_digest") != digest(scene) or level not in ("low", "high")):
            raise ValueError("AI manifest does not match this scene/level")
        path = (manifest.parent / report[level]["file"]).resolve()
        if file_hash(path) != report[level]["sha256"]:
            raise ValueError("AI model file hash differs from manifest")
        self.policies, payload = load_checkpoint(path, scene)
        self.seed = seed
        self.trial = -1
        self.deterministic = deterministic
        source = payload["source"]
        self.base_metadata = {
            "participant": "interactive_experience", "condition": level,
            "checkpoint": str(path), "checkpoint_sha256": file_hash(path),
            "source_checkpoint_sha256": source["sha256"],
            "ppo_iteration": source["iteration"],
            "training_environment_steps": source["training_environment_steps"],
            "initialization": source["initialization"],
            "deterministic": deterministic, "controllers": ["human", "ppo"],
            "data_source": "INTERACTIVE_EXPERIENCE_NOT_FORMAL_STUDY",
        }

    def configure_env(self, env):
        """
        @brief  设置人类/AI 槽位并按本局种子初始化环境。
        @param  env 当前游戏环境对象。
        @retval None 更新环境和推理随机数生成器。
        """
        self.trial += 1
        self.generator = torch.Generator().manual_seed(self.seed + self.trial)
        env.control_modes = self.base_metadata["controllers"][:]
        env.reset(seed=self.seed + self.trial)
        env.experiment_mode = True
        env.study_human_slot = 0
        env.partner_display_label = "AI Partner"
        env.test_run = True
        env.help_key_label = "F1"
        env.emit("controller_attached", **self.metadata())

    def metadata(self):
        """
        @brief  获取模型来源、条件与当前局种子的记录信息。
        @param  None 无显式输入参数。
        @retval dict 当前推理元信息。
        """
        return {**self.base_metadata, "trial": self.trial + 1,
                "policy_seed": self.seed + self.trial, "environment_seed": self.seed + self.trial}

    def step(self, env, manual_actions):
        """
        @brief  保留人类动作，由已有模型选择 P1 动作并执行一步游戏。
        @param  env 当前游戏环境对象。
        @param  manual_actions 双方手动动作列表，P1 可由模型动作覆盖。
        @retval tuple 游戏一步结果。
        @note   使用 inference_mode（推理模式），模型参数不更新。
        """
        observation = env.observe()
        env.emit("decision_observation", observation=observation)
        actions = list(manual_actions)
        with torch.inference_mode():
            logits, _ = self.policies[1](torch.from_numpy(encode(observation, 1)).unsqueeze(0))
            probabilities = logits.softmax(-1).squeeze(0)
            action = (int(logits.argmax(-1).item()) if self.deterministic else
                      int(torch.multinomial(probabilities, 1, generator=self.generator).item()))
        actions[1] = action
        env.emit("policy_decision", 1, action=action, probability=float(probabilities[action]))
        return apply_actions(env, actions)
