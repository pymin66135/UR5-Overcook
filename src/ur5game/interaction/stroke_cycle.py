"""按实际位置管理推出、停稳停留、人工拉回；不驱动自动回拉。"""
import math
from ..robot.guards import resolve_policy

class StrokeCycle:
    """Actual position, not game time, completes each out-and-back stroke."""
    def __init__(self, travel, tolerance=None, hold_seconds=.4, policy=None):
        """
        @brief  初始化位置到位容差、终点停留及完整行程计数。
        @param  travel 推出总行程，单位 m。
        @param  tolerance 到位容差，单位 m；None 时取数值策略中的默认容差。
        @param  hold_seconds 终点确认停稳后的等待时长，单位 s。
        @param  policy 集中数值策略字典；None 时使用兼容默认策略。
        @retval None 初始阶段为 push。
        """
        self.policy = resolve_policy(policy)
        tolerance = self.policy["stroke_tolerance_m"] if tolerance is None else tolerance
        if not 0 < tolerance < travel / 4:
            raise ValueError("Stroke tolerance must be positive and below one quarter travel")
        self.travel, self.tolerance = travel, tolerance
        if (isinstance(hold_seconds, bool) or not isinstance(hold_seconds, (int, float))
                or not math.isfinite(hold_seconds)
                or not 0 <= hold_seconds <= self.policy["upper"]["game_endpoint_hold_s"]):
            raise ValueError("game_endpoint_hold_s exceeds safety_policy.upper or is invalid")
        self.hold_seconds = hold_seconds
        self.hold_since = None
        self.hold_remaining = hold_seconds
        self.phase = "push"
        self.outbound_complete = False
        self.completed = 0

    def start(self, position):
        """
        @brief  根据当前位置选择初始推出或人工返回阶段。
        @param  position 相对起点的轴向位置，单位 m；测试环境函数中为游戏网格坐标。
        @retval None 更新阶段并清除半程状态。
        """
        self.phase = "return" if position > self.tolerance else "push"
        self.outbound_complete = False
        self.hold_since = None

    def home(self):
        """
        @brief  标记主动归位阶段并取消未完成行程计数资格。
        @param  None 无显式输入参数。
        @retval None 更新阶段和停留状态。
        """
        self.phase = "home"
        self.outbound_complete = False
        self.hold_since = None

    def update(self, position, *, home_done=False, timing_hold=False,
               timestamp=None, speed_m_s=None):
        """
        @brief  按位置和新鲜反馈切换推出、停留、返回或归位完成。
        @param  position 相对起点的轴向位置，单位 m；测试环境函数中为游戏网格坐标。
        @param  home_done 主动归位是否已经完成。
        @param  timing_hold 是否处于反馈软超时导致的零速度等待。
        @param  timestamp 机器人反馈时间戳，单位 s；None 表示未提供。
        @param  speed_m_s 实测平移速度模长，单位 m/s；None 表示未提供。
        @retval tuple (原阶段, 新阶段)；None 表示阶段未改变。
        @note   延迟反馈不能完成行程；停留仅在反馈确认停稳后计时。
        """
        if timing_hold:
            self.hold_since = None
            self.hold_remaining = self.hold_seconds
            return None
        before = self.phase
        if self.phase == "push" and position >= self.travel - self.tolerance:
            self.phase = "endpoint_hold" if self.hold_seconds else "return"
            self.outbound_complete = True
            self.hold_since = None
            self.hold_remaining = self.hold_seconds
        elif self.phase == "endpoint_hold":
            # Dwell begins only after a zero-command cycle AND fresh feedback
            # confirms the TCP has stopped. Never sleep or stop sampling here.
            if (timestamp is None or speed_m_s is None
                    or not math.isfinite(timestamp) or not math.isfinite(speed_m_s)
                    or speed_m_s < 0 or speed_m_s > self.policy["stationary_speed_m_s"]
                    or self.hold_since is not None and timestamp < self.hold_since):
                self.hold_since = None
                self.hold_remaining = self.hold_seconds
            else:
                if self.hold_since is None:
                    self.hold_since = timestamp
                self.hold_remaining = max(0., self.hold_seconds - (timestamp - self.hold_since))
                if self.hold_remaining <= 1e-9:
                    self.phase = "return"
        elif self.phase == "return" and position <= self.tolerance:
            self.completed += int(self.outbound_complete)
            self.phase, self.outbound_complete = "push", False
        elif self.phase == "home" and home_done:
            self.phase = "push"
        return (before, self.phase) if before != self.phase else None
