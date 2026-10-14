"""UR5 状态读取和速度通信；控制接口仅在明确激活后创建。"""
import socket
from .admittance import Limits
from .types import Sample
from .hex_sensor import HexUDPForceSensor

class URRTDEDevice:
    """Uses optional ur_rtde. Control interface is constructed only by arm()."""

    def __init__(self, host, limits: Limits, expected_version=None, force_config=None):
        """
        @brief  保存地址、参数和传感器配置。
        @param  host UR5 控制器 IP 地址。
        @param  limits Limits 控制限制与导纳参数对象。
        @param  expected_version 预期 PolyScope 版本字符串；None 时不进行指定版本匹配。
        @param  force_config 力数据来源和 HEX 连接设置；None 时使用内部力估计。
        @retval None 不执行网络连接。
        """
        self.host, self.limits = host, limits
        self.expected_version = expected_version
        self.receive = None
        self.control = None
        self.force_config = force_config or {"force_source": "ur5_internal_estimate"}
        self.sensor = None

    def connect_readonly(self):
        """
        @brief  创建 RTDE 接收连接及可选 HEX 连接。
        @param  None 无显式输入参数。
        @retval None 完成只读连接并读取首个采样。
        """
        try:
            from rtde_receive import RTDEReceiveInterface
        except ImportError as exc:
            raise RuntimeError("Install the official ur_rtde Python package for the lab environment") from exc
        self.receive = RTDEReceiveInterface(self.host)
        try:
            if self.force_config["force_source"] == "hex_udp":
                cfg = self.force_config
                self.sensor = HexUDPForceSensor(cfg["hex_sensor_host"], cfg["hex_sensor_port"],
                    cfg["hex_force_to_base_sign_xyz"], cfg["hex_timeout_s"])
                self.sensor.connect()
            self.sample()
        except BaseException:
            self.close()
            raise

    def sample(self):
        """
        @brief  读取 TCP 位姿、速度、力及机器人安全状态。
        @param  None 无显式输入参数。
        @retval Sample 机器人实时状态与原始力参考。
        @note   HEX 模式下控制力使用 HEX X/Y 与机器人 Z 保护通道。
        """
        if self.receive is None:
            raise RuntimeError("RTDE receive is not connected")
        r = self.receive
        robot_wrench = tuple(r.getActualTCPForce())
        sensor_raw = self.sensor.read() if self.sensor is not None else None
        # URTracker uses HEX X/Y only and explicitly sets Z to zero. Keep the
        # robot's own Z estimate as a separate conservative guard channel.
        wrench = ((sensor_raw[0], sensor_raw[1], robot_wrench[2],
                   sensor_raw[3], sensor_raw[4], sensor_raw[5])
                  if sensor_raw is not None else robot_wrench)
        return Sample(float(r.getTimestamp()), tuple(r.getActualTCPPose()),
                      tuple(r.getActualTCPSpeed()), wrench,
                      int(r.getRobotMode()), int(r.getSafetyMode()), robot_wrench, sensor_raw)

    def arm(self):
        """
        @brief  检查示教器程序状态和版本，建立 RTDE 控制接口。
        @param  None 无显式输入参数。
        @retval None 控制接口创建成功。
        @note   必须先建立只读连接；拒绝覆盖正在运行的其他机器人程序。
        """
        if self.receive is None:
            raise RuntimeError("Read-only preflight must succeed before arm")
        # Do not let the control library replace an existing pendant program.
        timeout = self.limits.safety_policy["dashboard_timeout_s"]
        with socket.create_connection((self.host, 29999), timeout=timeout) as sock:
            sock.settimeout(timeout)
            stream = sock.makefile("rwb")
            if not stream.readline():
                raise RuntimeError("Dashboard greeting missing")
            def query(command):
                """
                @brief  向 Dashboard 发送查询并读取一行回复。
                @param  command Dashboard 查询命令字符串。
                @retval str 去除首尾空白的状态回复。
                """
                stream.write((command + "\n").encode("ascii"))
                stream.flush()
                return stream.readline().decode("utf-8", "replace").strip()
            running = query("running")
            state = query("programState")
            version = query("PolyscopeVersion")
        if "false" not in running.lower() or "STOPPED" not in state.upper():
            raise RuntimeError(f"Another robot program may be running: {running} / {state}")
        if self.expected_version and self.expected_version not in version:
            raise RuntimeError(f"PolyScope version changed: expected {self.expected_version}, got {version}")
        try:
            from rtde_control import RTDEControlInterface
        except ImportError as exc:
            raise RuntimeError("ur_rtde control package is unavailable") from exc
        self.control = RTDEControlInterface(self.host)

    def command(self, velocity):
        """
        @brief  发送一个控制周期的六维 TCP 速度指令。
        @param  velocity TCP 六维速度 [vx,vy,vz,wx,wy,wz]；平移 m/s，角速度 rad/s。
        @retval None 指令未被控制器拒绝。
        @note   需要已建立控制接口。
        @warning 会驱动真实机械臂；由机器人所属控制线程调用。
        """
        if self.control is None:
            raise RuntimeError("Control interface not armed")
        # Short bounded commands still require a separately verified controller-side
        # communication watchdog. Host-side try/finally is not enough on PC failure.
        ok = self.control.speedL(list(velocity), self.limits.max_accel_m_s2,
                                 1 / self.limits.control_hz)
        if ok is False:
            raise RuntimeError("UR speedL command was rejected")

    def stop(self):
        """
        @brief  请求速度停止并结束当前控制脚本。
        @param  None 无显式输入参数。
        @retval None 停止请求完成，控制接口引用清空。
        """
        if self.control is not None:
            try:
                self.control.speedStop(self.limits.max_accel_m_s2)
            finally:
                self.control.stopScript()
                self.control = None

    def close(self):
        """
        @brief  停止控制并关闭 HEX 和 RTDE 接收连接。
        @param  None 无显式输入参数。
        @retval None 完成连接清理。
        """
        self.stop()
        if self.sensor is not None:
            self.sensor.close()
            self.sensor = None
        if self.receive is not None:
            if hasattr(self.receive, "disconnect"):
                self.receive.disconnect()
            self.receive = None
