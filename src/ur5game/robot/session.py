"""单次机器人会话：连接、基线采样、明确激活、控制、停止。"""
from contextlib import suppress
from datetime import datetime, timezone
from dataclasses import replace
import json
import math
from pathlib import Path
import statistics
import time

from .admittance import Admittance, SafetyFault, dot, norm
from .ur5_device import URRTDEDevice
from .homing import HomeMotion
from ..config import ConfigurationError, require_live_approval
from .types import Sample
from .guards import check_range

class RobotSession:
    def __init__(self, config, limits, *, enable=False, device=None, log_path=None,
                 fixed_origin=None):
        """
        @brief  创建机器人会话与可选固定起点归位器。
        @param  config 当前模块使用的配置字典。
        @param  limits Limits 控制限制与导纳参数对象。
        @param  enable 是否明确请求实机控制；False 不允许发送运动指令。
        @param  device 可注入的设备适配器；None 时构造实际 UR5/HEX 适配器。
        @param  log_path 可选机器人轨迹 JSONL 文件路径；None 时不创建轨迹流。
        @param  fixed_origin 固定起点的六维基座位姿 [x,y,z,rx,ry,rz]，位置 m、旋转向量 rad；None 表示未指定。
        @retval None 建立会话状态，尚未建立硬件连接。
        """
        self.config, self.limits = config, limits
        self.policy = limits.safety_policy
        self.enable_requested = bool(enable)
        self.device = device or URRTDEDevice(config["host"], limits,
                                           config.get("controller_version"), config)
        self.log_path = Path(log_path) if log_path else None
        self.log = None
        self.model = None
        self.fault = None
        self.connected = False
        self.armed = False
        self.last_output = None
        self.last_sample = None
        self.last_timing = None
        self.prepared = False
        self.fixed_origin = tuple(fixed_origin) if fixed_origin is not None else None
        if self.fixed_origin is not None and (len(self.fixed_origin) != 6
                or not all(math.isfinite(v) for v in self.fixed_origin)):
            raise ConfigurationError("Fixed origin requires six finite base-frame pose values")
        self.home_motion = HomeMotion(limits, config) if self.fixed_origin is not None else None
        self.previous_phase = None

    def connect(self):
        """
        @brief  建立只读连接并检查机器人运行和安全状态。
        @param  None 无显式输入参数。
        @retval Sample 当前机器人采样。
        """
        self.device.connect_readonly()
        sample = self.device.sample()
        if sample.robot_mode != 7 or sample.safety_mode != 1:
            raise SafetyFault("Robot must be RUNNING with NORMAL safety state")
        self.connected = True
        self.last_sample = sample
        return sample

    def arm(self):
        """
        @brief  先完成基线准备，再明确激活控制接口。
        @param  None 无显式输入参数。
        @retval None 准备和激活成功。
        @note   需要 --enable、只读连接和配置确认；激活本身尚未发送速度。
        """
        self.prepare()
        self.activate_prepared()

    def prepare(self, check_cancelled=None):
        """
        @brief  静止采样并计算力零点，校验固定起点对应工作空间。
        @param  check_cancelled 准备期间的取消检查回调；None 时不额外执行回调。
        @retval None 成功后 prepared=True。
        @note   调用前应已连接、请求实机使能且未激活；本阶段只读。
        """
        if not self.enable_requested:
            raise ConfigurationError("No --enable: read-only mode cannot command the robot")
        require_live_approval(self.config)
        if self.fault is not None:
            raise SafetyFault("Previous stop/fault is latched; restart the session")
        if not self.connected or self.armed:
            raise RuntimeError("Connect once before arming")
        self.prepared = False
        samples = []
        baseline_seconds = self.config.get("force_baseline_seconds",
                                           3.0 if self.config.get("force_source") == "hex_udp" else .5)
        check_range(self.policy, "force_baseline_seconds", baseline_seconds)
        deadline = time.monotonic() + baseline_seconds
        while time.monotonic() < deadline or len(samples) < self.policy["baseline_min_samples"]:
            if check_cancelled is not None:
                check_cancelled()
            sample = self.device.sample()
            if (len(sample.pose) != 6 or len(sample.speed) != 6 or len(sample.wrench) != 6
                    or not all(math.isfinite(x) for x in (*sample.pose, *sample.speed, *sample.wrench))):
                self.stop("baseline_feedback_invalid")
                raise SafetyFault("RTDE baseline contains invalid values")
            if sample.robot_mode != 7 or sample.safety_mode != 1:
                self.stop("baseline_safety_state_changed")
                raise SafetyFault("Safety state changed during force baseline")
            if norm(sample.speed[:3]) > self.policy["stationary_speed_m_s"]:
                self.stop("baseline_arm_not_stationary")
                raise SafetyFault("Hold the handle still for force baseline")
            if samples and sample.timestamp <= samples[-1].timestamp:
                self.stop("baseline_feedback_stale")
                raise SafetyFault("RTDE force baseline did not advance")
            samples.append(sample)
            time.sleep(self.policy["baseline_sample_interval_s"])
        axis = self.limits.axis_base
        forces = [dot(s.wrench[:3], axis) for s in samples]
        baseline = statistics.mean(forces)
        noise = statistics.pstdev(forces)
        mean_force_vector = tuple(statistics.mean(s.wrench[i] for s in samples)
                                  for i in range(3))
        force_vector_noise = max(statistics.pstdev(s.wrench[i] for s in samples)
                                 for i in range(3))
        if (not math.isfinite(baseline) or not math.isfinite(noise)
                or norm(mean_force_vector) > self.policy["baseline_max_force_n"]
                or force_vector_noise > self.policy["baseline_max_noise_n"]):
            self.stop("baseline_force_invalid")
            raise SafetyFault("Unstable/uncompensated 3D force baseline: "
                              f"{tuple(round(x, 1) for x in mean_force_vector)} N, "
                              f"noise {force_vector_noise:.1f} N; config safety_policy limits: "
                              f"norm <= {self.policy['baseline_max_force_n']:g} N, "
                              f"noise <= {self.policy['baseline_max_noise_n']:g} N")
        baseline_y = mean_force_vector[1] if self.limits.planar_enabled else 0.0
        start = (replace(samples[-1], pose=self.fixed_origin)
                 if self.fixed_origin is not None else samples[-1])
        self.model = Admittance(self.limits, start, baseline, baseline_y)
        if self.fixed_origin is not None:
            # Validate current location against the fixed test corridor before
            # any control connection. Never widen it to reach an arbitrary pose.
            self.model.last_timestamp = samples[-1].timestamp - 1 / self.limits.control_hz
            self.model.tick(samples[-1], 0, "wait")
        self.last_sample = samples[-1]
        self.prepared = True

    def activate_prepared(self, *, after_control_setup=None):
        """
        @brief  基线准备后建立控制接口，并重新检查即时反馈。
        @param  after_control_setup 控制接口建立后的同步回调；None 时直接检查新反馈。
        @retval None 成功后 armed=True。
        @note   调用前必须 prepared=True；连接后可等待界面同步回调。
        """
        if not self.enable_requested:
            raise ConfigurationError("No --enable: read-only mode cannot command the robot")
        require_live_approval(self.config)
        if self.fault is not None or not self.prepared or self.armed:
            raise SafetyFault("A successful preflight is required; restart after any stop/fault")
        try:
            sample = self.device.sample()
            if (not all(math.isfinite(x) for x in (*sample.pose, *sample.speed, *sample.wrench))
                    or norm(sample.speed[:3]) > self.policy["stationary_speed_m_s"]
                    or norm(sample.wrench[:3]) > self.policy["baseline_max_force_n"]):
                raise SafetyFault("Feedback changed after preflight; release handle and restart")
            if sample.timestamp <= self.model.last_timestamp:
                raise SafetyFault("Feedback did not advance after preflight")
            # Waiting at READY must not reset the calibrated origin or force zero.
            self.model.last_timestamp = sample.timestamp - 1 / self.limits.control_hz
            self.model.tick(sample, 0, "wait")
            self.last_sample = sample
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self.log = self.log_path.open("x", encoding="utf-8")
            self.device.arm()
            # The game can wait for a post-setup UI frame here, before sampling
            # a new timebase and before ANY velocity command. Cancellation raises.
            if after_control_setup is not None:
                after_control_setup()
            # Control-interface setup can take seconds; validate the fresh state
            # before permitting the first velocity command.
            fresh = self.device.sample()
            if fresh.timestamp <= sample.timestamp:
                raise SafetyFault("Feedback stale during control-interface setup")
            if (norm(fresh.speed[:3]) > self.policy["stationary_speed_m_s"]
                    or norm(fresh.wrench[:3]) > self.policy["baseline_max_force_n"]):
                raise SafetyFault("Robot motion/force changed during control-interface setup")
            self.model.last_timestamp = fresh.timestamp - 1 / self.limits.control_hz
            self.model.tick(fresh, 0, "wait")
            self.last_sample = fresh
            self.armed = True
            self.prepared = False
        except BaseException:
            self.stop("arm_failed")
            raise

    def tick(self, level, phase, damping_override=None, *, command_guard=None):
        """
        @brief  读取实测反馈，计算导纳或归位速度，检查授权后发送并记录。
        @param  level 三档索引 0/1/2，对应轻/中/重档。
        @param  phase 控制阶段标识，按调用接口使用 push/return/wait/home。
        @param  damping_override 可选阻尼覆盖值，单位 N·s/m；三档质量/刚度模式不接受覆盖。
        @param  command_guard 发送速度前的授权检查回调；None 时无附加外部检查。
        @retval Output 本周期控制结果。
        @note   调用前必须 armed=True；command_guard 在发送前再次执行。
        @warning 可发送实际运动指令；只能由所属控制循环调用。
        """
        if not self.armed:
            raise RuntimeError("Not armed")
        started = time.monotonic()
        previous_timestamp = self.model.last_timestamp
        sample = None
        sampled = started
        command_started = None
        command_finished = None
        try:
            sample = self.device.sample()
            sampled = time.monotonic()
            if phase == "home":
                if self.home_motion is None:
                    raise ConfigurationError("Save and load a fixed start before using H")
                if self.previous_phase != "home":
                    self.home_motion.reset()
                output = self.model.tick(sample, level, "wait", damping_override)
                output = self.home_motion.tick(sample, output, sample.timestamp - previous_timestamp)
            else:
                if self.home_motion:
                    self.home_motion.reset()
                output = self.model.tick(sample, level, phase, damping_override)
            self.previous_phase = phase
            # A threaded game can revoke permission while sample() is blocked.
            # Recheck immediately before sending, not just at the cycle start.
            if command_guard is not None:
                command_guard()
            command_started = time.monotonic()
            self.device.command(output.velocity_base)
            command_finished = time.monotonic()
            self.last_timing = {
                "feedback_dt_ms": (sample.timestamp - previous_timestamp) * 1000,
                "sample_ms": (sampled - started) * 1000,
                "command_ms": (command_finished - command_started) * 1000,
                "cycle_ms": (command_finished - started) * 1000,
            }
            self.last_sample, self.last_output = sample, output
            if self.log:
                self.log.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(),
                    "event": "sample", "timing": self.last_timing,
                    "timing_hold": output.timing_hold,
                    "robot_timestamp": sample.timestamp, "pose": sample.pose, "speed": sample.speed,
                    "wrench": sample.wrench, "level": level, "phase": phase,
                    "force_source": self.config.get("force_source", "ur5_internal_estimate"),
                    "robot_wrench_reference": sample.robot_wrench,
                    "hex_raw_wrench": sample.sensor_raw_wrench,
                    "damping_n_s_m": output.damping_n_s_m,
                    "mass_kg": output.mass_kg, "stiffness_n_m": output.stiffness_n_m,
                    "interaction_force_n": output.interaction_force_n,
                    "lateral_force_n": output.lateral_force_n,
                    "interaction_force_xy_n": output.interaction_force_xy_n,
                    "lateral_position_m": output.lateral_position_m,
                    "y_centering": output.y_centering,
                    "y_virtual_spring_force_n": output.y_virtual_spring_force_n,
                    "progress_percent": output.progress_percent,
                    "origin_pose": self.model.origin,
                    "fixed_start_used": self.fixed_origin is not None,
                    "home_complete": bool(phase == "home" and self.home_motion.done),
                    "velocity_command": output.velocity_base,
                    "source": ("UR5_RTDE_HEX_REAL" if self.config.get("force_source") == "hex_udp"
                               else "UR5_RTDE_REAL")}, allow_nan=False) + "\n")
                self.log.flush()
            return output
        except BaseException as exc:
            # Stop hardware BEFORE diagnostic file I/O. Keep first-cycle faults
            # in the trace as well, instead of leaving an unexplained empty file.
            log, self.log = self.log, None
            try:
                self.stop(f"controller_fault:{type(exc).__name__}:{exc}")
            finally:
                if log:
                    try:
                        with suppress(OSError, ValueError):
                            log.write(json.dumps({
                                "event": "fault", "utc": datetime.now(timezone.utc).isoformat(),
                                "error": f"{type(exc).__name__}:{exc}",
                                "level": level, "phase": phase,
                                "previous_robot_timestamp": previous_timestamp,
                                "robot_timestamp": sample.timestamp if sample else None,
                                "pose": sample.pose if sample else None,
                                "speed": sample.speed if sample else None,
                                "wrench": sample.wrench if sample else None,
                                "sample_ms": (sampled - started) * 1000,
                                "command_attempted": command_started is not None,
                                "command_returned": command_finished is not None,
                            }, allow_nan=False) + "\n")
                    finally:
                        with suppress(OSError):
                            log.close()
            raise

    def stop(self, reason="operator_stop"):
        """
        @brief  锁定停止原因、停止模型和设备并关闭轨迹记录。
        @param  reason 停止、故障或日志收尾原因字符串。
        @retval None 更新停止状态并请求设备减速停止。
        """
        self.fault = reason
        self.armed = False
        self.prepared = False
        if self.model:
            self.model.stop(reason)
        try:
            self.device.stop()
        finally:
            if self.log:
                self.log.close()
                self.log = None

    def close(self):
        """
        @brief  停止会话并释放机器人及传感器资源。
        @param  None 无显式输入参数。
        @retval None 完成资源释放。
        """
        try:
            self.stop("closed")
        finally:
            self.device.close()
