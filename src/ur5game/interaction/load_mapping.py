"""上一游戏时间窗的加权持物移动距离，决定下一时间窗的负载档位。"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class LoadChange:
    source_window: int
    applied_window: int
    weighted_cells: float
    level: int


class LoadMapper:
    def __init__(self, config):
        """
        @brief  初始化游戏时间窗、物品权重与档位映射。
        @param  config 当前模块使用的配置字典。
        @retval None 校验并保存映射参数。
        """
        self.window_seconds = config["window_seconds"]
        self.q_ref = config["q_ref_cells"]
        self.weights = dict(config["item_weights"])
        self.weights[None] = self.weights.pop("empty")
        if (not math.isfinite(self.window_seconds) or self.window_seconds <= 0
                or not math.isfinite(self.q_ref) or self.q_ref <= 0
                or any(not math.isfinite(v) or v < 0 for v in self.weights.values())):
            raise ValueError("Invalid interaction window/reference/weights")
        self.window = 0
        self.quantity = 0.
        self.last_position = None
        self.last_held = None

    def reset(self, env):
        """
        @brief  以当前人类玩家状态初始化搬运量统计。
        @param  env 当前游戏环境对象。
        @retval None 清空窗口累计并保存当前位置和持物。
        """
        player = env.state.players[0]
        self.window, self.quantity = 0, 0.
        self.last_position = tuple(player.position)
        self.last_held = getattr(player.held_object, "name", None)
        if self.last_held not in self.weights:
            raise ValueError(f"unknown_held_object:{self.last_held}")

    def feed(self, env):
        """
        @brief  根据人类玩家移动更新搬运量并在窗口结束时选择下一档。
        @param  env 当前游戏环境对象。
        @retval LoadChange 新窗口档位变化；None 表示尚未切换窗口。
        @note   每格按移动前持物加权；站立不增加搬运量。
        """
        player = env.state.players[0]
        current = tuple(player.position)
        held = getattr(player.held_object, "name", None)
        if held not in self.weights:
            raise ValueError(f"unknown_held_object:{held}")
        distance = sum(abs(a-b) for a, b in zip(current, self.last_position))
        if distance == 1:
            self.quantity += self.weights[self.last_held]
        elif distance > 1:
            raise ValueError("non_adjacent_game_position")
        self.last_position, self.last_held = current, held
        next_window = int(env.time / env.config["step_hz"] // self.window_seconds)
        if next_window <= self.window:
            return None
        level = 0 if self.quantity < self.q_ref / 3 else 1 if self.quantity < 2*self.q_ref/3 else 2
        result = LoadChange(self.window, next_window, self.quantity, level)
        self.window, self.quantity = next_window, 0.
        return result
