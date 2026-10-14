"""导纳计算：输入采样和参数，输出六维速度；这里没有网络或文件操作。"""
import math
from ..config import Limits
from .types import Sample, Output, dot, norm
from .rotations import orientation_distance


class SafetyFault(RuntimeError):
    pass


class Admittance:
    """X return is manual; optional bounded Y spring recenters while enabled."""

    def __init__(self, limits: Limits, start: Sample, baseline_force_n: float,
                 baseline_y_force_n: float = 0.0):
        """
        @brief  以初始位姿和力基线初始化导纳计算器。
        @param  limits Limits 控制限制与导纳参数对象。
        @param  start 初始机器人采样，用于建立位置和时间基准。
        @param  baseline_force_n 推出轴方向的静止力基线，单位 N。
        @param  baseline_y_force_n 基座 Y 方向的静止力基线，单位 N。
        @retval None 初始化模型状态，不执行硬件操作。
        """
        self.limits = limits
        self.origin = start.pose
        self.baseline = baseline_force_n
        self.baseline_y = baseline_y_force_n
        self.last_timestamp = start.timestamp
        self.v = 0.0
        self.vy = 0.0
        self.damping = limits.damping_n_s_m[0]
        self.mass = limits.mass_levels_kg[0] if limits.has_profile_levels else limits.mass_kg
        self.stiffness = 0.0
        self.filtered_force = 0.0
        self.filtered_force_y = 0.0
        self.fault = None
        self.timing_hold = False
        self.y_centering = False

    def stop(self, reason="operator_stop"):
        """
        @brief  清零虚拟速度并锁定模型故障或停止原因。
        @param  reason 停止、故障或日志收尾原因字符串。
        @retval None 更新模型停止状态。
        """
        self.v = 0.0
        self.vy = 0.0
        self.y_centering = False
        self.fault = reason

    def _planar_velocity(self, dt, force_x, force_y, x, y, phase):
        """
        @brief  计算 X/Y 导纳速度并施加合成限速、边界制动和回中。
        @param  dt 当前计算时间步长，单位 s。
        @param  force_x 死区处理后的推出方向人力，单位 N。
        @param  force_y 死区处理后的 Y 方向人力，单位 N。
        @param  x 沿 Base X− 的推出位置，单位 m。
        @param  y 基座 Y 相对起点的偏移，单位 m。
        @param  phase 控制阶段标识，按调用接口使用 push/return/wait/home。
        @retval None 更新模型内部 v、vy 和回中状态。
        """
        lim = self.limits
        spring = self.stiffness * max(0., x) if phase == "push" else 0.
        target_x = (force_x - spring) / self.damping
        proposed_x = target_x + (self.v - target_x) * math.exp(-self.damping * dt / self.mass)
        spring_y = lim.planar_y_stiffness_n_m * y
        target_y = (force_y - spring_y) / lim.planar_y_damping_n_s_m
        proposed_y = target_y + (self.vy - target_y) * math.exp(
            -lim.planar_y_damping_n_s_m * dt / lim.planar_y_mass_kg)
        # Limit total acceleration and total speed, not each component separately.
        dx, dy = proposed_x - self.v, proposed_y - self.vy
        change = math.hypot(dx, dy)
        factor = min(1., lim.max_accel_m_s2 * dt / change) if change else 1.
        vx, vy = self.v + factor * dx, self.vy + factor * dy
        vx = max(0., vx) if phase == "push" else min(0., vx)
        speed = math.hypot(vx, vy)
        factor = min(1., lim.max_speed_m_s / speed) if speed else 1.
        vx, vy = vx * factor, vy * factor

        # Reserve distance for one maximum feedback interval plus deceleration.
        # Split acceleration budget across axes for diagonal braking.
        braking_accel = lim.max_accel_m_s2 / math.sqrt(2)
        latency = lim.safety_policy["feedback_hard_periods"] / lim.control_hz
        def bounded(v, position, low, high):
            """
            @brief  根据剩余空间和反馈延迟限制边界方向速度。
            @param  v 待限制的带符号速度，单位 m/s。
            @param  position 相对起点的轴向位置，单位 m；测试环境函数中为游戏网格坐标。
            @param  low 轴向位置下界，单位 m。
            @param  high 轴向位置上界，单位 m。
            @retval float 带原方向符号的受限速度，单位 m/s。
            """
            remaining = max(0., high - position if v > 0 else position - low)
            cap = math.sqrt((braking_accel * latency) ** 2 +
                            2 * braking_accel * remaining) - braking_accel * latency
            return math.copysign(min(abs(v), cap), v)
        self.v = bounded(vx, x, 0., lim.travel_m)
        self.vy = bounded(vy, y, -lim.planar_y_travel_m, lim.planar_y_travel_m)
        # The force deadband applies only to hand force, not the virtual spring.
        # Returning to Y zero needs no hand force but requires live enable.
        self.y_centering = lim.planar_y_stiffness_n_m > 0 and force_y == 0.
        if self.y_centering:
            if abs(y) <= lim.planar_y_center_tolerance_m:
                self.vy = 0.
                self.y_centering = False
            else:
                self.vy = math.copysign(min(abs(self.vy), lim.planar_y_center_speed_m_s), self.vy)
                if self.vy * y < 0:
                    # Brake approaching the center just as at an outer edge.
                    self.vy = bounded(self.vy, y, min(0., y), max(0., y))

    def tick(self, sample: Sample, level: int, phase: str, damping_override=None) -> Output:
        """
        @brief  检查反馈和工作空间，平滑变参并输出本周期导纳速度。
        @param  sample 当前机器人 Sample 采样，位姿 m/rad、速度 m/s 和 rad/s、力 N。
        @param  level 三档索引 0/1/2，对应轻/中/重档。
        @param  phase 算法阶段，仅接受 push、return 或 wait。
        @param  damping_override 可选阻尼覆盖值，单位 N·s/m；三档质量/刚度模式不接受覆盖。
        @retval Output 六维速度、行程、力、档位与时序状态。
        @note   只计算；异常抛出 SafetyFault，硬件发送由 RobotSession 执行。
        """
        if self.fault:
            raise SafetyFault(self.fault)
        lim = self.limits
        if level not in (0, 1, 2) or phase not in ("push", "return", "wait"):
            self.stop("invalid_level_or_phase")
            raise SafetyFault(self.fault)
        dt = sample.timestamp - self.last_timestamp
        soft_dt = lim.safety_policy["feedback_soft_periods"] / lim.control_hz
        hard_dt = lim.safety_policy["feedback_hard_periods"] / lim.control_hz
        late = dt > soft_dt
        if not 0 < dt <= hard_dt or (late and self.timing_hold):
            self.stop("feedback_stale_or_clock_jump")
            raise SafetyFault(f"{self.fault}: feedback interval={dt * 1000:.1f} ms, "
                              f"limit={hard_dt * 1000:.1f} ms; "
                              f"consecutive overrun={bool(late and self.timing_hold)}; "
                              f"robot timestamp {self.last_timestamp:.6f} -> {sample.timestamp:.6f}")
        self.last_timestamp = sample.timestamp
        if sample.robot_mode != 7 or sample.safety_mode != 1:
            self.stop("robot_not_running_or_safety_not_normal")
            raise SafetyFault(self.fault)
        if (len(sample.pose) != 6 or len(sample.speed) != 6 or len(sample.wrench) != 6
                or not all(math.isfinite(x) for x in (*sample.pose, *sample.speed, *sample.wrench))):
            self.stop("invalid_feedback")
            raise SafetyFault(self.fault)
        displacement = tuple(sample.pose[i] - self.origin[i] for i in range(3))
        x = dot(displacement, lim.axis_base)
        y = displacement[1] if lim.planar_enabled else 0.0
        lateral = (abs(displacement[2]) if lim.planar_enabled else
                   norm(tuple(displacement[i] - x * lim.axis_base[i] for i in range(3))))
        # 旋转向量表示不唯一；检查最短姿态夹角，避免接近 π 时的等价表示误报。
        rotation = orientation_distance(sample.pose[3:6], self.origin[3:6])
        speed = norm(sample.speed[:3])
        force = dot(sample.wrench[:3], lim.axis_base) - self.baseline
        force_y = sample.wrench[1] - self.baseline_y if lim.planar_enabled else 0.
        force_norm = math.hypot(force, force_y)
        exceeded = []
        if x < -lim.travel_margin_m or x > lim.travel_m + lim.travel_margin_m:
            exceeded.append(f"X travel {x * 100:.2f} cm outside {-lim.travel_margin_m * 100:.2f}..{(lim.travel_m + lim.travel_margin_m) * 100:.2f} cm")
        if lim.planar_enabled and abs(y) > lim.planar_y_travel_m + lim.travel_margin_m:
            exceeded.append(f"Y travel {y * 100:.2f} cm exceeds +/-{(lim.planar_y_travel_m + lim.travel_margin_m) * 100:.2f} cm")
        if lateral > lim.max_lateral_m:
            exceeded.append(f"{'Z drift' if lim.planar_enabled else 'lateral drift'} {lateral * 1000:.2f} mm > {lim.max_lateral_m * 1000:.2f} mm")
        if rotation > lim.max_rotation_rad:
            exceeded.append(f"rotation {rotation:.4f} rad > {lim.max_rotation_rad:.4f} rad")
        if speed > lim.max_tracking_speed_m_s:
            exceeded.append(f"speed {speed * 100:.2f} cm/s > {lim.max_tracking_speed_m_s * 100:.2f} cm/s")
        if force_norm > lim.max_force_n:
            exceeded.append(f"{'XY' if lim.planar_enabled else 'axis'} force {force_norm:.2f} N > {lim.max_force_n:.2f} N")
        if exceeded:
            self.stop("workspace_speed_or_force_limit")
            raise SafetyFault(f"{self.fault}: " + "; ".join(exceeded))
        if damping_override is not None and (not math.isfinite(damping_override)
                or not lim.damping_n_s_m[0] <= damping_override <= lim.damping_n_s_m[-1]):
            self.stop("invalid_damping_override")
            raise SafetyFault(self.fault)
        if lim.has_profile_levels and damping_override is not None:
            self.stop("profile_cannot_override_damping")
            raise SafetyFault(self.fault)
        self.timing_hold = late
        self.y_centering = False
        if late:
            # One isolated scheduling hiccup is a ZERO velocity command, not a
            # latched fault. Never integrate the delayed sample or reuse velocity.
            # A second late sample faults; only a normal fresh interval recovers.
            self.v = 0.0
            self.vy = 0.0
            self.filtered_force = 0.0
            self.filtered_force_y = 0.0
            return Output((0.0,) * 6, 100 * max(0.0, min(1.0, x / lim.travel_m)),
                          x, force, self.damping, phase, self.mass, self.stiffness,
                          timing_hold=True, lateral_position_m=y, lateral_force_n=force_y,
                          interaction_force_xy_n=force_norm)
        # Accepted jitter must not turn into a larger acceleration/parameter step.
        dt = min(dt, 1 / lim.control_hz)
        self.filtered_force += lim.force_filter_alpha * (force - self.filtered_force)
        effective_force = math.copysign(max(0.0, abs(self.filtered_force) - lim.force_deadband_n),
                                        self.filtered_force)
        self.filtered_force_y += lim.force_filter_alpha * (force_y - self.filtered_force_y)
        effective_force_y = math.copysign(max(0., abs(self.filtered_force_y) - lim.force_deadband_n),
                                         self.filtered_force_y)
        target_damping = (damping_override if damping_override is not None else lim.damping_n_s_m[level]) \
            if phase == "push" else lim.damping_n_s_m[0]
        # Configured slew avoids a step change as the next game window begins.
        slew = lim.safety_policy["parameter_slew_s"]
        max_change = (lim.damping_n_s_m[-1] - lim.damping_n_s_m[0]) * dt / slew
        self.damping += max(-max_change, min(max_change, target_damping - self.damping))
        damping = self.damping
        if lim.has_profile_levels:
            target_mass = lim.mass_levels_kg[level if phase == "push" else 0]
            target_stiffness = lim.stiffness_levels_n_m[level] if phase == "push" else 0.0
            mass_step = (max(lim.mass_levels_kg) - min(lim.mass_levels_kg)) * dt / slew
            stiffness_step = max(lim.stiffness_levels_n_m) * dt / slew
            self.mass += max(-mass_step, min(mass_step, target_mass - self.mass))
            self.stiffness += max(-stiffness_step, min(stiffness_step,
                                                       target_stiffness - self.stiffness))
        mass = self.mass
        stiffness = self.stiffness
        if phase == "wait":
            self.v = 0.0
            self.vy = 0.0
        elif lim.planar_enabled:
            self._planar_velocity(dt, effective_force, effective_force_y, x, y, phase)
        else:
            # M*dv/dt + B*v + K*x = F_h. K only resists the outbound push;
            # it must never pull the handle back without a human return force.
            spring_force = stiffness * max(0.0, x) if phase == "push" else 0.0
            acceleration = (effective_force - damping * self.v - spring_force) / mass
            acceleration = max(-lim.max_accel_m_s2, min(lim.max_accel_m_s2, acceleration))
            self.v = max(-lim.max_speed_m_s, min(lim.max_speed_m_s, self.v + acceleration * dt))
            if phase == "push":
                self.v = max(0.0, self.v)
            else:
                self.v = min(0.0, self.v)
            if (x <= 0 and self.v < 0) or (x >= lim.travel_m and self.v > 0):
                self.v = 0.0
        velocity = ((-self.v, self.vy, 0.0, 0.0, 0.0, 0.0) if lim.planar_enabled else
                    tuple(component * self.v for component in lim.axis_base) + (0.0, 0.0, 0.0))
        return Output(velocity, 100 * max(0.0, min(1.0, x / lim.travel_m)), x,
                      force, damping, phase, mass, stiffness,
                      lateral_position_m=y, lateral_force_n=force_y,
                      interaction_force_xy_n=force_norm, y_centering=self.y_centering,
                      y_virtual_spring_force_n=(-lim.planar_y_stiffness_n_m * y
                                               if lim.planar_enabled and phase != "wait" else 0.))
