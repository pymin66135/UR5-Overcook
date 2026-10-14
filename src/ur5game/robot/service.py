"""游戏与机器人之间的服务：一个线程独占机器人通信，主线程只发布请求。"""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

from .session import RobotSession
from .admittance import dot, norm
from .cadence import ControlCadence
from .homing import load_start
from ..interaction.load_mapping import LoadMapper
from ..interaction.stroke_cycle import StrokeCycle

class RobotService:
    """Explicit F6 start, F7 stop; no held keyboard key. Stops remain latched."""
    def __init__(self, app_config, output, *, enable=False, session_factory=RobotSession):
        """
        @brief  组装共享控制参数、负载映射和后台控制状态。
        @param  app_config ApplicationConfig 分组配置对象。
        @param  output 数据输出路径；None 时使用配置中的默认位置。
        @param  enable 是否明确请求实机控制；False 不允许发送运动指令。
        @param  session_factory 机器人会话构造工厂；测试可注入兼容的假会话。
        @retval None 初始化服务；不建立硬件连接。
        """
        self.app_config = app_config
        self.config_path = app_config.source
        self.config, self.limits = app_config.robot_settings, app_config.limits
        self.output = Path(output)
        self.live_allowed = bool(enable)
        self.session_factory = session_factory
        self.policy = self.limits.safety_policy
        self.fixed_origin = load_start(self.config_path, self.config) if enable else None
        if enable and (self.fixed_origin is None or not self.limits.planar_enabled):
            raise ValueError("Realtime requires XY configuration and saved fixed start")
        self.cycle = StrokeCycle(self.limits.travel_m,
                                 hold_seconds=self.config["game_endpoint_hold_s"],
                                 policy=self.policy) if enable else None
        self.mapper = LoadMapper(app_config.interaction)
        self.lock = threading.RLock()
        self.shutdown = threading.Event()
        self.activation = threading.Event()
        self.ui_synced = threading.Event()
        self.worker = self.session = None
        self.transport_log = self.trace_path = None
        self.status = "CHECKING" if enable else "DISABLED"
        self.last_input = self.last_publish = self.last_output = None
        self.focused = self.run_authorized = False
        self.stop_reason = self.fault = None
        self.home_requested = self.closed = False
        self.ever_armed = self.connected_once = False
        self.level = self.window = 0
        self.q = 0.
        self.damping = self.limits.damping_n_s_m[0]
        self.stiffness = self.limits.stiffness_levels_n_m[0]
        self.phase = "push"
        self.data_source = "INTERACTIVE_EXPERIENCE_NOT_FORMAL_STUDY"

    def __enter__(self):
        """
        @brief  进入机器人服务的上下文管理范围。
        @param  None 无显式输入参数。
        @retval RobotService 当前服务对象。
        """
        return self

    def __exit__(self, *exc):
        """
        @brief  离开上下文时停止服务并释放记录与连接。
        @param  exc 上下文退出时传入的异常类型、异常实例及追踪信息。
        @retval None 不吞掉外层异常。
        """
        self.close()

    def _event(self, kind, **data):
        """
        @brief  保存带时间、档位和数据来源的联动事件。
        @param  kind 要记录的事件类型字符串。
        @param  data 要追加到事件记录中的扩展字段。
        @retval None 无记录流时跳过。
        """
        if self.transport_log:
            self.transport_log.write(json.dumps({"utc": datetime.now(timezone.utc).isoformat(),
                "monotonic": time.monotonic(), "event": kind, "window": self.window,
                "q_weighted_cells": self.q, "level": self.level, "damping_n_s_m": self.damping,
                "stiffness_n_m": self.stiffness,
                "hardware_enabled": bool(self.session and self.session.armed),
                "data_source": self.data_source, **data},
                ensure_ascii=False, allow_nan=False) + "\n")
            self.transport_log.flush()

    def _make_session(self):
        """
        @brief  通过会话工厂构造机器人会话。
        @param  None 无显式输入参数。
        @retval RobotSession 或用于离线测试的兼容会话。
        """
        return self.session_factory(self.config, self.limits, enable=True,
                                    log_path=None, fixed_origin=self.fixed_origin)

    def begin_episode(self, env, runtime):
        # A bridge is one episode / one activation; no implicit restart/rearming.
        """
        @brief  初始化本轮负载记录并按使能状态启动只读准备线程。
        @param  env 当前游戏环境对象。
        @param  runtime AI 推理运行对象及本轮元信息。
        @retval None 初始化记录和可选控制线程。
        @note   每个服务对象只用于一局；禁止自动重新激活。
        """
        if self.worker is not None or self.closed:
            raise RuntimeError("Restart the realtime launcher for another episode")
        self.mapper.reset(env)
        self.data_source = runtime.base_metadata.get("data_source", self.data_source)
        self.last_publish = time.monotonic()
        self.output.mkdir(parents=True, exist_ok=True)
        episode_id = runtime.base_metadata["episode_id"]
        self.transport_log = (self.output / f"{episode_id}_transport.jsonl").open("x", encoding="utf-8")
        self.trace_path = self.output / f"{episode_id}_ur5"
        self.session = self._make_session() if self.live_allowed else None
        self._event("episode_start")
        runtime.base_metadata.update(
            ur5_load_source="UR5_RTDE_HEX_REAL" if self.live_allowed else "UR5_DISABLED")
        runtime.base_metadata.update(
            ur5_cycle="position_completed_endpoint_hold_manual_return_v2",
            ur5_activation="explicit_f6_latched_no_shift_v1",
            ur5_endpoint_hold_s=self.cycle.hold_seconds if self.cycle else None,
            ur5_tail_status="not_applicable_interactive_experience",
            data_source=self.data_source,
            ur5_hardware_config=self.config if self.live_allowed else None,
            ur5_fixed_origin=self.fixed_origin,
            ur5_fixed_start_file=str(self.app_config.home_file) if self.live_allowed else None)
        if self.live_allowed:
            # Store exactly the configuration/pose used, not merely a mutable path.
            self._event("experience_configuration", config=self.config, fixed_origin=self.fixed_origin)
            self.session.log_path = self.trace_path.with_name(self.trace_path.name + "_01.jsonl")
            self.worker = threading.Thread(target=self._worker, name="UR5Game", daemon=True)
            self.worker.start()

    @property
    def blocks_game(self):
        """
        @brief  判断机器人当前状态是否要求暂停游戏推进。
        @param  None 无显式输入参数。
        @retval bool True 表示实机未进入 ON 或已停止。
        """
        with self.lock:
            return self.live_allowed and (self.status != "ON" or self.shutdown.is_set())

    def poll_inputs(self, *, focused):
        """
        @brief  更新界面心跳和焦点，必要时锁定失焦停止。
        @param  focused 游戏窗口当前是否有焦点。
        @retval None 更新输入状态。
        """
        with self.lock:
            self.last_input = time.monotonic()
            self.focused = bool(focused)
            if self.status in ("ARMING", "SYNCING", "ON") and not self.focused:
                self.stop("input_focus_lost")

    def finish_input_frame(self, *, focused):
        """
        @brief  全部界面事件处理后，确认新的输入帧和启动同步。
        @param  focused 游戏窗口当前是否有焦点。
        @retval None 满足同步条件时设置确认事件。
        @note   必须先处理排队的退出、停止和失焦事件。
        """
        with self.lock:
            self.poll_inputs(focused=focused)
            if self.status == "SYNCING" and not self.shutdown.is_set() and self.run_authorized and self.focused:
                self.ui_synced.set()

    def enable(self):
        """
        @brief  在 READY 和新鲜界面状态下登记 F6 启动授权。
        @param  None 无显式输入参数。
        @retval None 状态转 ARMING，后台线程开始激活。
        """
        with self.lock:
            if self.status != "READY" or self.shutdown.is_set():
                raise RuntimeError(f"UR5 {self.status}: wait for READY; restart after stop/fault")
            if (not self.focused or self.last_input is None
                    or time.monotonic() - self.last_input > self.policy["game_input_timeout_s"]):
                raise RuntimeError("Press F6 in the focused game window after READY")
            self.run_authorized = True
            self.status = "ARMING"
            self.activation.set()

    def request_home(self):
        """
        @brief  登记 H 主动归位请求。
        @param  None 无显式输入参数。
        @retval None 下一控制周期进入 home。
        @note   需要输出已开启、窗口有焦点且未停止。
        """
        with self.lock:
            if self.status != "ON" or not self.run_authorized or not self.focused or self.shutdown.is_set():
                raise RuntimeError("Home requires active output and a focused game window")
            if self.phase != "home":
                self.home_requested = True

    def publish_env(self, env):
        """
        @brief  发布游戏状态并更新下一窗口的负载档位。
        @param  env 当前游戏环境对象。
        @retval None 更新档位、窗口记录和状态心跳。
        """
        with self.lock:
            try:
                change = self.mapper.feed(env)
            except ValueError as exc:
                self.fault = str(exc)
                self.stop(self.fault)
                return
            if change:
                self.q = change.weighted_cells
                self.level = change.level
                self.damping = self.limits.damping_n_s_m[self.level]
                self.stiffness = self.limits.stiffness_levels_n_m[self.level]
                self._event("window_closed_next_load", source_window=change.source_window,
                            applied_window=change.applied_window, source_q=change.weighted_cells)
                self.window = change.applied_window
            self.q = self.mapper.quantity
            self.last_publish = time.monotonic()

    def _guard(self, *, check_freshness=True):
        """
        @brief  检查停止锁定、授权、焦点和状态新鲜度。
        @param  check_freshness 是否同时检查心跳时间；False 仅用于连接后的同步等待。
        @retval None 条件满足；不满足时抛出明确原因的 RuntimeError。
        @note   关闭新鲜度检查只用于启动同步等待，不可作为速度发送前的检查。
        """
        if self.shutdown.is_set():
            raise RuntimeError(self.stop_reason or "stopped")
        with self.lock:
            if not self.run_authorized:
                raise RuntimeError("game_start_not_authorized: press F6 after READY")
            if not self.focused:
                raise RuntimeError("game_window_not_focused")
            if not check_freshness:
                return  # Setup handshake only; NEVER used as a command guard.
            if self.last_input is None:
                raise RuntimeError("game_input_missing: no UI heartbeat")
            age = time.monotonic() - self.last_input
            limit = self.policy["game_input_timeout_s"]
            if age < 0 or age > limit:
                raise RuntimeError(f"game_input_stale: age={age:.3f}s, limit={limit:g}s, stage={self.status}")
            if self.status == "ON":
                if self.last_publish is None:
                    raise RuntimeError("game_state_missing")
                age = time.monotonic() - self.last_publish
                limit = self.policy["game_state_timeout_s"]
                if age < 0 or age > limit:
                    raise RuntimeError(f"game_state_stale: age={age:.3f}s, limit={limit:g}s")

    def _sync_after_control_setup(self):
        # A blocking native control constructor may stall the UI/GIL. Do not
        # count that setup interval as running-game staleness. No command is
        # sent until the UI drains pending events and acknowledges a NEW frame.
        """
        @brief  控制接口创建后等待一帧处理完停止事件的新界面确认。
        @param  None 无显式输入参数。
        @retval None 界面确认和授权检查完成。
        @note   此等待阶段不发送速度；超过配置时限则取消激活。
        """
        with self.lock:
            self.ever_armed = True  # Control interface opened, not motion permission.
            self._guard(check_freshness=False)
            self.ui_synced.clear()
            self.status = "SYNCING"
        timeout = self.policy["startup_ui_sync_timeout_s"]
        deadline = time.monotonic() + timeout
        while not self.ui_synced.wait(.01):
            self._guard(check_freshness=False)
            if time.monotonic() >= deadline:
                raise RuntimeError(f"startup_ui_sync_timeout: no post-setup UI frame in {timeout:g}s")
        self._guard()

    def _cancel_preflight(self):
        """
        @brief  检查只读准备过程中是否收到取消请求。
        @param  None 无显式输入参数。
        @retval None 未取消；取消时抛出 RuntimeError。
        """
        if self.shutdown.is_set():
            raise RuntimeError(self.stop_reason or "preflight_cancelled")

    def _worker(self):
        """
        @brief  独占机器人通信并执行准备、激活、采样控制及关闭。
        @param  None 无显式输入参数。
        @retval None 控制线程结束并记录停止原因。
        @note   异常进入 FAULT；资源停止和清理由本线程负责。
        """
        try:
            self._cancel_preflight()
            self.session.connect()
            self.connected_once = True
            self.session.prepare(check_cancelled=self._cancel_preflight)
            with self.lock:
                self._cancel_preflight()
                self.status = "READY"
            while not self.activation.wait(.02):
                self._cancel_preflight()
            self._guard()
            self.session.activate_prepared(after_control_setup=self._sync_after_control_setup)
            self.ever_armed = True
            self._guard()  # stop/focus loss/quit while controller setup was blocked
            with self.lock:
                self._guard()
                position = dot(tuple(a - b for a, b in zip(self.session.last_sample.pose[:3],
                                                          self.fixed_origin[:3])), self.limits.axis_base)
                self.cycle.start(position)
                self.phase = self.cycle.phase
                self.status = "ON"
                self.last_publish = time.monotonic()
                self._event("explicit_game_enable")
            cadence = ControlCadence(self.limits.control_hz)
            while not self.shutdown.wait(cadence.delay()):
                self._guard()
                with self.lock:
                    if self.home_requested:
                        self.home_requested = False
                        self.cycle.home()
                        self.phase = "home"
                        self._event("home_requested")
                    level, phase = self.level, self.phase
                    override = None if self.limits.has_profile_levels else self.damping
                cadence.begin()
                # Never hold the UI lock over sample(), speedL() or robot stop.
                # WAIT uses the exact same guarded zero-velocity behavior as
                # standalone W: no virtual impact or separate lateral tuning.
                control_phase = "wait" if phase == "endpoint_hold" else phase
                out = self.session.tick(level, control_phase, override, command_guard=self._guard)
                with self.lock:
                    self.last_output = out
                    transition = self.cycle.update(out.position_m,
                        home_done=bool(self.session.home_motion and self.session.home_motion.done),
                        timing_hold=out.timing_hold,
                        timestamp=self.session.last_sample.timestamp,
                        speed_m_s=norm(self.session.last_sample.speed[:3]))
                    self.phase = self.cycle.phase
                    if transition:
                        self._event("stroke_transition", previous_phase=transition[0],
                                    next_phase=transition[1], position_m=out.position_m,
                                    completed_strokes=self.cycle.completed)
        except Exception as exc:
            with self.lock:
                if not self.shutdown.is_set():
                    self.fault = f"{type(exc).__name__}: {exc}"
                    self.status = "FAULT"
                    self.shutdown.set()
        finally:
            with self.lock:
                self.run_authorized = False
            # Device ownership stays in this thread, including stop and cleanup.
            try:
                self.session.close()
            except Exception as exc:
                with self.lock:
                    self.fault = self.fault or f"close_failed: {exc}"
                    self.status = "FAULT"
            with self.lock:
                self._event("hardware_stopped", reason=self.fault or self.stop_reason,
                            completed_strokes=self.cycle.completed)
            if self.fault:
                print(f"UR5 FAULT: {self.fault}", flush=True)

    def stop(self, reason):
        """
        @brief  撤销授权并锁定停止请求。
        @param  reason 停止、故障或日志收尾原因字符串。
        @retval None 更新停止状态；设备关闭由控制线程执行。
        """
        with self.lock:
            self.run_authorized = False
            if self.shutdown.is_set():
                return
            self.stop_reason = reason
            self.shutdown.set()
            if self.status != "FAULT":
                self.status = "STOPPED" if self.live_allowed else "DISABLED"

    def close(self):
        """
        @brief  请求停止，等待控制线程结束并关闭联动记录。
        @param  None 无显式输入参数。
        @retval None 资源关闭完成；线程未退出时抛出 RuntimeError。
        """
        if self.closed:
            return
        self.stop("bridge_closed")
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=5.)
            if self.worker.is_alive():
                # Do not race another thread's socket/trace cleanup.
                raise RuntimeError("UR5 worker did not stop; verify the controller is stopped on site")
        with self.lock:
            if self.transport_log:
                self.transport_log.close()
                self.transport_log = None
            self.closed = True

    def decorate(self, canvas):
        """
        @brief  调用渲染模块把机器人状态面板附加到游戏画布。
        @param  canvas 要绘制或附加面板的 Pygame 画布。
        @retval pygame.Surface 附带机器人面板的画布。
        """
        from ..game.renderer import draw_robot_panel
        return draw_robot_panel(canvas, self)
