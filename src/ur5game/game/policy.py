"""已有模型的结构、观测编码与动作执行；仅用于推理。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

KIND = "forced_coop_independent_ppo_v1"
ACTION_DIM = 10  # six native actions; Auto/A/B/C recipe keys (consume one tick).
PHASES = ("empty", "wait2", "add2", "wait3", "add3", "final", "ready", "burnt")


def digest(config):
    # Display preferences and RNG seed do not alter the policy's environment contract.
    """
    @brief  计算模型场景契约的 SHA-256 摘要。
    @param  config 当前模块使用的配置字典。
    @retval str 十六进制场景摘要。
    @note   种子、提示和控制显示设置不进入场景契约。
    """
    gameplay = {k: v for k, v in config.items() if k not in ("seed", "counter_guides", "control")}
    return hashlib.sha256(json.dumps(gameplay, sort_keys=True).encode()).hexdigest()


def file_hash(path):
    """
    @brief  读取文件并计算 SHA-256 校验值。
    @param  path 目标配置、记录或模型文件路径。
    @retval str 十六进制文件摘要。
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def onehot(value, choices):
    """
    @brief  按给定候选列表生成独热编码。
    @param  value 要检查、编码或绘制的值，具体含义由当前函数决定。
    @param  choices 独热编码对应的有序候选值列表。
    @retval list[float] 匹配项为 1，其余为 0。
    """
    return [float(value == v) for v in choices]


def encode(obs, actor):
    """
    @brief  将当前可观察游戏状态编码为模型输入向量。
    @param  obs 模型编码使用的当前游戏观察字典。
    @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
    @retval numpy.ndarray 包含地图、玩家、订单、时序和动作掩码的 float32 向量。
    """
    grid = obs["map"]["grid"]
    h, w = len(grid), len(grid[0])
    board = np.zeros((h, w, 12), dtype=np.float32)
    for y, row in enumerate(grid):
        for x, cell in enumerate(row):
            board[y, x, " XOTDPS".index(cell if cell not in "12" else " ")] = 1
    for obj in obs["objects"]:
        x, y = obj["position"]
        board[y, x, 7 + ("onion", "tomato", "dish", "soup").index(obj["name"])] = 1
    features = []
    for i in (actor, 1 - actor):
        p = obs["players"][i]
        x, y = p["position"]
        board[y, x, 11] = 1 if i == actor else -1
        held = p["held"]
        features += [x / w, y / h, *p["orientation"]]
        features += onehot(held["name"] if held else None, (None, "onion", "tomato", "dish", "soup"))
        features += onehot(p["role"], ("prep", "kitchen"))
        features += onehot(obs["selected"][i], (None, *obs["recipes"]))
    hz = obs["timing"]["step_hz"]
    for pot in obs["pots"]:
        recipe = obs["orders"][pot["order"]]["recipe"] if pot["order"] is not None else None
        features += onehot(pot["phase"], PHASES)
        features += onehot(recipe, (None, *obs["recipes"]))
        features += onehot(pot["next_ingredient"], (None, "onion", "tomato"))
        features += [len(pot["feed_ticks"]) / 3]
        features += [np.clip(((pot[k] or obs["tick"]) - obs["tick"]) / hz / 10, -1, 1)
                     for k in ("open_tick", "deadline", "ready_tick")]
    # Ordered pending requirements, not indices into the ever-growing order history.
    pending = [o["recipe"] for o in obs["orders"] if o["status"] == "pending"][:6]
    for recipe in pending + [None] * (6 - len(pending)):
        features += onehot(recipe, (None, *obs["recipes"]))
    features += [obs["remaining_seconds"] / 120]
    features += onehot(obs["swap"]["status"], (None, "Requested", "Switching", "Ready", "Cancelled"))
    features += onehot((obs["swap"].get("targets") or [None, None])[actor], (None, "prep", "kitchen"))
    mask = list(obs["legal_actions"][actor]) + [True] * 4
    if obs["done"]:
        mask = [False] * ACTION_DIM
        mask[4] = True
    return np.concatenate((board.ravel(), np.asarray(features, np.float32), np.asarray(mask, np.float32)))


def apply_actions(env, actions):
    """
    @brief  把十类模型动作转换为原生动作或配方选择，并执行游戏步。
    @param  env 当前游戏环境对象。
    @param  actions 双方动作索引序列。
    @retval tuple 环境一步执行结果。
    """
    native = []
    for actor, action in enumerate(actions):
        action = int(action)
        if not 0 <= action < ACTION_DIM:
            raise ValueError("PPO action outside 0..9")
        if action >= 6:
            env.select_recipe(actor, [None, *env.config["recipes"]][action - 6])
            native.append(4)
        else:
            native.append(action)
    return env.step(native)


class Policy(nn.Module):
    """保留原模型层名和权重形状，确保已训练模型动作一致。"""
    def __init__(self, dimension, hidden=128):
        """
        @brief  按已保存权重的尺寸创建模型网络。
        @param  dimension 模型输入向量长度。
        @param  hidden 网络隐藏层宽度。
        @retval None 初始化共享层、动作输出层和价值输出层。
        """
        super().__init__()
        self.dimension, self.hidden = dimension, hidden
        self.body = nn.Sequential(nn.Linear(dimension, hidden), nn.Tanh(), nn.Linear(hidden, hidden), nn.Tanh())
        self.actor = nn.Linear(hidden, ACTION_DIM)
        self.critic = nn.Linear(hidden, 1)

    def forward(self, x):
        """
        @brief  计算带动作掩码的模型输出。
        @param  x 模型输入 Tensor（张量），最后十项为动作有效掩码。
        @retval tuple (动作 logits 张量, 状态价值张量)。
        @note   本函数只前向计算，运行入口不会执行参数更新。
        """
        z = self.body(x)
        return self.actor(z).masked_fill(x[..., -ACTION_DIM:] < .5, -1e9), self.critic(z).squeeze(-1)
