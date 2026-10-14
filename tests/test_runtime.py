"""独立离线回归：使用假设备，不连接 UR5 或 HEX。"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bootstrap import activate
activate()

from ur5game.config import load_experiment
from ur5game.robot.admittance import Admittance, SafetyFault
from ur5game.robot.types import Sample
from ur5game.robot.session import RobotSession
from ur5game.robot.service import RobotService
from ur5game.robot.homing import load_start
from ur5game.interaction.stroke_cycle import StrokeCycle
from ur5game.interaction.load_mapping import LoadMapper
from ur5game.game.ai_partner import AIPartner
from ur5game.game.environment import ForcedCoopEnv, load_config
from ur5game.questionnaire.nasa_tlx import new_record, rate, submit

CONFIG = load_experiment(ROOT / "configs/experiment.json")
ORIGIN = load_start(CONFIG.source, CONFIG.robot_settings)


def sample(t, x=0., y=0., z=0., force=0., fy=0., speed=(0.,)*6):
    """
    @brief  构造相对固定起点的假机器人采样。
    @param  t 机器人采样时间戳，单位 s。
    @param  x TCP 沿 Base X− 相对起点的推出位移，单位 m。
    @param  y TCP 相对固定起点的基座 Y 偏移，单位 m。
    @param  z 测试 TCP 相对起点的 Z 偏移，单位 m。
    @param  force 测试施加的推出方向交互力，单位 N。
    @param  fy 测试施加的基座 Y 交互力，单位 N。
    @param  speed 测试采样的六维速度，平移 m/s、角速度 rad/s。
    @retval Sample 测试采样。
    @note   仅用于离线测试，不连接机器人。
    """
    pose = (ORIGIN[0]-x, ORIGIN[1]+y, ORIGIN[2]+z, *ORIGIN[3:])
    return Sample(t, pose, speed, (-force, fy, 0., 0., 0., 0.), 7, 1)


def env(tick=0, position=(0, 0), held=None):
    """
    @brief  构造用于负载映射测试的简化游戏环境。
    @param  tick 测试游戏逻辑步数。
    @param  position 测试玩家所在的游戏网格坐标 (x,y)。
    @param  held 测试玩家持有的物品名称；None 表示空手。
    @retval SimpleNamespace 测试环境。
    @note   仅用于离线测试，不连接机器人。
    """
    player = SimpleNamespace(position=position, held_object=SimpleNamespace(name=held))
    return SimpleNamespace(state=SimpleNamespace(players=[player]), time=tick, config={"step_hz": 6})


class FakeDevice:
    def __init__(self):
        """
        @brief  初始化不连接网络的假设备。
        @param  None 无显式输入参数。
        @retval None 完成假状态初始化。
        @note   仅用于离线测试，不连接机器人。
        """
        self.timestamp = 0.
        self.commands = []
        self.stops = 0
        self.armed = False
        self.force = 0.

    def sample(self):
        """
        @brief  返回时间递增的假机器人采样。
        @param  None 无显式输入参数。
        @retval Sample 假采样。
        @note   仅用于离线测试，不连接机器人。
        """
        self.timestamp += .05
        return sample(self.timestamp, force=self.force)

    def arm(self):
        """
        @brief  模拟控制接口激活。
        @param  None 无显式输入参数。
        @retval None 设置假激活标记。
        @note   仅用于离线测试，不连接机器人。
        """
        self.armed = True
    def command(self, velocity):
        """
        @brief  保存待验证的假速度命令。
        @param  velocity TCP 六维速度 [vx,vy,vz,wx,wy,wz]；平移 m/s，角速度 rad/s。
        @retval None 追加命令；不操作硬件。
        @note   仅用于离线测试，不连接机器人。
        """
        self.commands.append(velocity)
    def stop(self):
        """
        @brief  模拟停止并累计停止次数。
        @param  None 无显式输入参数。
        @retval None 清除假激活标记。
        @note   仅用于离线测试，不连接机器人。
        """
        self.armed = False; self.stops += 1
    def close(self):
        """
        @brief  关闭假设备并模拟停止。
        @param  None 无显式输入参数。
        @retval None 完成假清理。
        @note   仅用于离线测试，不连接机器人。
        """
        self.stop()


class TestControl(unittest.TestCase):
    def model(self):
        """
        @brief  构造零基线的测试导纳模型。
        @param  None 无显式输入参数。
        @retval Admittance 测试模型。
        @note   仅用于离线测试，不连接机器人。
        """
        return Admittance(CONFIG.limits, sample(0.), 0.)

    def test_real_prepare_path_with_fake_device_is_readonly(self):
        """
        @brief  验证真实准备路径使用假设备时只读采样且能正确激活。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        device = FakeDevice()
        device.connect_readonly = lambda: None
        session = RobotSession(CONFIG.robot_settings, CONFIG.limits, enable=True,
                               device=device, fixed_origin=ORIGIN)
        clock = SimpleNamespace(now=0.)
        def advance(seconds):
            """
            @brief  推进测试虚拟时钟。
            @param  seconds 持续时长，单位 s。
            @retval None 增加假时间，不进行真实等待。
            @note   仅用于离线测试，不连接机器人。
            """
            clock.now += seconds
        with patch("ur5game.robot.session.time.monotonic", side_effect=lambda: clock.now), \
                patch("ur5game.robot.session.time.sleep", side_effect=advance):
            session.connect()
            session.prepare()
        self.assertTrue(session.prepared)
        self.assertFalse(session.armed)
        self.assertFalse(device.armed)
        self.assertEqual(device.commands, [])
        session.activate_prepared()
        session.tick(0, "wait")
        self.assertEqual(device.commands, [(0.,)*6])
        session.close()

    def test_three_profiles_have_distinct_response(self):
        """
        @brief  验证三档负载响应有区分且速度受限。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        velocities = []
        for level in range(3):
            model = self.model()
            for i in range(1, 201):
                output = model.tick(sample(i*.05, x=.03, force=3.), level, "push")
            velocities.append(-output.velocity_base[0])
        self.assertGreater(velocities[0], velocities[1])
        self.assertGreater(velocities[1], velocities[2])
        self.assertLessEqual(velocities[0], CONFIG.limits.max_speed_m_s)

    def test_push_return_and_y_recenter_directions(self):
        """
        @brief  验证推出、人工返回与 Y 回中的速度方向。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        model = self.model()
        for i in range(1, 6):
            out = model.tick(sample(i*.05, x=.04, y=.003, force=4.), 0, "push")
        self.assertLess(out.velocity_base[0], 0.)
        self.assertLess(out.velocity_base[1], 0.)
        model = self.model()
        for i in range(1, 6):
            out = model.tick(sample(i*.05, x=.04, force=-4.), 0, "return")
        self.assertGreater(out.velocity_base[0], 0.)

    def test_wait_stops_xy_and_does_not_recenter(self):
        """
        @brief  验证等待阶段同时停止 X/Y 输出。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        out = self.model().tick(sample(.05, x=.03, y=.003, force=4.), 0, "wait")
        self.assertEqual(out.velocity_base, (0.,)*6)

    def test_endpoint_cannot_command_outward(self):
        """
        @brief  验证终点不再产生向外速度。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        out = self.model().tick(sample(.05, x=CONFIG.limits.travel_m, force=5.), 0, "push")
        self.assertEqual(out.velocity_base[0], 0.)

    def test_workspace_force_and_speed_faults(self):
        """
        @brief  验证位置、交互力和实测速度超限会停止。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        for kwargs in ({"x": CONFIG.limits.travel_m+.003},
                       {"y": CONFIG.limits.planar_y_travel_m+.003},
                       {"z": CONFIG.limits.max_lateral_m+.001},
                       {"force": 36., "fy": 36.},
                       {"speed": (.31, 0., 0., 0., 0., 0.)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(SafetyFault):
                self.model().tick(sample(.05, **kwargs), 0, "push")

    def test_single_delay_holds_and_second_delay_stops(self):
        """
        @brief  验证单次反馈延迟零速等待，连续延迟锁定停止。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        model = self.model()
        out = model.tick(sample(.12, force=4.), 0, "push")
        self.assertTrue(out.timing_hold)
        self.assertEqual(out.velocity_base, (0.,)*6)
        with self.assertRaises(SafetyFault): model.tick(sample(.24, force=4.), 0, "push")

    def test_nan_or_backward_clock_cannot_move(self):
        """
        @brief  验证非法或倒退时间戳不能产生运动指令。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        for t in (float("nan"), -.01, 0.):
            with self.subTest(t=t), self.assertRaises(SafetyFault):
                self.model().tick(sample(t, force=5.), 0, "push")

    def prepared(self):
        """
        @brief  构造已通过准备状态的假会话。
        @param  None 无显式输入参数。
        @retval tuple (RobotSession, FakeDevice)。
        @note   仅用于离线测试，不连接机器人。
        """
        device = FakeDevice()
        session = RobotSession(CONFIG.robot_settings, CONFIG.limits, enable=True,
                               device=device, fixed_origin=ORIGIN)
        session.connected = session.prepared = True
        session.model = Admittance(CONFIG.limits, device.sample(), 0.)
        return session, device

    def test_setup_handoff_before_fresh_timebase_and_commands(self):
        """
        @brief  验证连接同步完成后才建立新时间基准并发送指令。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        session, device = self.prepared()
        def handoff():
            """
            @brief  模拟控制连接后的界面同步与时间推进。
            @param  None 无显式输入参数。
            @retval None 同步条件检查通过。
            @note   仅用于离线测试，不连接机器人。
            """
            self.assertEqual(device.commands, [])
            for _ in range(30): device.sample()
        session.activate_prepared(after_control_setup=handoff)
        self.assertEqual(device.commands, [])
        session.tick(0, "wait")
        self.assertEqual(device.commands[-1], (0.,)*6)
        session.close()

    def test_cancelled_setup_never_commands(self):
        """
        @brief  验证连接阶段取消不会发送速度。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        session, device = self.prepared()
        def cancel():
            """
            @brief  模拟操作员取消授权。
            @param  None 无显式输入参数。
            @retval 无正常返回；抛出 RuntimeError。
            @note   仅用于离线测试，不连接机器人。
            """
            raise RuntimeError("operator_stop")
        with self.assertRaisesRegex(RuntimeError, "operator_stop"):
            session.activate_prepared(after_control_setup=cancel)
        self.assertFalse(session.armed)
        self.assertEqual(device.commands, [])
        self.assertGreater(device.stops, 0)

    def test_guard_after_read_prevents_send(self):
        """
        @brief  验证采样后撤销授权可以阻止发送。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        session, device = self.prepared()
        session.activate_prepared()
        def cancel():
            """
            @brief  模拟操作员取消授权。
            @param  None 无显式输入参数。
            @retval 无正常返回；抛出 RuntimeError。
            @note   仅用于离线测试，不连接机器人。
            """
            raise RuntimeError("operator_stop")
        with self.assertRaises(RuntimeError): session.tick(0, "push", command_guard=cancel)
        self.assertEqual(device.commands, [])
        self.assertFalse(session.armed)


class TestInteraction(unittest.TestCase):
    def test_position_controls_cycle_with_dwell(self):
        """
        @brief  验证按位置切换推拉并执行终点停稳停留。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        cycle = StrokeCycle(.1, policy=CONFIG.limits.safety_policy)
        self.assertIsNone(cycle.update(.05, timestamp=100., speed_m_s=0.))
        self.assertEqual(cycle.phase, "push")
        cycle.update(.1)
        self.assertEqual(cycle.phase, "endpoint_hold")
        cycle.update(.1, timestamp=101., speed_m_s=.01)
        self.assertIsNone(cycle.hold_since)
        cycle.update(.1, timestamp=102., speed_m_s=0.)
        cycle.update(.1, timestamp=102.41, speed_m_s=0.)
        self.assertEqual(cycle.phase, "return")
        cycle.update(0.)
        self.assertEqual(cycle.completed, 1)
        self.assertEqual(cycle.phase, "push")

    def test_home_does_not_count_half_stroke(self):
        """
        @brief  验证归位中断的半程不计作完整行程。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        cycle = StrokeCycle(.1)
        cycle.update(.1)
        cycle.home()
        cycle.update(0., home_done=True)
        self.assertEqual(cycle.completed, 0)

    def test_movement_and_delay_reset_dwell(self):
        """
        @brief  验证未停稳或延迟反馈会重置终点停留计时。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        cycle = StrokeCycle(.1)
        cycle.update(.1)
        cycle.update(.1, timestamp=1., speed_m_s=0.)
        cycle.update(.1, timestamp=1.3, speed_m_s=.01)
        self.assertIsNone(cycle.hold_since)
        cycle.update(.1, timestamp=1.5, speed_m_s=0.)
        cycle.update(.1, timestamp=1.9, speed_m_s=0., timing_hold=True)
        self.assertEqual(cycle.phase, "endpoint_hold")

    def test_previous_window_distance_sets_next_load(self):
        """
        @brief  验证上一时间窗的搬运量决定下一时间窗档位。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        mapper = LoadMapper(CONFIG.interaction)
        mapper.reset(env(held="onion"))
        self.assertIsNone(mapper.feed(env(1, (1,0), "onion")))
        self.assertIsNone(mapper.feed(env(2, (2,0), "onion")))
        result = mapper.feed(env(18, (3,0), "onion"))
        self.assertEqual((result.source_window, result.applied_window, result.level), (0,1,2))
        self.assertEqual(result.weighted_cells, 3.)

    def test_empty_motion_and_stationary_holding_do_not_add_quantity(self):
        """
        @brief  验证空手移动和静止持物不增加搬运量。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        mapper = LoadMapper(CONFIG.interaction)
        mapper.reset(env())
        mapper.feed(env(1, (1,0)))
        mapper.feed(env(2, (1,0), "soup"))
        result = mapper.feed(env(18, (1,0), "soup"))
        self.assertEqual(result.weighted_cells, 0.)

    def test_teleport_or_unknown_item_is_rejected(self):
        """
        @brief  验证非邻格移动和未知物品被拒绝。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        for bad in (env(1, (3,0)), env(1, held="unknown")):
            mapper = LoadMapper(CONFIG.interaction)
            mapper.reset(env())
            with self.assertRaises(ValueError): mapper.feed(bad)


class FakeSession:
    def __init__(self, config, limits, **kwargs):
        """
        @brief  初始化线程协调测试使用的假会话。
        @param  config 当前模块使用的配置字典。
        @param  limits Limits 控制限制与导纳参数对象。
        @param  kwargs 兼容会话构造参数，测试中包含固定起点等字段。
        @retval None 完成假状态初始化。
        @note   仅用于离线测试，不连接机器人。
        """
        self.armed = self.fault = False
        self.last_sample = Sample(0., kwargs["fixed_origin"], (0.,)*6, (0.,)*6, 7, 1)
        self.home_motion = SimpleNamespace(done=False)
        self.commands = []
        self.call_threads = []
        self.hold_setup = False
        self.entered, self.release = threading.Event(), threading.Event()
        self.position = 0.

    def connect(self):
        """
        @brief  记录假连接调用的线程。
        @param  None 无显式输入参数。
        @retval None 不连接网络。
        @note   仅用于离线测试，不连接机器人。
        """
        self.call_threads.append(threading.get_ident())
    def prepare(self, check_cancelled):
        """
        @brief  模拟只读准备并执行取消检查。
        @param  check_cancelled 准备期间的取消检查回调；None 时不额外执行回调。
        @retval None 准备检查完成。
        @note   仅用于离线测试，不连接机器人。
        """
        check_cancelled()
    def activate_prepared(self, *, after_control_setup=None):
        """
        @brief  模拟可阻塞的控制连接并执行界面同步回调。
        @param  after_control_setup 控制接口建立后的同步回调；None 时直接检查新反馈。
        @retval None 设置假激活标记。
        @note   仅用于离线测试，不连接机器人。
        """
        if self.hold_setup:
            self.entered.set()
            if not self.release.wait(2.): raise RuntimeError("fake setup timeout")
        after_control_setup()
        self.armed = True
    def tick(self, level, phase, override, *, command_guard):
        """
        @brief  执行发送前授权检查并记录假控制阶段。
        @param  level 档位索引 0/1/2，或 AI 的 low/high 标识，具体由当前函数决定。
        @param  phase 控制阶段标识，按调用接口使用 push/return/wait/home。
        @param  override 测试传入的可选阻尼覆盖值。
        @param  command_guard 发送速度前的授权检查回调；None 时无附加外部检查。
        @retval SimpleNamespace 假输出状态。
        @note   仅用于离线测试，不连接机器人。
        """
        command_guard()
        self.call_threads.append(threading.get_ident())
        self.commands.append(phase)
        self.last_sample = replace(self.last_sample, timestamp=self.last_sample.timestamp+.05)
        self.home_motion.done = phase == "home"
        return SimpleNamespace(position_m=self.position, progress_percent=100*self.position/.1,
                               damping_n_s_m=.1, timing_hold=False)
    def close(self):
        """
        @brief  清除假会话激活标记。
        @param  None 无显式输入参数。
        @retval None 不操作硬件。
        @note   仅用于离线测试，不连接机器人。
        """
        self.armed = False


class TestService(unittest.TestCase):
    def setUp(self):
        """
        @brief  为每个服务测试创建独立的记录位置。
        @param  None 无显式输入参数。
        @retval None 初始化测试状态。
        @note   仅用于离线测试，不连接机器人。
        """
        self.output = ROOT / "results/offline_tests" / uuid4().hex
        self.service = None

    def make(self):
        """
        @brief  创建并启动只使用假设备的机器人服务。
        @param  None 无显式输入参数。
        @retval RobotService 已达到 READY 的测试服务。
        @note   仅用于离线测试，不连接机器人。
        """
        self.service = RobotService(CONFIG, self.output, enable=True, session_factory=FakeSession)
        self.addCleanup(self.service.close)
        self.service.begin_episode(env(), SimpleNamespace(base_metadata={
            "episode_id": "test", "data_source": "TEST_FAKE_HARDWARE"}))
        self.wait(lambda: self.service.status == "READY")
        return self.service

    def wait(self, predicate, *, drive=False):
        """
        @brief  在限定时间内等待测试条件并按需驱动心跳。
        @param  predicate 用于测试等待的条件函数。
        @param  drive 测试等待期间是否同时发布界面及游戏心跳。
        @retval None 条件满足；超时触发测试断言。
        @note   仅用于离线测试，不连接机器人。
        """
        deadline = time.monotonic()+2.
        while not predicate() and time.monotonic() < deadline:
            if drive:
                self.service.finish_input_frame(focused=True)
                self.service.publish_env(env())
            time.sleep(.002)
        self.assertTrue(predicate(), getattr(self.service, "fault", None))

    def start(self, service):
        """
        @brief  提交假 F6 授权并等待服务进入 ON。
        @param  service 机器人服务及当前状态。
        @retval None 测试服务开始控制循环。
        @note   仅用于离线测试，不连接机器人。
        """
        service.poll_inputs(focused=True)
        service.enable()
        self.wait(lambda: service.status == "ON", drive=True)

    def test_disabled_never_constructs_hardware(self):
        """
        @brief  验证未启用实机时不构造硬件会话。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        factory = Mock(side_effect=AssertionError("Hardware not allowed"))
        service = RobotService(CONFIG, self.output, enable=False, session_factory=factory)
        service.begin_episode(env(), SimpleNamespace(base_metadata={"episode_id":"test"}))
        service.publish_env(env())
        service.close()
        factory.assert_not_called()

    def test_f6_starts_no_held_key_and_f7_latches(self):
        """
        @brief  验证 F6 单次授权、单线程通信和 F7 停止锁定。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        service = self.make()
        self.assertFalse(service.activation.is_set())
        self.start(service)
        self.wait(lambda: bool(service.session.commands), drive=True)
        self.assertEqual(len(set(service.session.call_threads)), 1)
        self.assertNotEqual(service.session.call_threads[0], threading.get_ident())
        service.stop("operator_stop")
        service.finish_input_frame(focused=True)
        service.close()
        self.assertFalse(service.session.armed)
        with self.assertRaises(RuntimeError): service.enable()

    def test_cancel_during_setup_prevents_first_command(self):
        """
        @brief  验证连接期间停止能阻止首条速度指令。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        service = self.make()
        service.session.hold_setup = True
        service.poll_inputs(focused=True)
        service.enable()
        self.assertTrue(service.session.entered.wait(1.))
        service.stop("operator_stop")
        service.session.release.set()
        service.close()
        self.assertEqual(service.session.commands, [])

    def test_focus_loss_stops_even_when_focus_returns(self):
        """
        @brief  验证失焦停止后恢复焦点不会重新使能。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        service = self.make()
        self.start(service)
        service.poll_inputs(focused=False)
        service.finish_input_frame(focused=True)
        service.close()
        self.assertFalse(service.run_authorized)
        self.assertEqual(service.stop_reason, "input_focus_lost")

    def test_stale_input_has_clear_reason(self):
        """
        @brief  验证界面超时的故障原因明确可读。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        service = self.make()
        self.start(service)
        service.last_input = time.monotonic()-1.
        self.wait(lambda: service.status == "FAULT")
        self.assertIn("game_input_stale", service.fault)

    def test_home_and_manual_cycle(self):
        """
        @brief  验证人工推拉计数和 H 归位请求。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        service = self.make()
        self.start(service)
        service.session.position = .1
        self.wait(lambda: service.phase == "return", drive=True)
        service.session.position = 0.
        self.wait(lambda: service.cycle.completed == 1, drive=True)
        service.request_home()
        self.wait(lambda: "home" in service.session.commands, drive=True)
        self.assertEqual(service.cycle.completed, 1)


class TestDataAndModels(unittest.TestCase):
    def test_questionnaire_requires_six_explicit_answers(self):
        """
        @brief  验证问卷必须显式回答全部六项。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        record = new_record("test")
        with self.assertRaises(ValueError): submit(record, "test")
        for key in record["questionnaire"]["scores"]: rate(record, "test", key, 0)
        submit(record, "test")
        self.assertEqual(record["questionnaire"]["raw_tlx"], 0.)

    def test_questionnaire_score_and_direction(self):
        """
        @brief  验证问卷均值计算与提交后的不可修改状态。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        record = new_record("test")
        for key in record["questionnaire"]["scores"]: rate(record, "test", key, 50)
        submit(record, "test")
        self.assertEqual(record["questionnaire"]["raw_tlx"], 50.)
        with self.assertRaises(ValueError): rate(record, "test", "mental", 5)

    def test_invalid_questionnaire_value(self):
        """
        @brief  验证问卷拒绝越界、非步长和布尔评分。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        for value in (-5, 101, 12, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rate(new_record("test"), "test", "mental", value)

    def test_two_ai_levels_are_readonly(self):
        """
        @brief  验证两个 AI 的导出载荷和推理模型参数只读。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        import torch
        scene = load_config(CONFIG.scene)
        for level in ("low", "high"):
            payload = torch.load(ROOT / "models" / f"{level}.pt", map_location="cpu", weights_only=True)
            self.assertNotIn("optimizer_states", payload)
            self.assertNotIn("torch_rng", payload)
            partner = AIPartner(CONFIG.model_manifest, scene, level)
            game = ForcedCoopEnv(scene, "prep")
            partner.configure_env(game)
            before = {key: value.clone() for key, value in partner.policies[1].state_dict().items()}
            for _ in range(12): partner.step(game, [4,4])
            self.assertTrue(all(not p.requires_grad for p in partner.policies[1].parameters()))
            self.assertTrue(all(torch.equal(value, before[key]) for key, value in partner.policies[1].state_dict().items()))

    def test_vendor_and_paths_belong_to_this_project(self):
        """
        @brief  验证资源与模型路径属于独立新工程。
        @param  None 无显式输入参数。
        @retval None 断言通过；失败由 unittest 报告。
        @note   仅用于离线测试，不连接机器人。
        """
        import overcooked_ai_py
        self.assertTrue(Path(overcooked_ai_py.__file__).resolve().is_relative_to(ROOT / "vendor"))
        for path in (CONFIG.scene, CONFIG.home_file, CONFIG.model_manifest, CONFIG.results_dir):
            self.assertTrue(path.is_relative_to(ROOT))


if __name__ == "__main__":
    unittest.main()
