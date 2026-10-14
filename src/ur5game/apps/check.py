"""Read-only UR5 force-baseline check; never creates an RTDE control interface."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import time

from ..robot.types import Sample
from ..robot.admittance import dot, norm
from ..robot.ur5_device import URRTDEDevice
from ..config import load_robot_config, DEFAULT_CONFIG
from ..robot.guards import resolve_policy


def analyze(samples: list[Sample], axis, policy=None) -> dict:
    """
    @brief  计算实测力均值、噪声及基线条件诊断。
    @param  samples 按时间排列的机器人实测 Sample 序列。
    @param  axis 基座坐标下的推出方向单位向量。
    @param  policy 集中数值策略字典；None 时使用兼容默认策略。
    @retval dict 力统计、速度、状态和通过标记。
    @note   只读诊断不会激活机器人运动。
    """
    policy = resolve_policy(policy)
    if len(samples) < 10:
        raise ValueError("At least ten advancing RTDE samples are required")
    if any(b.timestamp <= a.timestamp for a, b in zip(samples, samples[1:])):
        raise ValueError("RTDE timestamps did not advance")
    mean = tuple(statistics.mean(s.wrench[i] for s in samples) for i in range(3))
    std = tuple(statistics.pstdev(s.wrench[i] for s in samples) for i in range(3))
    peak_speed = max(norm(s.speed[:3]) for s in samples)
    result = {
        "sample_count": len(samples),
        "start_pose_m_rad": samples[0].pose,
        "end_pose_m_rad": samples[-1].pose,
        "mean_force_xyz_n": mean,
        "std_force_xyz_n": std,
        "mean_force_norm_n": norm(mean),
        "mean_force_xy_norm_n_diagnostic": norm(mean[:2]),
        "mean_force_along_push_axis_n": dot(mean, axis),
        "peak_tcp_speed_m_s": peak_speed,
        "robot_normal_throughout": all(s.robot_mode == 7 and s.safety_mode == 1 for s in samples),
    }
    result["software_baseline_gate_pass"] = (
        result["robot_normal_throughout"] and peak_speed <= policy["stationary_speed_m_s"]
        and result["mean_force_norm_n"] <= policy["baseline_max_force_n"] and max(std) <= policy["baseline_max_noise_n"]
        and all(math.isfinite(x) for x in (*mean, *std, peak_speed))
    )
    # Diagnostic only: isolates whether Z is the reason for a failed baseline.
    # RobotSession.arm() deliberately continues to use the full 3D gate.
    result["xy_only_baseline_gate_pass_diagnostic"] = (
        result["robot_normal_throughout"] and peak_speed <= policy["stationary_speed_m_s"]
        and result["mean_force_xy_norm_n_diagnostic"] <= policy["baseline_max_force_n"] and max(std[:2]) <= policy["baseline_max_noise_n"]
        and all(math.isfinite(x) for x in (*mean, *std, peak_speed))
    )
    return result


def run(config_path: Path, seconds: float) -> dict:
    """
    @brief  在指定时长内读取 UR5/HEX 状态并输出基线诊断。
    @param  config_path 实验配置文件路径。
    @param  seconds 持续时长，单位 s。
    @retval dict 采样统计与数据来源。
    @note   只建立接收及传感器连接，不创建控制接口。
    """
    if not .5 <= seconds <= 10:
        raise ValueError("Sampling duration must be between 0.5 and 10 seconds")
    config, limits = load_robot_config(config_path)
    device = URRTDEDevice(config["host"], limits, force_config=config)
    samples = []
    try:
        device.connect_readonly()
        last_timestamp = None
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            sample = device.sample()
            if sample.timestamp != last_timestamp:
                samples.append(sample)
                last_timestamp = sample.timestamp
            time.sleep(.008)
        result = analyze(samples, limits.axis_base, limits.safety_policy)
        result["force_source"] = config["force_source"]
        result["robot_z_guard_used"] = config["force_source"] == "hex_udp"
        if config["force_source"] == "hex_udp":
            result["mean_raw_hex_z_n_unused"] = statistics.mean(
                s.sensor_raw_wrench[2] for s in samples)
        return result
    finally:
        device.close()


def main(argv=None):
    """
    @brief  解析只读检查参数并打印诊断结果。
    @param  argv 命令行参数列表；None 表示使用进程命令行。
    @retval None 打印 JSON 结果。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    result = run(args.config, args.seconds)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    print("Read-only result. XY-only is diagnostic; it does NOT change live arm() gates or enable motion.")
    print("Passing either gate does NOT verify known-force response or human safety.")


if __name__ == "__main__":
    main()
