"""机器人采样与导纳输出。单位：m、rad、s、N。"""
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class Sample:
    timestamp: float
    pose: tuple[float, ...]
    speed: tuple[float, ...]
    wrench: tuple[float, ...]
    robot_mode: int
    safety_mode: int
    robot_wrench: tuple[float, ...] | None = None
    sensor_raw_wrench: tuple[float, ...] | None = None


@dataclass(frozen=True)
class Output:
    velocity_base: tuple[float, float, float, float, float, float]
    progress_percent: float
    position_m: float
    interaction_force_n: float
    damping_n_s_m: float
    phase: str
    mass_kg: float = 0.0
    stiffness_n_m: float = 0.0
    timing_hold: bool = False
    lateral_position_m: float = 0.0
    lateral_force_n: float = 0.0
    interaction_force_xy_n: float = 0.0
    y_centering: bool = False
    y_virtual_spring_force_n: float = 0.0

def dot(a, b):
    """
    @brief  计算两向量对应分量的内积。
    @param  a 第一个向量，调用方应保证与 b 维数一致。
    @param  b 第二个向量，调用方应保证与 a 维数一致。
    @retval float 内积结果。
    @note   当前实现按 zip 对齐分量；调用方应保证维数一致。
    """
    return sum(x * y for x, y in zip(a, b))


def norm(a):
    """
    @brief  计算向量欧氏模长。
    @param  a 第一个向量，调用方应保证与 b 维数一致。
    @retval float 非负模长，单位随输入向量。
    """
    return math.sqrt(dot(a, a))
