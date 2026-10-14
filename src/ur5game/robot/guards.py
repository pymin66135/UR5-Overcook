"""Numeric policy in one place; explicit JSON policy overrides legacy defaults.

These are host software checks, not certified controller safety settings.
Invalid numbers and missing/unknown explicit keys fail closed.
"""
from copy import deepcopy
import math


def default_policy(planar=False):
    """
    @brief  获取单轴或 XY 模式的兼容默认数值策略。
    @param  planar 是否生成 XY 模式的兼容默认策略。
    @retval dict 独立的默认策略字典。
    @note   完整显式配置由 JSON 覆盖；此函数不修改控制器硬件限制。
    """
    return {
        "upper": {
            "travel_m": .30 if planar else .10,
            "max_speed_m_s": .25 if planar else .02,
            "max_tracking_speed_m_s": .30 if planar else .05,
            "max_accel_m_s2": .50 if planar else .10,
            "max_force_n": 50. if planar else 20.,
            "max_lateral_m": .01, "max_rotation_rad": .10,
            "mass_kg": 40., "damping_n_s_m": 500., "stiffness_n_m": 400.,
            "planar_y_travel_m": .20, "planar_y_center_speed_m_s": .02,
            "planar_y_center_tolerance_m": .001,
            "home_speed_m_s": .20, "home_abort_force_n": 10.,
            "home_position_gain_s_inv": 3., "game_endpoint_hold_s": 2.,
        },
        "ranges": {
            "control_hz": [10., 50.], "hex_timeout_s": [.005, .05],
            "force_baseline_seconds": [.5, 5.],
            "home_tolerance_m": [.0005, .002], "home_timeout_s": [1., 60.],
        },
        "baseline_max_force_n": 50., "baseline_max_noise_n": 10.,
        "stationary_speed_m_s": .002, "baseline_min_samples": 25,
        "baseline_sample_interval_s": .02, "dashboard_timeout_s": 2.,
        "planar_y_damping_min_n_s_m": 1.,
        "game_input_timeout_s": .5, "game_state_timeout_s": .5,
        "startup_ui_sync_timeout_s": 2., "stroke_tolerance_m": .001,
        "feedback_soft_periods": 2., "feedback_hard_periods": 3.,
        "parameter_slew_s": .3,
        "capture_samples": 25, "capture_angular_speed_rad_s": .01,
        "capture_position_drift_m": .001, "capture_rotation_drift_rad": .005,
    }


def resolve_policy(value=None, *, planar=False):
    """
    @brief  校验完整显式策略或返回兼容默认策略。
    @param  value 要检查、编码或绘制的值，具体含义由当前函数决定。
    @param  planar 是否生成 XY 模式的兼容默认策略。
    @retval dict 已校验策略的深拷贝。
    """
    template = default_policy(planar)
    if value is None:
        return template  # Compatibility for older configs only.
    def check(candidate, expected, path):
        """
        @brief  递归核对策略字段、区间和值的类型及有限性。
        @param  candidate 当前待验证的策略节点。
        @param  expected 策略节点应符合的结构或数值模板。
        @param  path 目标配置、记录或模型文件路径。
        @retval None 当前策略节点有效。
        """
        if isinstance(expected, dict):
            if not isinstance(candidate, dict) or candidate.keys() != expected.keys():
                raise ValueError(f"{path}: explicit policy must contain exactly {sorted(expected)}")
            for key in expected:
                check(candidate[key], expected[key], f"{path}.{key}")
        elif isinstance(expected, list):
            if not isinstance(candidate, (tuple, list)) or len(candidate) != 2:
                raise ValueError(f"{path}: expected [minimum, maximum]")
            for item in candidate:
                check(item, 1., path)
            if candidate[0] > candidate[1]:
                raise ValueError(f"{path}: minimum exceeds maximum")
        elif (isinstance(candidate, bool) or not isinstance(candidate, (int, float))
              or not math.isfinite(candidate) or candidate <= 0):
            raise ValueError(f"{path}: expected finite positive number")
    check(value, template, "safety_policy")
    for key in ("baseline_min_samples", "capture_samples"):
        if not isinstance(value[key], int) or value[key] < 2:
            raise ValueError(f"safety_policy.{key}: expected integer >= 2")
    if not 1 <= value["feedback_soft_periods"] < value["feedback_hard_periods"]:
        raise ValueError("Feedback periods require 1 <= soft < hard")
    return deepcopy(value)


def check_range(policy, name, value):
    """
    @brief  校验一个参数是否处于策略指定允许区间。
    @param  policy 集中数值策略字典；None 时使用兼容默认策略。
    @param  name 物品或配方名称，依当前接口使用。
    @param  value 要检查的有限数值。
    @retval None 值有效；无效时抛出 ValueError。
    """
    low, high = policy["ranges"][name]
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError(f"{name}={value!r} outside safety_policy.ranges.{name} [{low}, {high}]")
