"""Persistent start capture (read-only) and bounded XY homing; no I/O on import."""
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from .admittance import SafetyFault, norm
from .guards import check_range
from .rotations import orientation_distance


def start_path(config_path, config=None):
    """
    @brief  解析保存固定起点的文件路径。
    @param  config_path 实验配置文件路径。
    @param  config 当前模块使用的配置字典。
    @retval Path 固定起点文件路径。
    """
    return Path(config["fixed_start_file"]) if config else Path(config_path).parent / "home.json"


def load_start(config_path, config):
    """
    @brief  读取固定起点并验证机器人、工具和 TCP 配置匹配。
    @param  config_path 实验配置文件路径。
    @param  config 当前模块使用的配置字典。
    @retval tuple 六维基座位姿，单位 m/rad；None 表示未启用固定起点。
    """
    if not config.get("fixed_start_enabled", False):
        return None
    path = start_path(config_path, config)
    if not path.exists():
        raise ValueError(f"Fixed start missing: {path}. Position the handle at the desired start, "
                         "then run standalone --config ... --save-start (read-only).")
    data = json.loads(path.read_text(encoding="utf-8"))
    pose = data.get("pose_base_m_rad")
    if (data.get("schema") != 1 or data.get("host") != config["host"]
            or data.get("tcp_pose_m_rad") != config["tcp_pose_m_rad"]
            or data.get("tool_mass_kg") != config["tool_mass_kg"]
            or data.get("tool_center_of_gravity_m") != config["tool_center_of_gravity_m"]
            or not isinstance(pose, list) or len(pose) != 6
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(v) for v in pose)):
        raise ValueError("Saved start is invalid or robot/TCP/payload configuration changed")
    return tuple(pose)


def capture_start(config_path, config, limits, *, device=None):
    """
    @brief  只读采样静止位姿并首次保存固定起点文件。
    @param  config_path 实验配置文件路径。
    @param  config 当前模块使用的配置字典。
    @param  limits Limits 控制限制与导纳参数对象。
    @param  device 可注入的设备适配器；None 时构造实际 UR5/HEX 适配器。
    @retval tuple (保存文件 Path, 六维位姿列表)。
    @note   现有文件不覆盖；使用 RTDE 接收连接，不建立控制接口。
    """
    from .ur5_device import URRTDEDevice
    if not limits.planar_enabled:
        raise ValueError("Fixed-start homing is supported only by the standalone XY profile")
    path = start_path(config_path, config)
    if path.exists():
        raise FileExistsError(f"Start already saved: {path}. Keep it, or explicitly rename it "
                              "before recording a different start.")
    device = device or URRTDEDevice(config["host"], limits,
        config.get("controller_version"), {"force_source": "ur5_internal_estimate"})
    try:
        device.connect_readonly()
        rows = []
        policy = limits.safety_policy
        for _ in range(policy["capture_samples"]):
            row = device.sample()
            if (len(row.pose) != 6 or len(row.speed) != 6
                    or not all(math.isfinite(v) for v in (*row.pose, *row.speed, row.timestamp))
                    or row.robot_mode != 7 or row.safety_mode != 1
                    or norm(row.speed[:3]) > policy["stationary_speed_m_s"]
                    or norm(row.speed[3:]) > policy["capture_angular_speed_rad_s"]):
                raise SafetyFault("Keep the robot stationary and RUNNING/NORMAL to save its start")
            if rows and (row.timestamp <= rows[-1].timestamp
                    or norm(tuple(row.pose[i] - rows[0].pose[i] for i in range(3))) > policy["capture_position_drift_m"]
                    or orientation_distance(row.pose[3:6], rows[0].pose[3:6]) > policy["capture_rotation_drift_rad"]):
                raise SafetyFault("Start capture requires fresh, stationary feedback")
            rows.append(row)
            time.sleep(policy["baseline_sample_interval_s"])
        data = {"schema": 1, "host": config["host"],
                "saved_utc": datetime.now(timezone.utc).isoformat(),
                "pose_base_m_rad": list(rows[-1].pose),
                "tcp_pose_m_rad": config["tcp_pose_m_rad"],
                "tool_mass_kg": config["tool_mass_kg"],
                "tool_center_of_gravity_m": config["tool_center_of_gravity_m"]}
        with path.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
        return path, data["pose_base_m_rad"]
    finally:
        device.close()


class HomeMotion:
    """H selects a low-speed XY return inside the fixed-start test corridor.

    No Z/rotation movement, no arbitrary-location MoveJ/MoveL and no rearming.
    The caller must keep checking enable/deadman and all model feedback guards.
    """
    def __init__(self, limits, config):
        """
        @brief  校验并保存 XY 主动归位参数。
        @param  limits Limits 控制限制与导纳参数对象。
        @param  config 当前模块使用的配置字典。
        @retval None 初始化归位状态。
        """
        if not limits.planar_enabled:
            raise ValueError("Fixed-start homing requires the XY profile")
        self.limits = limits
        self.speed = config.get("home_speed_m_s", .02)
        self.tolerance = config.get("home_tolerance_m", .001)
        self.timeout = config.get("home_timeout_s", 20.)
        self.force_limit = config.get("home_abort_force_n", 10.)
        self.position_gain = config.get("home_position_gain_s_inv", 1.)
        values = (self.speed, self.tolerance, self.timeout, self.force_limit, self.position_gain)
        upper = limits.safety_policy["upper"]
        check_range(limits.safety_policy, "home_tolerance_m", self.tolerance)
        check_range(limits.safety_policy, "home_timeout_s", self.timeout)
        if (not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)
                or not 0 < self.speed <= min(upper["home_speed_m_s"], limits.max_speed_m_s)
                or not 0 < self.force_limit <= min(upper["home_abort_force_n"], limits.max_force_n)
                or not 0 < self.position_gain <= upper["home_position_gain_s_inv"]):
            raise ValueError("Invalid home settings: check safety_policy.upper and command/force limits")
        self.reset()

    def reset(self):
        """
        @brief  清空归位计时、速度和完成标记。
        @param  None 无显式输入参数。
        @retval None 恢复未开始状态。
        """
        self.velocity = (0., 0.)
        self.started = None
        self.done = False
        self.entry_stopped = False

    def tick(self, sample, checked, dt):
        """
        @brief  在检查合格的反馈上计算制动、接近与归位速度。
        @param  sample 当前机器人 Sample 采样，位姿 m/rad、速度 m/s 和 rad/s、力 N。
        @param  checked 已通过导纳反馈与工作空间检查的 Output 控制结果。
        @param  dt 当前计算时间步长，单位 s。
        @retval Output 带 home 阶段的归位控制输出。
        @note   先确认停止再归位；Z 与姿态不主动运动，超时或对抗力超限抛出异常。
        """
        if self.started is None:
            self.started = sample.timestamp
        if sample.timestamp - self.started > self.timeout:
            raise SafetyFault("home_timeout: homing did not complete within its time limit")
        if checked.interaction_force_xy_n > self.force_limit:
            raise SafetyFault(f"home_contact_force: {checked.interaction_force_xy_n:.2f} N "
                              f"> {self.force_limit:g} N; stop opposing the home motion")
        if checked.timing_hold:
            self.velocity = (0., 0.)
            return replace(checked, phase="home", velocity_base=(0.,) * 6)
        if not self.entry_stopped:
            if norm(sample.speed[:3]) > self.limits.safety_policy["stationary_speed_m_s"]:
                # One H press can be used after pushing: brake first, then
                # begin the return only after fresh feedback confirms rest.
                self.velocity = (0., 0.)
                return replace(checked, phase="home", velocity_base=(0.,) * 6)
            self.entry_stopped = True
        error = (-checked.position_m, -checked.lateral_position_m)
        distance = math.hypot(*error)
        if distance <= self.tolerance:
            self.done = True
            self.velocity = (0., 0.)
            return replace(checked, phase="home", velocity_base=(0.,) * 6,
                           y_centering=False)
        # P-like approach plus a braking-distance cap reserves the hard timing
        # interval. Rate limit the reference; the UR controller limits real accel.
        accel = self.limits.max_accel_m_s2 / math.sqrt(2)
        reserve = self.limits.safety_policy["feedback_hard_periods"] / self.limits.control_hz
        # Brake toward the actual origin, not the completion-tolerance edge.
        # Subtracting tolerance can asymptotically stop just OUTSIDE it due to
        # floating-point cancellation, causing a false timeout without motion.
        braking = math.sqrt((accel * reserve) ** 2 +
                            2 * accel * distance) - accel * reserve
        speed = min(self.speed, self.position_gain * distance, braking)
        target = tuple(v * speed / distance for v in error)
        delta = tuple(a - b for a, b in zip(target, self.velocity))
        magnitude = math.hypot(*delta)
        step = self.limits.max_accel_m_s2 * min(dt, 1 / self.limits.control_hz)
        scale = min(1., step / magnitude) if magnitude else 1.
        self.velocity = tuple(v + scale * d for v, d in zip(self.velocity, delta))
        vx, vy = self.velocity
        # Cannot command farther past X origin or a Y boundary during approach.
        if checked.position_m <= 0 and vx < 0:
            vx = 0.
        if checked.position_m >= self.limits.travel_m and vx > 0:
            vx = 0.
        if abs(checked.lateral_position_m) >= self.limits.planar_y_travel_m and vy * checked.lateral_position_m > 0:
            vy = 0.
        self.velocity = (vx, vy)
        return replace(checked, phase="home", velocity_base=(-vx, vy, 0., 0., 0., 0.),
                       y_centering=False)
