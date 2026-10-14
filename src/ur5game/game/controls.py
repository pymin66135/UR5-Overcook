"""键盘输入与固定步长游戏时钟。"""
from typing import Protocol
import time

class PartnerController(Protocol):
    def act(self, observation: dict, agent_index: int) -> int:
        """
        @brief  定义伙伴控制器根据观察选择动作的接口。
        @param  observation 提供给控制器的当前游戏观察字典。
        @param  agent_index 需要选择动作的玩家索引。
        @retval int 原生动作索引 0–5。
        @note   此处是协议接口，由具体控制器实现。
        """
        ...


class ManualInput:
    def __init__(self):
        """
        @brief  初始化双方的待执行手动动作。
        @param  None 无显式输入参数。
        @retval None 默认动作为等待。
        """
        self.pending = [4, 4]

    def key_action(self, actor, action):
        """
        @brief  登记指定玩家下一步要执行的键盘动作。
        @param  actor 玩家索引 0/1；0 为人类 P0，1 为 AI P1。
        @param  action 待执行的动作索引。
        @retval None 更新待执行动作。
        """
        self.pending[actor] = action

    def actions(self, held_directions=(4, 4)):
        """
        @brief  合并单次按键与持续方向键，并清空待执行动作。
        @param  held_directions 双方持续方向键对应的动作，默认 (4,4) 表示等待。
        @retval list 双方本步动作索引。
        """
        actions = [a if a != 4 else held_directions[i] for i, a in enumerate(self.pending)]
        self.pending = [4, 4]
        return actions


class FixedStepClock:
    """Frame-rate-independent accumulator; never silently drops elapsed time."""
    def __init__(self, hz):
        """
        @brief  按游戏逻辑频率初始化固定步长时钟。
        @param  hz 目标循环频率，单位 Hz。
        @retval None 初始化间隔与累计时间。
        """
        self.interval = 1.0 / hz
        self.accumulator = 0.0

    def advance(self, elapsed, paused=False):
        """
        @brief  累积界面经过时间并计算本帧应推进的游戏步数。
        @param  elapsed 距上一界面帧经过的时间，单位 s。
        @param  paused 是否暂停当前时钟或显示暂停状态。
        @retval int 本帧逻辑步数；暂停时返回 0 并清空累计。
        """
        if paused:
            self.accumulator = 0.0
            return 0
        self.accumulator += elapsed
        ticks = int((self.accumulator + 1e-9) / self.interval)
        self.accumulator -= ticks * self.interval
        return ticks


class Session:
    """GUI input adapter. P0/P1 indices stay fixed throughout a role exchange."""
    def __init__(self, env, partner_controller: PartnerController | None = None, runtime=None):
        """
        @brief  绑定游戏环境、键盘映射、时钟和可选 AI 控制器。
        @param  env 当前游戏环境对象。
        @param  partner_controller 可选的伙伴动作控制器；None 时不额外选择伙伴动作。
        @param  runtime AI 推理运行对象及本轮元信息。
        @retval None 完成输入会话初始化。
        """
        import pygame as pg
        self.env, self.partner_controller = env, partner_controller
        self.runtime = runtime
        self.input = ManualInput()
        self.clock = FixedStepClock(env.config["step_hz"])
        self.paused = self.help_open = self.debug = False
        self.keymaps = [
            {pg.K_UP: 0, pg.K_w: 0, pg.K_DOWN: 1, pg.K_s: 1, pg.K_RIGHT: 2, pg.K_d: 2,
             pg.K_LEFT: 3, pg.K_a: 3, pg.K_SPACE: 5, pg.K_RETURN: 5},
            {pg.K_i: 0, pg.K_k: 1, pg.K_l: 2, pg.K_j: 3, pg.K_u: 5}]
        names = list(env.config["recipes"])
        self.recipe_keys = [{pg.K_0: None, **dict(zip((pg.K_1, pg.K_2, pg.K_3), names))},
                            {pg.K_6: None, **dict(zip((pg.K_7, pg.K_8, pg.K_9), names))}]

    def handle(self, event, recipe_rects=(), pointer=None):
        """
        @brief  处理键盘、配方点击、暂停、退出和截图事件。
        @param  event 界面事件，或环境要记录的事件类型。
        @param  recipe_rects 配方名称与可点击矩形的对应集合。
        @param  pointer 已换算到游戏画布坐标的鼠标位置；None 表示没有点击位置。
        @retval str quit/reset/restart_requested/screenshot 等控制指令；None 表示普通处理。
        """
        import pygame as pg
        if event.type == pg.QUIT:
            return "quit"
        if event.type == pg.MOUSEBUTTONDOWN and event.button == 1 and pointer is not None:
            if self.runtime and 0 in self.runtime.controlled:
                return
            if self.paused or self.help_open or self.env.done:
                return
            for name, rect in recipe_rects:
                if rect.collidepoint(pointer):
                    self.env.select_recipe(0, name)
            return
        if event.type != pg.KEYDOWN:
            return
        key = event.key
        if key == pg.K_ESCAPE:
            return "quit"
        if key == pg.K_r:
            if self.runtime and getattr(self.runtime, "experiment_mode", False):
                return "restart_requested"
            return "reset"
        if key == pg.K_F12:
            return "screenshot"
        if key == pg.K_F10:
            self.debug = not self.debug
        elif key in (pg.K_p, pg.K_h):
            was_paused = self.paused or self.help_open
            if key == pg.K_p:
                self.paused = not self.paused
            else:
                self.help_open = not self.help_open
            is_paused = self.paused or self.help_open
            if not was_paused and is_paused:
                self.env.pause_started_perf = time.perf_counter()
            elif was_paused and not is_paused:
                self.env.pause_wall_seconds = getattr(self.env, "pause_wall_seconds", 0.0) + time.perf_counter() - self.env.pause_started_perf
                self.env.pause_started_perf = None
            self.input = ManualInput()
            self.clock.accumulator = 0
            if self.runtime:
                self.env.emit("pause_changed", paused=self.paused, help_open=self.help_open)
        elif key == pg.K_f:
            if not (self.runtime and getattr(self.runtime, "experiment_mode", False)):
                self.env.request_swap()
        elif key == pg.K_x:
            if not (self.runtime and getattr(self.runtime, "experiment_mode", False)):
                self.env.cancel_swap()
        elif key == pg.K_g:
            self.env.toggle_guides()
        elif not self.paused and not self.help_open:
            for actor in range(2):
                if self.runtime and actor in self.runtime.controlled:
                    continue
                if key in self.recipe_keys[actor]:
                    self.env.select_recipe(actor, self.recipe_keys[actor][key])
                elif key in self.keymaps[actor]:
                    self.input.key_action(actor, self.keymaps[actor][key])

    def tick(self, pressed=None):
        """
        @brief  合并手动输入与 AI 动作，推进一步游戏。
        @param  pressed 当前键盘按住状态；None 表示没有持续方向输入。
        @retval tuple 环境 step 返回的观察、得分变化、结束标记及事件信息。
        """
        held = [4, 4]
        if pressed is not None:
            for actor, keymap in enumerate(self.keymaps):
                held[actor] = next((a for k, a in keymap.items() if a < 4 and pressed[k]), 4)
        actions = self.input.actions(held)
        if self.runtime:
            return self.runtime.step(self.env, actions)
        if self.partner_controller is not None:
            actions[1] = self.partner_controller.act(self.env.observe(), 1)
        return self.env.step(actions)
