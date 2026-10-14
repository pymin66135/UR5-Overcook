"""Native Overcooked sprites plus compact, English-only pixel HUD."""
import math

import pygame
from overcooked_ai_py.visualization.state_visualizer import StateVisualizer

BG = (15, 16, 18)
INK = (233, 232, 220)
MUTED = (153, 151, 141)
GOLD = (215, 181, 80)
GREEN = (113, 200, 132)
RED = (225, 104, 81)


class Renderer:
    tile = 40
    board_y = 160

    def __init__(self):
        """
        @brief  加载原生游戏图像、字体和物品图标。
        @param  None 无显式输入参数。
        @retval None 初始化渲染资源。
        """
        self.native = StateVisualizer(tile_size=self.tile, is_rendering_hud=False,
                                      is_rendering_cooking_timer=False)
        self.font = pygame.font.SysFont("consolas", 17)
        self.small = pygame.font.SysFont("consolas", 13)
        self.tiny = pygame.font.SysFont("consolas", 11, bold=True)
        self.recipe_rects = []
        self.icons = {}
        for name in ("onion", "tomato", "dish"):
            source = pygame.Surface((15, 15), pygame.SRCALPHA)
            self.native.OBJECTS_IMG.blit_on_surface(source, (0, 0), name)
            self.icons[name] = source

    def text(self, canvas, value, x, y, color=INK, font=None):
        """
        @brief  在指定画布位置绘制文字。
        @param  canvas 要绘制或附加面板的 Pygame 画布。
        @param  value 要检查、编码或绘制的值，具体含义由当前函数决定。
        @param  x 文字或图标的画布横坐标，单位像素。
        @param  y 文字或图标的画布纵坐标，单位像素。
        @param  color RGB 文字颜色。
        @param  font 可选字体；None 时使用模块默认字体。
        @retval None 修改目标画布。
        """
        canvas.blit((font or self.font).render(str(value), True, color), (x, y))

    def food(self, canvas, name, x, y, size=20):
        """
        @brief  在指定画布位置绘制物品图标。
        @param  canvas 要绘制或附加面板的 Pygame 画布。
        @param  name 物品或配方名称，依当前接口使用。
        @param  x 文字或图标的画布横坐标，单位像素。
        @param  y 文字或图标的画布纵坐标，单位像素。
        @param  size 文字字号或图标边长，具体由绘制接口决定。
        @retval None 修改目标画布。
        """
        canvas.blit(pygame.transform.scale(self.icons[name], (size, size)), (x, y))

    def draw(self, env, help_open=False, paused=False, debug=False, animation_time=None):
        """
        @brief  绘制地图、人物、订单、烹饪时序和提示。
        @param  env 当前游戏环境对象。
        @param  help_open 是否显示帮助覆盖层。
        @param  paused 是否暂停当前时钟或显示暂停状态。
        @param  debug 是否显示调试信息。
        @param  animation_time 可选动画时间，单位 s。
        @retval pygame.Surface 本帧游戏画布。
        """
        board = self.native.render_state(env.state, env.mdp.terrain_mtx)
        width = max(520, board.get_width() + 160)
        canvas = pygame.Surface((width, board.get_height() + self.board_y + 28))
        canvas.fill(BG)
        bx = (width - board.get_width()) // 2
        time_left = max(0, env.config["episode_seconds"] - env.time / env.config["step_hz"])
        status = "FINISHED" if env.done else "PAUSED" if paused or help_open else ""
        self.text(canvas, f"Time {time_left:05.1f}   Score {env.score:03d}   {status}", 16, 9)
        if getattr(env, "test_run", False):
            self.text(canvas, "TEST", width - 65, 10, GOLD, self.small)
        pending = [(i, o) for i, o in enumerate(env.orders) if o["status"] == "pending"][:5]
        self.text(canvas, "ORDERS  " + "  ".join(f"#{i + 1}:{o['recipe']}" for i, o in pending), 16, 35, GOLD, self.small)
        human_slot = getattr(env, "study_human_slot", 0)
        self.text(canvas, "RECIPE REFERENCE" + (" (P0: click or 1/2/3)" if human_slot == 0 else " (P1: 7/8/9)"), 16, 55, MUTED, self.small)
        self.recipe_rects = []
        for i, (name, foods) in enumerate(env.config["recipes"].items()):
            x = 16 + i * ((width - 32) // 3)
            rect = pygame.Rect(x, 74, (width - 32) // 3 - 8, 39)
            self.recipe_rects.append((name, rect))
            pygame.draw.rect(canvas, GOLD if env.selected[0] == name else (65, 62, 52), rect, 1)
            self.text(canvas, f"{name} [{i + 1}]", x + 7, 85, GOLD if env.selected[0] == name else INK, self.small)
            for j, food in enumerate(foods):
                fx = x + 54 + j * 31
                self.food(canvas, food, fx, 82, 20)
                if j < 2:
                    self.text(canvas, ">", fx + 22, 85, MUTED, self.small)
        controls = getattr(env, "control_modes", ["human", "manual"])
        # Two-model play has no assigned duties. Display current physical room,
        # not the historical initial-role label. Manual/human displays stay intact.
        names = ([env.room(p.position).upper() for p in env.state.players]
                 if controls in (['ppo','ppo'], ['model','model']) or getattr(env, "study_human_slot", None) is not None
                 else [r.upper() for r in env.roles])
        p0_label = (" AI Partner" if controls[0] in ("ppo", "model") and getattr(env, "experiment_mode", False)
                    else " PPO" if controls[0] == "ppo" else " AI" if controls[0] == "model" else "")
        self.text(canvas, f"P0 {names[0]} [{env.selected[0] or 'Auto'}]" + p0_label, 16, 119, (91, 168, 207), self.small)
        label = getattr(env, "partner_display_label", "AI" if controls[1] == "model" else controls[1].upper())
        self.text(canvas, f"P1 {names[1]} [{env.selected[1] or 'Auto'}] {label}", width // 2, 119, GREEN, self.small)
        for title, bounds in (("PREP ROOM", "prep_bounds"), ("KITCHEN", "kitchen_bounds")):
            left, _, right, _ = env.config["map"][bounds]
            center = bx + (left + right + 1) * self.tile / 2
            self.text(canvas, title, round(center - self.small.size(title)[0] / 2), 140, MUTED, self.small)
        canvas.blit(board, (bx, self.board_y))
        # Native X counters remain fully generic; marks express only a convention.
        for n, (cx, cy) in enumerate(env.shared[:9] if env.counter_guides else []):
            x, y = bx + cx * self.tile, self.board_y + cy * self.tile
            self.text(canvas, env.config["map"].get("shared_labels", [f"{j // 3 + 1}.{j % 3 + 1}" for j in range(len(env.shared))])[n], x + 2, y + 2, (196, 157, 82), self.tiny)
            if n % 3 == 2:
                pygame.draw.line(canvas, (117, 80, 21), (x + 3, y + 39), (x + 37, y + 39), 2)
        seconds = env.time / env.config["step_hz"] if animation_time is None else animation_time
        for i, (pos, pot) in enumerate(zip(env.pot_positions, env.pots)):
            x, y = bx + pos[0] * self.tile, self.board_y + pos[1] * self.tile
            recipe = env.orders[pot.order]["recipe"] if pot.order is not None else "-"
            # Keep annotations in the black gutter beside each pot so ordinary
            # counters above/below remain unobscured when players put items there.
            label_x = x + self.tile + 4
            self.text(canvas, f"P{i + 1} {recipe}", label_x, y - 5, INK, self.tiny)
            if pot.order is not None:
                foods = env.config["recipes"][recipe]
                for j, food in enumerate(foods):
                    fx = label_x + j * 19
                    self.food(canvas, food, fx, y + 10, 16)
                    if j < len(pot.feed_ticks):
                        pygame.draw.line(canvas, GREEN, (fx + 2, y + 26), (fx + 12, y + 26), 2)
                    elif j == len(pot.feed_ticks) and pot.phase != "burnt":
                        pygame.draw.rect(canvas, GOLD, (fx - 1, y + 9, 18, 18), 1)
            if pot.phase.startswith("wait"):
                remain = max(0, (pot.open_tick - env.time) / env.config["step_hz"])
                label, color = f"WAIT {len(pot.feed_ticks) + 1} {remain:.1f}", INK
                frac = 1 - remain / env.config["wait_seconds"]
            elif pot.phase.startswith("add"):
                remain = max(0, (pot.deadline - env.time) / env.config["step_hz"])
                label, color = f"ADD {len(pot.feed_ticks) + 1} {remain:.1f}", GOLD
                frac = remain / env.config["add_window_seconds"]
            elif pot.phase == "final":
                remain = max(0, (pot.ready_tick - env.time) / env.config["step_hz"])
                label, color = f"COOK {remain:.1f}", INK
                frac = 1 - remain / env.config["final_cook_seconds"]
            elif pot.phase == "ready":
                label, color, frac = "READY", GREEN, 1
                self.food(canvas, "dish", x - 21, y + 13, 18)
            elif pot.phase == "burnt":
                label, color, frac = "CLEAN", RED, 0
                # Keep the native pot silhouette, with charcoal inside the bowl.
                for dx, dy in ((10, 9), (18, 13), (26, 8)):
                    pygame.draw.rect(canvas, (39, 34, 30), (x + dx, y + dy, 7, 5))
            else:
                label, color, frac = "EMPTY", MUTED, 0
            if pot.phase != "empty":
                pygame.draw.rect(canvas, (55, 46, 29), (x + 2, y + 36, 36, 3))
                pygame.draw.rect(canvas, color, (x + 2, y + 36, round(36 * min(1, max(0, frac))), 3))
            self.text(canvas, label, label_x, y + 32, color, self.tiny)
            if pot.phase not in ("empty", "ready"):
                # Pygame's original renderer has static soup frames. Add only
                # small animated pixel steam/bubbles along its native sprite path.
                for k in range(2):
                    rise = int((seconds * 12 + k * 7) % 16)
                    dx = round(math.sin(seconds * 4 + k) * 2)
                    steam = (113, 104, 90) if pot.phase == "burnt" else (219, 208, 180)
                    pygame.draw.rect(canvas, steam, (x + 11 + k * 13 + dx, y + 7 - rise, 3, 3))
                if pot.phase != "burnt":
                    offset = int(seconds * 4) % 3
                    pygame.draw.rect(canvas, (229, 197, 100), (x + 12 + offset * 5, y + 12, 2, 2))
        if env.swap["status"]:
            state = env.swap["status"]
            text = f"Swap: {state}"
            if state in ("Requested", "Switching"):
                text += f"  P0 -> {env.swap['targets'][0].upper()} / P1 -> {env.swap['targets'][1].upper()}"
            self.text(canvas, text, 16, canvas.get_height() - 23, GOLD, self.small)
        feedback = " | ".join(f"P{i}: {f['text']}" for i, f in enumerate(env.feedback)
                              if f and f["until"] > env.time)
        if feedback:
            pygame.draw.rect(canvas, BG, (0, canvas.get_height() - 26, width, 26))
            self.text(canvas, feedback, 16, canvas.get_height() - 23, RED, self.small)
        if debug:
            self.text(canvas, f"DEBUG tick={env.time} issued={dict(env.dispensed)}", 16, canvas.get_height() - 23, MUTED, self.small)
        if help_open:
            self.help(canvas, env)
        return canvas

    def help(self, canvas, env):
        """
        @brief  在画布上绘制游戏帮助信息。
        @param  canvas 要绘制或附加面板的 Pygame 画布。
        @param  env 当前游戏环境对象。
        @retval None 修改目标画布。
        """
        canvas.fill(BG)
        controls = getattr(env, "control_modes", ["human", "manual"])
        lines = [f"HELP  [{getattr(env, 'help_key_label', 'H')}] close (game paused)", "",
            (f"P0 (blue): {'PPO' if controls[0] == 'ppo' else 'AI'} controls movement and interaction." if controls[0] in ("ppo", "model") else "P0 (blue): arrows / WASD. Interact: Space / Enter."),
            (f"P1 (green): {'PPO' if controls[1] == 'ppo' else 'AI'} controls movement and interaction." if controls[1] in ("ppo", "model") else
             "P1 (green, human): I J K L. Interact: U." if getattr(env, "study_human_slot", None) == 1 else
             "P1 (green, manual partner): I J K L. Interact: U."),
            ("P0 selects its own recipe; manual keys are disabled." if controls[0] in ("ppo", "model") else "P0 recipe: 1/2/3, 0 Auto, or click a reference card."),
            ("P1 selects its own recipe; manual keys are disabled." if controls[1] in ("ppo", "model") else "P1 recipe: 7/8/9, 6 Auto. Selection binds on first feed."),
            "", "Shared counter: put / pick up from either side.",
            "Counter marks suggest feed order; all slots are generic.",
            "[G] Toggle placement marks (visual hints only).",
            "All brown counters accept any item. Nothing is reserved.",
            "", f"After feeds 1 and 2: wait {env.config['wait_seconds']:g}s, then add within {env.config['add_window_seconds']:g}s.",
            "Both window endpoints are accepted; an expired pot burns.",
            "Wrong/early input keeps your item. Empty hands clean burns.",
            f"Feed 3 cooks for {env.config['final_cook_seconds']:g}s. Use a PLATE, then serve.",
            "", "[F] Request swap. Walk BOTH chefs into the opposite rooms.",
            "Swap is Ready when both reach their opposite room floors.",
            "[X] Cancel: roles stay; positions/items/timers stay as-is.",
            "Return to assigned rooms before requesting a new swap.",
            "No control/colour changes. Pots continue during switching.",
            "", "[P] Pause   [R] New round   [F12] Screenshot   [Esc] Exit",
            f"[F10] Debug (off by default). Orders repeat until {env.config['episode_seconds']:g}s.",
            ("AI-controlled chefs ignore manual move/recipe keys." if any(c in ("ppo", "model") for c in controls) else "Partner slot is manual; no trained model or autonomous AI.")]
        spacing = min(26, (canvas.get_height() - 40) // (len(lines) - 1))
        for n, text in enumerate(lines):
            if getattr(env, "experiment_mode", False):
                text = text.replace("PPO", "AI Partner")
                if "[F]" in text:
                    text = "F / X: disabled during experimental rounds."
                elif "[X]" in text:
                    text = "Both areas remain accessible; chef and network identities stay fixed."
                elif "[R]" in text:
                    text = "[R] End as restart request (saved)   [Esc] End round"
            self.text(canvas, text, 18, 15 + n * spacing, INK if n == 0 else MUTED, self.font if n == 0 else self.small)


def draw_robot_panel(canvas, service):
    """
    @brief  在游戏画面下方绘制机器人状态、行程与操作提示。
    @param  canvas 要绘制或附加面板的 Pygame 画布。
    @param  service 机器人服务及当前状态。
    @retval pygame.Surface 拼接后的游戏和机器人面板。
    """
    import pygame as pg
    with service.lock:
        status, phase, output = service.status, service.phase, service.last_output
        fault, level = service.fault, service.level
        completed = service.cycle.completed if service.cycle else 0
        hold_remaining = service.cycle.hold_remaining if service.cycle else 0.
    panel = pg.Surface((canvas.get_width(), canvas.get_height() + 145))
    panel.fill((15, 16, 18))
    panel.blit(canvas, (0, 0))
    y = canvas.get_height() + 6
    font = pg.font.SysFont("consolas", 16)
    small = pg.font.SysFont("consolas", 13)
    color = (113, 200, 155) if status == "ON" else (235, 180, 105)
    percent = min(100., max(0., output.progress_percent)) if output else None
    phase_label = "AT END" if phase == "endpoint_hold" else phase.upper()
    title = f"UR5 {status} | {phase_label} | travel {percent:.0f}%" if percent is not None else f"UR5 {status} | travel N/A"
    panel.blit(font.render(title, True, color), (16, y))
    bar = pg.Rect(16, y + 25, panel.get_width() - 32, 16)
    pg.draw.rect(panel, (48, 54, 58), bar)
    if percent is not None:
        pg.draw.rect(panel, color, (bar.x, bar.y, round(bar.width * percent / 100), bar.height))
    hint = {"CHECKING": "Release handle: read-only checks. Game timer has not started.",
            "READY": "F6 starts robot and game. F7 stops. No held key.",
            "ARMING": "Connecting control... F7 cancels; game timer waits.",
            "SYNCING": "Synchronizing game input... no motion; F7 cancels.",
            "DISABLED": "No robot connection or simulated load.",
            "STOPPED": "Output stopped. Exit and restart; no automatic re-enable.",
            "FAULT": "Fault: see terminal/log. Exit after resolving it."}.get(status)
    if hint is None:
        hint = {"push": "PUSH to the end -> brief hold -> manual RETURN",
                "endpoint_hold": f"AT END: stop / hold {hold_remaining:.1f}s, then pull back",
                "return": "Pull back to 0% -> automatic next PUSH",
                "home": "Homing to saved start -> next PUSH"}[phase]
    panel.blit(small.render(hint, True, color), (16, y + 47))
    if service.limits:
        actual_b = output.damping_n_s_m if output else service.limits.damping_n_s_m[level]
        detail = f"Level {level + 1}/3 | B={actual_b:g} N*s/m | cycles {completed} | {service.limits.travel_m*100:g} cm"
        panel.blit(small.render(detail, True, (225, 225, 215)), (16, y + 69))
    panel.blit(small.render("F6 start | F7 STOP | H home | F1 help | Esc exit", True, (190, 190, 180)), (16, y + 91))
    if fault:
        panel.blit(small.render(fault[:max(1, (panel.get_width()-32)//8)], True, (225, 104, 81)), (16, y + 113))
    return panel
