"""Three-level real UR5 admittance test with a live travel-percentage bar.

No --enable = RTDE monitoring only. With --enable, startup checks are read-only;
after READY a fresh SPACE press enables control. Release/S/Esc/focus loss stop.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import textwrap
import time
from uuid import uuid4

from ..robot.cadence import ControlCadence
from ..robot.admittance import dot
from ..robot.session import RobotSession
from ..config import load_experiment, load_robot_config, DEFAULT_CONFIG, PROJECT_ROOT
from ..robot.homing import capture_start, load_start

TEMPLATE = DEFAULT_CONFIG


def automatic_log_path(output_dir=None):
    """
    @brief  生成包含时间和随机编号的新三档轨迹路径。
    @param  output_dir 输出根目录；None 时使用工程默认目录。
    @retval Path 独立体验日志路径。
    """
    return Path(output_dir or PROJECT_ROOT / "results") / ("ur5_three_levels_" +
        datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid4().hex[:8] + ".jsonl")


def run(config_path, *, enable=False, offline_ui=False, screenshot=None, output=None):
    """
    @brief  运行三档体验窗口、实机状态监视或完全离线预览。
    @param  config_path 实验配置文件路径。
    @param  enable 是否明确请求实机控制；False 不允许发送运动指令。
    @param  offline_ui 是否只显示独立窗口预览；True 不连接机器人。
    @param  screenshot 可选预览图片保存路径；None 表示正常显示界面。
    @param  output 数据输出路径；None 时使用配置中的默认位置。
    @retval None 窗口退出并关闭会话。
    @note   实机准备后需重新按住 Space 才激活；离线预览不连接设备。
    """
    if offline_ui and enable:
        raise ValueError("Offline UI preview cannot be enabled for motion")
    import pygame as pg

    session = None
    pg.init()
    screen = pg.display.set_mode((980, 620), pg.RESIZABLE)
    pg.display.set_caption("UR5 | three-level admittance calibration")
    font = pg.font.SysFont("consolas", 22)
    small = pg.font.SysFont("consolas", 16)
    clock = pg.time.Clock()
    level, phase = 0, "push"
    notice = ("OFFLINE UI PREVIEW - NO ROBOT" if offline_ui else
              "CHECKING: release the handle and SPACE. Startup checks send no motion.")
    pending_start = not offline_ui
    ready = False
    space_released = False
    notice_scroll = 0
    cadence = None
    last_monitor = time.monotonic()
    progress = None
    raw_axis_force = None
    lateral_position = None
    monitor_origin = None
    fixed_origin = None

    def label(value, x, y, color=(230, 229, 220), face=font):
        """
        @brief  在独立体验窗口绘制一条文字提示。
        @param  value 要检查、编码或绘制的值，具体含义由当前函数决定。
        @param  x 文字或图标的画布横坐标，单位像素。
        @param  y 文字或图标的画布纵坐标，单位像素。
        @param  color RGB 文字颜色。
        @param  face 独立窗口文字所用字体对象。
        @retval None 修改窗口画布。
        """
        screen.blit(face.render(value, True, color), (x, y))

    def set_notice(value, *, terminal=True):
        """
        @brief  更新可滚动状态提示并按需打印到终端。
        @param  value 要检查、编码或绘制的值，具体含义由当前函数决定。
        @param  terminal 是否同步把提示打印到终端。
        @retval None 重置提示滚动位置。
        """
        nonlocal notice, notice_scroll
        notice, notice_scroll = value, 0
        if terminal:
            print(value, flush=True)

    def cancel_preflight():
        """
        @brief  检查准备过程中排队的退出、失焦和停止事件。
        @param  None 无显式输入参数。
        @retval None 未取消；收到取消事件则抛出 RuntimeError。
        """
        for event in pg.event.get():
            if (event.type in (pg.QUIT, pg.WINDOWFOCUSLOST)
                    or event.type == pg.KEYDOWN and event.key in (pg.K_ESCAPE, pg.K_s)):
                raise RuntimeError("Startup check cancelled; restart the program")

    def stop(reason):
        """
        @brief  停止独立会话并显示需重启的停止提示。
        @param  reason 停止、故障或日志收尾原因字符串。
        @retval None 清除就绪标记并停止设备。
        """
        nonlocal ready
        ready = False
        if session:
            session.stop(reason)
        set_notice(f"STOPPED: {reason}. Exit and restart for another attempt.")

    try:
        running = True
        while running:
            for event in pg.event.get():
                if event.type == pg.QUIT or event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE:
                    if session:
                        stop("operator_exit")
                    running = False
                elif event.type == pg.WINDOWFOCUSLOST:
                    if session and (session.armed or ready):
                        stop("window_focus_lost")
                elif event.type == pg.KEYUP and event.key == pg.K_SPACE:
                    space_released = True
                    if session and session.armed:
                        stop("SPACE released")
                elif event.type == pg.MOUSEWHEEL:
                    notice_scroll = max(0, notice_scroll - event.y)
                elif event.type == pg.KEYDOWN:
                    if event.key in (pg.K_1, pg.K_2, pg.K_3):
                        level = event.key - pg.K_1
                    elif event.key == pg.K_p:
                        phase = "push"
                    elif event.key == pg.K_r:
                        phase = "return"
                    elif event.key == pg.K_w:
                        phase = "wait"
                    elif event.key == pg.K_h:
                        if fixed_origin is None:
                            set_notice("HOME UNAVAILABLE: first save a fixed start with --save-start.",
                                       terminal=not bool(session and session.armed))
                        else:
                            phase = "home"
                    elif event.key == pg.K_s and session:
                        stop("operator_stop")
                    elif event.key == pg.K_PAGEDOWN:
                        notice_scroll += 1
                    elif event.key == pg.K_PAGEUP:
                        notice_scroll = max(0, notice_scroll - 1)
                    elif (event.key == pg.K_SPACE and session and ready
                          and space_released and pg.key.get_focused()):
                        ready = False
                        space_released = False
                        try:
                            session.activate_prepared()
                            # A release during blocking connection setup must not
                            # be missed, even if SPACE has since been pressed again.
                            cancelled = False
                            for queued in pg.event.get():
                                if queued.type == pg.QUIT:
                                    running = False
                                    cancelled = True
                                elif (queued.type == pg.WINDOWFOCUSLOST
                                      or queued.type == pg.KEYUP and queued.key == pg.K_SPACE
                                      or queued.type == pg.KEYDOWN and queued.key in (pg.K_ESCAPE, pg.K_s)):
                                    cancelled = True
                            if cancelled or not pg.key.get_focused() or not pg.key.get_pressed()[pg.K_SPACE]:
                                stop("activation cancelled")
                            else:
                                cadence = ControlCadence(session.limits.control_hz)
                                # Terminal output can block; do not delay the first
                                # live feedback sample for an informational print.
                                set_notice("OUTPUT ON: hold SPACE; release it to stop. Start with a gentle push.",
                                           terminal=False)
                        except Exception as exc:
                            session.stop("activation_failed")
                            set_notice(f"ARM REFUSED: {exc}\nExit and restart after resolving this error.")
            if not running:
                break
            if ready and not pg.key.get_pressed()[pg.K_SPACE]:
                space_released = True
            if session and session.armed:
                if not pg.key.get_pressed()[pg.K_SPACE]:
                    stop("SPACE released")
                elif not pg.key.get_focused():
                    stop("window_focus_lost")
                elif cadence.due():
                    try:
                        cadence.begin()
                        out = session.tick(level, phase)
                        progress = out.progress_percent
                        if session.limits.planar_enabled:
                            lateral_position = out.lateral_position_m
                        if (phase == "home" and session.home_motion.done):
                            phase = "wait"
                            set_notice("HOME REACHED (XY within tolerance): W/WAIT. Press P to push or R for manual return.",
                                       terminal=False)
                    except Exception as exc:
                        set_notice(f"FAULT: {exc}\nExit and restart after resolving this error.")
            elif session and session.connected and time.monotonic() - last_monitor >= .1:
                try:
                    measured = session.device.sample()
                    x = dot(tuple(measured.pose[i] - monitor_origin[i] for i in range(3)),
                            session.limits.axis_base)
                    progress = 100 * max(0., min(1., x / session.limits.travel_m))
                    raw_axis_force = dot(measured.wrench[:3], session.limits.axis_base)
                    if session.limits.planar_enabled:
                        lateral_position = measured.pose[1] - monitor_origin[1]
                except Exception as exc:
                    ready = False
                    session.stop("readonly_feedback_failed")
                    set_notice(f"RTDE READ FAILED: {exc}\nExit and restart after resolving this error.")
                last_monitor = time.monotonic()
            screen.fill((16, 19, 22))
            planar = bool(session and session.limits.planar_enabled)
            label("UR5 XY LOAD TEST - Z FIXED" if planar else "UR5 ONE-AXIS LOAD TEST",
                  30, 24, (228, 198, 109))
            source_label = ("REAL RTDE + HEX" if session and session.config["force_source"] == "hex_udp"
                            else "REAL RTDE" if session else
                            "OFFLINE UI ONLY" if offline_ui else "NOT CONNECTED - OUTPUT OFF")
            label(source_label, 30, 68,
                  (110, 205, 145) if session else (236, 170, 88))
            label(f"Level {level+1}/3   Phase: {phase.upper()}   Output: {'ON' if session and session.armed else 'OFF'}",
                  30, 110)
            if session and session.armed and getattr(session.last_output, "timing_hold", False):
                label("TIMING HOLD: ZERO SPEED", 620, 110, (236, 170, 88), small)
            elif session and session.armed and getattr(session.last_output, "y_centering", False):
                label("Y RECENTERING <= 1 cm/s" if session.limits.planar_y_center_speed_m_s == .01 else
                      f"Y RECENTERING <= {session.limits.planar_y_center_speed_m_s * 100:g} cm/s",
                      620, 110, (110, 205, 145), small)
            if session:
                b = session.limits.damping_n_s_m[level if phase == "push" else 0]
                if session.limits.has_profile_levels:
                    m = session.limits.mass_levels_kg[level if phase == "push" else 0]
                    k = session.limits.stiffness_levels_n_m[level] if phase == "push" else 0.0
                    label(f"Selected M={m:g} kg  B={b:g} N*s/m  K={k:g} N/m", 30, 151)
                else:
                    label(f"Selected B = {b:.1f} N*s/m", 30, 151)
                if session.armed and session.last_output:
                    out = session.last_output
                    if planar:
                        label(f"Force push={out.interaction_force_n:+.1f} N  Y={out.lateral_force_n:+.1f} N"
                              f"  XY={out.interaction_force_xy_n:.1f} N", 30, 185)
                    else:
                        label(f"Actual interaction force = {out.interaction_force_n:+.1f} N", 30, 185)
                elif raw_axis_force is not None:
                    source = "HEX" if session.config["force_source"] == "hex_udp" else "RTDE"
                    label(f"RAW {source} axis force (not zeroed) = {raw_axis_force:+.1f} N", 30, 185)
            else:
                label("Measured force: N/A (no robot)", 30, 185)
            label(f"PUSH TRAVEL: {progress:.1f}%" if progress is not None else "PUSH TRAVEL: N/A", 30, 230)
            pg.draw.rect(screen, (49, 54, 59), (30, 270, 790, 40))
            if progress is not None:
                pg.draw.rect(screen, (112, 205, 152), (30, 270, round(790 * progress / 100), 40))
            if planar and lateral_position is not None:
                label(f"Y: {lateral_position * 100:+.1f} cm / +/-{session.limits.planar_y_travel_m * 100:.0f} cm"
                      f"   X range: {session.limits.travel_m * 100:.0f} cm"
                      f"   XY speed <= {session.limits.max_speed_m_s * 100:.0f} cm/s", 30, 314, face=small)
            label("1/2/3 level | P push  R manual return  W wait | H home (brakes first)", 30, 336, face=small)
            label("After READY: hold SPACE to start  |  release / S stop  |  Esc quit", 30, 362, face=small)
            timing = getattr(session, "last_timing", None)
            if timing:
                label(f"Feedback {timing['feedback_dt_ms']:.1f} ms | Read {timing['sample_ms']:.1f} ms | "
                      f"Command {timing['command_ms']:.1f} ms", 30, 389, face=small)
            else:
                label("Error details: mouse wheel / PageUp / PageDown (also printed in terminal)", 30, 389, face=small)
            if session and monitor_origin is not None:
                axis_text = ",".join(f"{value:+g}" for value in session.limits.axis_base)
                origin_text = ",".join(f"{value:+.4f}" for value in monitor_origin)
                origin_kind = "FIXED" if fixed_origin is not None else "SESSION"
                label(f"Push BASE [{axis_text}] | {origin_kind} origin XYZ (m): [{origin_text}]", 30, 412, face=small)
            columns = max(20, (screen.get_width() - 60) // small.size("M")[0])
            lines = [line for paragraph in notice.splitlines()
                     for line in (textwrap.wrap(paragraph, columns) or [""])]
            count = max(1, (screen.get_height() - 462) // 20)
            notice_scroll = min(notice_scroll, max(0, len(lines) - count))
            for index, line in enumerate(lines[notice_scroll:notice_scroll + count]):
                label(line, 30, 448 + index * 20, (236, 170, 88), small)
            pg.display.flip()
            if pending_start:
                pending_start = False
                try:
                    experiment = load_experiment(config_path)
                    config, limits = experiment.robot_settings, experiment.limits
                    fixed_origin = load_start(config_path, config)
                    log_path = (output if output is not None else automatic_log_path(experiment.results_dir)) if enable else None
                    if log_path is not None and Path(log_path).exists():
                        raise FileExistsError(f"Log already exists: {log_path}. Omit --output for an automatic unique name.")
                    session = RobotSession(config, limits, enable=enable, log_path=log_path,
                                          fixed_origin=fixed_origin)
                    session.connect()
                    monitor_origin = tuple((fixed_origin or session.last_sample.pose)[:3])
                    if fixed_origin is not None:
                        phase = "wait"
                    if enable:
                        session.prepare(check_cancelled=cancel_preflight)
                        # Match read-only progress to the actual calibrated
                        # control origin, rather than the earlier connect sample.
                        monitor_origin = tuple((fixed_origin or session.last_sample.pose)[:3])
                        ready = True
                        space_released = False
                        home_notice = "\nFIXED START loaded. Hold SPACE; H returns to start, P pushes, R returns manually." if fixed_origin else ""
                        set_notice(f"READY: release SPACE, then hold it to start. Keep handle unloaded until OUTPUT ON.{home_notice}\nLog: {log_path}")
                    else:
                        set_notice("READ ONLY: monitoring live feedback. Motion requires --enable and successful startup checks.")
                except Exception as exc:
                    ready = False
                    if session:
                        session.stop("startup_failed")
                    set_notice(f"CHECK FAILED: {exc}\nOutput remains OFF. Exit and restart after resolving this error.")
                continue  # Render the complete startup result before a screenshot.
            if screenshot:
                screenshot = Path(screenshot)
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                pg.image.save(screen, str(screenshot))
                running = False
            clock.tick(60)
    finally:
        if session:
            session.close()
        pg.quit()


def main(argv=None):
    """
    @brief  解析独立体验参数并选择保存起点或运行窗口。
    @param  argv 命令行参数列表；None 表示使用进程命令行。
    @retval None 所选操作完成。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=TEMPLATE)
    parser.add_argument("--enable", action="store_true", help="Automatic read-only preflight; after READY hold SPACE to enable motion")
    parser.add_argument("--offline-ui", action="store_true", help="UI preview with no robot connection or commands")
    parser.add_argument("--output", type=Path, help="Optional new JSONL path; otherwise a unique log name is generated with --enable")
    parser.add_argument("--screenshot", type=Path, help="Save UI image and quit")
    parser.add_argument("--save-start", action="store_true",
                        help="Read-only: save current stationary base TCP pose once, then exit; no motion")
    args = parser.parse_args(argv)
    if args.save_start:
        if args.enable or args.offline_ui or args.screenshot or args.output:
            parser.error("--save-start must be used alone with --config; never combine it with --enable")
        config, limits = load_robot_config(args.config)
        path, pose = capture_start(args.config, config, limits)
        print(f"FIXED START SAVED: {path}\nBase pose (m/rad): {pose}\n"
              "Read-only capture; no motion sent. Future launches keep this position.")
        return
    run(args.config, enable=args.enable, offline_ui=args.offline_ui,
        output=args.output, screenshot=args.screenshot)


if __name__ == "__main__":
    main()
