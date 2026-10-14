"""Read-only HEX UDP force check. Does not connect to or move the UR5."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time

from ..robot.hex_sensor import HexUDPForceSensor
from ..config import load_robot_config, DEFAULT_CONFIG


def run(config_path: Path, seconds: float) -> dict:
    """
    @brief  只读采集 HEX 数据并统计力与查询延迟。
    @param  config_path 实验配置文件路径。
    @param  seconds 持续时长，单位 s。
    @retval dict 传感器力统计、样本数量和延迟。
    @note   不连接 UR5 控制接口。
    """
    if not .5 <= seconds <= 10:
        raise ValueError("Sampling duration must be between 0.5 and 10 seconds")
    config, _ = load_robot_config(config_path)
    if config.get("force_source") != "hex_udp":
        raise ValueError("Use a config with force_source=hex_udp")
    sensor = HexUDPForceSensor(config["hex_sensor_host"], config["hex_sensor_port"],
        config["hex_force_to_base_sign_xyz"], config["hex_timeout_s"])
    rows = []
    latencies = []
    try:
        sensor.connect()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            started = time.monotonic()
            rows.append(sensor.read())
            latencies.append(time.monotonic() - started)
            time.sleep(.008)
    finally:
        sensor.close()
    if len(rows) < 10:
        raise RuntimeError(f"Only {len(rows)} HEX samples; at least 10 are required")
    return {
        "sensor_host": config["hex_sensor_host"],
        "sample_count": len(rows),
        "mean_force_base_xyz_n": [statistics.mean(row[i] for row in rows) for i in range(3)],
        "std_force_base_xyz_n": [statistics.pstdev(row[i] for row in rows) for i in range(3)],
        "mean_force_along_push_axis_n": statistics.mean(
            sum(row[i] * config["axis_base"][i] for i in range(3)) for row in rows),
        "peak_query_latency_ms": 1000 * max(latencies),
        "mean_query_latency_ms": 1000 * statistics.mean(latencies),
    }


def main(argv=None):
    """
    @brief  解析 HEX 检查参数并输出统计结果。
    @param  argv 命令行参数列表；None 表示使用进程命令行。
    @retval None 打印 JSON 结果。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.config, args.seconds), ensure_ascii=False, indent=2))
    print("Read-only HEX result; this does not calibrate force magnitude or enable UR5 motion.")


if __name__ == "__main__":
    main()
