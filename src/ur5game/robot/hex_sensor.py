"""Bounded read-only UDP adapter for the HEX sensor used by URTracker.

Protocol and scaling are taken from URTracker's UR5_admittance/hex.py. This
module never sends a robot command. A missing/invalid reply is a fault, not a
zero-force measurement.
"""
from __future__ import annotations

import math
import socket
import struct

REQUEST = bytes.fromhex("1234000200000001")
REPLY_HEADER_BYTES = 12
REPLY_VALUES_BYTES = 24


def decode_reply(packet: bytes, signs: tuple[float, float, float]) -> tuple[float, ...]:
    """
    @brief  按 HEX UDP 格式解码六维数据并调整三个力轴符号。
    @param  packet HEX UDP 原始回复字节串。
    @param  signs 三个力轴的符号映射，每项为 1 或 -1。
    @retval tuple 六个测量值，前三项为力 N，后三项为力矩。
    """
    if len(packet) < REPLY_HEADER_BYTES + REPLY_VALUES_BYTES:
        raise ValueError(f"HEX reply too short: {len(packet)} bytes")
    raw = struct.unpack(">6i", packet[REPLY_HEADER_BYTES:REPLY_HEADER_BYTES + REPLY_VALUES_BYTES])
    values = tuple(value / 10000.0 for value in raw)
    force = tuple(signs[i] * values[i] for i in range(3))
    result = force + values[3:]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("HEX reply contains a non-finite value")
    return result


class HexUDPForceSensor:
    def __init__(self, host: str, port: int, signs, timeout_s: float):
        """
        @brief  保存 HEX 地址、符号和回复超时。
        @param  host UR5 控制器 IP 地址。
        @param  port HEX UDP 服务端口。
        @param  signs 三个力轴的符号映射，每项为 1 或 -1。
        @param  timeout_s 传感器回复超时，单位 s。
        @retval None 不建立连接。
        """
        self.host = host
        self.port = port
        self.signs = tuple(signs)
        self.timeout_s = timeout_s
        self.sock = None

    def connect(self):
        """
        @brief  创建 UDP 套接字并设置目标地址及回复超时。
        @param  None 无显式输入参数。
        @retval None UDP 连接准备完成。
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout_s)
        try:
            self.sock.connect((self.host, self.port))
        except BaseException:
            self.close()
            raise

    def read(self) -> tuple[float, ...]:
        """
        @brief  请求一帧 HEX 数据并解码回复。
        @param  None 无显式输入参数。
        @retval tuple 六维传感器测量值。
        @note   回复无效或超时抛出 RuntimeError，不把缺失测量替换为零。
        """
        if self.sock is None:
            raise RuntimeError("HEX sensor is not connected")
        try:
            self.sock.send(REQUEST)
            packet = self.sock.recv(1024)
            return decode_reply(packet, self.signs)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"HEX sensor reply unavailable/invalid: {exc}") from exc

    def close(self):
        """
        @brief  关闭传感器套接字。
        @param  None 无显式输入参数。
        @retval None 清空连接引用。
        """
        if self.sock is not None:
            self.sock.close()
            self.sock = None
