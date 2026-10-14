"""Start-to-start pacing; a blocking speedL call already consumes its period."""
from __future__ import annotations

import time


class ControlCadence:
    def __init__(self, hz):
        """
        @brief  按指定频率初始化控制周期起始时间。
        @param  hz 目标循环频率，单位 Hz。
        @retval None 保存周期并安排下一起始时刻。
        """
        self.period = 1.0 / hz
        self.next_start = time.monotonic() + self.period

    def delay(self):
        """
        @brief  计算距离下一控制周期的剩余等待时间。
        @param  None 无显式输入参数。
        @retval float 非负秒数；到期返回 0。
        """
        return max(0.0, self.next_start - time.monotonic())

    def due(self):
        """
        @brief  判断下一控制周期是否已经到期。
        @param  None 无显式输入参数。
        @retval bool True 表示可开始下一周期。
        """
        return self.delay() == 0.0

    def begin(self):
        # Schedule before, not after, the blocking command. No catch-up bursts
        # after a missed deadline; real feedback freshness is checked separately.
        """
        @brief  以当前周期起始时刻安排下一个周期。
        @param  None 无显式输入参数。
        @retval None 更新下一次起始时刻。
        @note   应在可能阻塞的发送之前调用，避免额外再等待一整个周期。
        """
        self.next_start = time.monotonic() + self.period
