"""游戏主循环：键盘、画面、AI 推理、机器人请求与数据记录。"""
from datetime import datetime
import json
import os
from pathlib import Path

from .environment import ForcedCoopEnv, load_config
from .controls import Session, ManualInput
from ..records import EpisodeLog


class GameApp:
    def __init__(self, config, partner, robot, output, *, human_role=None, screenshot=None):
        """
        @brief  组装配置、AI、机器人服务和输出位置。
        @param  config 当前模块使用的配置字典。
        @param  partner 已加载的 AI 伙伴。
        @param  robot 机器人服务对象。
        @param  output 数据输出路径；None 时使用配置中的默认位置。
        @param  human_role 玩家初始角色 prep/kitchen；None 时采用配置。
        @param  screenshot 可选预览图片保存路径；None 表示正常显示界面。
        @retval None 完成游戏应用初始化。
        """
        self.config, self.partner, self.robot = config, partner, robot
        self.output = Path(output)
        self.human_role = human_role or config.game["human_role"]
        self.screenshot = Path(screenshot) if screenshot else None

    def _robot_key(self, event, session):
        """
        @brief  处理 F6/F7/H 和窗口失焦对应的机器人请求。
        @param  event 界面事件，或环境要记录的事件类型。
        @param  session 当前游戏键盘输入会话。
        @retval bool True 表示按键已处理；False 表示交由普通游戏输入处理。
        @note   返回 True 表示该按键已被机器人服务处理。
        """
        import pygame as pg
        if event.type == pg.KEYDOWN and event.key in (pg.K_F6, pg.K_F7, pg.K_h):
            if event.key == pg.K_F7:
                self.robot.stop("operator_stop")
            elif not (session.paused or session.help_open or session.env.done):
                if event.key == pg.K_F6 and getattr(event, "repeat", False):
                    return True
                try:
                    self.robot.enable() if event.key == pg.K_F6 else self.robot.request_home()
                except RuntimeError as exc:
                    print(f"UR5: {exc}", flush=True)
            return True
        if event.type == pg.WINDOWFOCUSLOST or (event.type == pg.ACTIVEEVENT and not event.gain):
            self.robot.stop("input_focus_lost")
        return False

    def run(self):
        """
        @brief  运行游戏界面、逻辑、机器人请求与记录主循环。
        @param  None 无显式输入参数。
        @retval dict 包含结果摘要和日志文件前缀。
        @note   退出时先关闭机器人，再收尾日志和界面。
        """
        if self.screenshot:
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        import pygame as pg
        from .renderer import Renderer

        env = ForcedCoopEnv(load_config(self.config.scene), self.human_role)
        self.partner.configure_env(env)
        self.robot.begin_episode(env, self.partner)
        log = EpisodeLog(env, self.output, self.partner.log_mode, self.partner.metadata())
        exit_reason = "window_closed"
        running = True
        try:
            pg.init()
            pg.display.set_caption("Overcooked | AI Partner")
            renderer = Renderer()
            session = Session(env, runtime=self.partner)
            canvas = self.robot.decorate(renderer.draw(env))
            display = pg.display.Info()
            scale = min(1., (display.current_w - 60) / canvas.get_width(),
                        (display.current_h - 100) / canvas.get_height())
            window = pg.display.set_mode((max(480, int(canvas.get_width()*scale)),
                                          max(480, int(canvas.get_height()*scale))), pg.RESIZABLE)
            clock = pg.time.Clock()
            while running:
                # 连接完成当帧也保持暂停，避免将连接阻塞时间计入游戏。
                was_waiting = self.robot.blocks_game
                elapsed = clock.tick(self.config.game["fps"]) / 1000
                pg.event.pump()
                self.robot.poll_inputs(focused=pg.key.get_focused())
                scale = min(window.get_width()/canvas.get_width(), window.get_height()/canvas.get_height())
                for event in pg.event.get():
                    if self._robot_key(event, session):
                        continue
                    if event.type == pg.KEYDOWN and event.key == pg.K_F1:
                        event = pg.event.Event(pg.KEYDOWN, key=pg.K_h)
                    pointer = tuple(v/scale for v in event.pos) if event.type == pg.MOUSEBUTTONDOWN else None
                    result = session.handle(event, renderer.recipe_rects, pointer)
                    if result in ("quit", "restart_requested", "reset"):
                        exit_reason = "restart_requested" if result != "quit" else "window_closed"
                        self.robot.stop("window_exit" if result == "quit" else "game_restart")
                        running = False
                    if session.paused or session.help_open:
                        self.robot.stop("game_paused")
                    if result == "screenshot":
                        self.output.mkdir(parents=True, exist_ok=True)
                        pg.image.save(canvas, str(self.output / f"capture_{datetime.now():%Y%m%d_%H%M%S}.png"))

                # 必须先处理全部排队停止事件，再向控制线程确认界面恢复。
                self.robot.finish_input_frame(focused=pg.key.get_focused())
                waiting = self.robot.blocks_game
                paused = session.paused or session.help_open or waiting or was_waiting
                if waiting:
                    session.input = ManualInput()
                steps = session.clock.advance(elapsed, paused=paused or env.done or bool(self.screenshot))
                for _ in range(steps if running else 0):
                    if env.done:
                        break
                    session.tick(pg.key.get_pressed())
                    self.robot.publish_env(env)
                log.flush(env)
                if env.done:
                    self.robot.stop("episode_complete")
                    exit_reason, running = "episode_complete", False
                animation = env.time / env.config["step_hz"] + (0 if paused or env.done else session.clock.accumulator)
                canvas = self.robot.decorate(renderer.draw(env, session.help_open, session.paused,
                                                            session.debug, animation))
                scale = min(window.get_width()/canvas.get_width(), window.get_height()/canvas.get_height())
                window.fill((15, 16, 18))
                window.blit(pg.transform.scale(canvas, (int(canvas.get_width()*scale),
                                                        int(canvas.get_height()*scale))), (0, 0))
                pg.display.flip()
                if self.screenshot:
                    self.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    pg.image.save(canvas, str(self.screenshot))
                    exit_reason, running = "ui_preview", False
        except BaseException:
            self.robot.stop("game_exception")
            exit_reason = "error"
            raise
        finally:
            # 停止硬件完成后，才收尾日志或弹出问卷。
            try:
                self.robot.close()
            finally:
                log.close(env, "episode_complete" if env.done else exit_reason)
                pg.quit()
        return {"summary": json.loads(log.base.with_suffix(".summary.json").read_text(encoding="utf-8")),
                "log_base": str(log.base)}
