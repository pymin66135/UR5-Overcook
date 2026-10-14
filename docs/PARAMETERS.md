# 参数在哪里改

编辑 `configs/experiment.json`，保存后重启。所有值带单位后缀；三个 load_profiles 对象依次对应轻、中、重档。

## 三档负载

| 字段 | 当前三档 | 含义 |
| --- | --- | --- |
| `load_profiles[*].mass_kg` | 0.1 / 1 / 1 | 虚拟质量，kg；越大，模型响应越慢 |
| `load_profiles[*].damping_n_s_m` | 0.1 / 40 / 80 | 黏性阻尼，N·s/m；负载随运动速度变化 |
| `load_profiles[*].stiffness_n_m` | 0.01 / 4 / 8 | X 弹簧刚度，N/m；随推出位置增加 |

RETURN 使用轻档，X 弹簧项关闭。游戏每个窗口选择下一窗口的推出档位。
真实工具质量是 `robot.tool_mass_kg=0.1`，与虚拟质量不同。
`motion.mass_kg` 是原控制器的备用字段；在本工程三档配置完整时，实际模型使用 load_profiles 中的质量。

## 运动与回位

| 字段 | 当前值 | 含义 |
| --- | --- | --- |
| `motion.axis_base` | [-1,0,0] | 基座 X− 推出，Z 不主动运动 |
| `motion.travel_m` | 0.10 | X 行程，10 cm 等于 100% |
| `motion.planar_y_travel_m` | 0.05 | Y 左右各 5 cm |
| `motion.max_speed_m_s` | 0.25 | 合成指令速度上限 25 cm/s |
| `motion.max_tracking_speed_m_s` | 0.30 | 实测速度停机线 30 cm/s |
| `motion.max_accel_m_s2` | 0.5 | 合成加速度上限 |
| `motion.max_force_n` | 50 | XY 交互力模长停机线，不是恒力输出 |
| `motion.max_lateral_m` | 0.01 | XY 模式下的 Z 漂移停机线，1 cm |
| `motion.max_rotation_rad` | 0.15 | 相对起始姿态的真实最短旋转角限制，用四元数计算 |
| `motion.force_filter_alpha` | 0.35 | 力滤波；更大表示响应更快、保留更多波动 |
| `motion.force_deadband_n` | 2 | 扣除基线、滤波后的力死区 |
| `motion.travel_margin_m` | 0.002 | 超限容差 2 mm，非体验行程 |
| `motion.control_hz` | 20 | 控制频率 |
| `motion.planar_y_mass_kg` | 0.5 | Y 虚拟质量 |
| `motion.planar_y_damping_n_s_m` | 60 | Y 阻尼 |
| `motion.planar_y_stiffness_n_m` | 80 | Y 回中弹簧刚度 |
| `motion.planar_y_center_speed_m_s` | 0.01 | Y 主动回中速度上限 1 cm/s |
| `motion.planar_y_center_tolerance_m` | 0.0005 | Y 中心容差 0.5 mm |
| `homing.home_speed_m_s` | 0.20 | H 回位速度上限 20 cm/s，接近起点减速 |
| `homing.home_position_gain_s_inv` | 3 | 归位位置误差转换为目标速度的比例 |
| `homing.home_tolerance_m` | 0.001 | XY 归位完成容差 1 mm |
| `homing.home_timeout_s` | 20 | 归位时限 |
| `homing.home_abort_force_n` | 10 | 归位接触/对抗停机阈值 |

迁移时保留当前原文件数值，包括姿态 0.15 rad、Y ±5 cm、Z 漂移 1 cm。历史文档中的 2 cm 等旧值没有重新套用。

## 游戏负载映射

- `interaction.window_seconds=3`：按游戏时间累计窗口。
- `interaction.q_ref_cells=3`：加权移动量小于 1 用轻档，1 到小于 2 用中档，大于等于 2 用重档。
- `interaction.item_weights`：食材 1、盘子 1.5、成品 2，空手 0。
- `interaction.game_endpoint_hold_s=0.4`：推出到位且停稳后停留，再允许人工拉回。

按前一个状态持有的物品计算移动；站着不动或空手移动不增加搬运量。
档位变化平滑过渡，行程切换只看真实位置。

## 基线与时序

以下均位于 `checks.safety_policy`：

| 字段 | 当前值 | 用途 |
| --- | --- | --- |
| `baseline_max_force_n` | 50 N | 三维基线均值模长及激活原始力检查 |
| `baseline_max_noise_n` | 10 N | 三轴标准差最大值，即 A4 噪声阈值 |
| `stationary_speed_m_s` | 0.002 | 静止、激活、终点停稳判定 |
| `baseline_min_samples` | 25 | 最少基线样本 |
| `baseline_sample_interval_s` | 0.02 | 基线采样请求间隔 |
| `game_input_timeout_s` / `game_state_timeout_s` | 0.5 / 0.5 s | 界面/游戏状态运行超时 |
| `startup_ui_sync_timeout_s` | 2 s | 控制接口连接后等待新界面帧 |
| `feedback_soft_periods` / `feedback_hard_periods` | 2 / 3 | 20 Hz 下为 100/150 ms；连续两次软超时也停机 |
| `stroke_tolerance_m` | 0.001 | 游戏行程到位容差 |
| `parameter_slew_s` | 0.3 | 档位参数切换平滑时间 |

`checks.force_baseline_seconds=3` 是采样时长。HEX Z 仍不用于控制，机器人内部 Z 力估计仍参与三维基线检查。
`checks.safety_policy.upper` 是配置校验上限，`ranges` 是配置允许范围；报错会指明对应字段。
提高配置上限不会修改示教器限制。当前完整策略已显式保存在 JSON 中。

## 路径与模型

`paths` 下的相对路径均以配置所属的工程根目录为基准。模型是 `models/low.pt`、`high.pt`；`manifest.json` 保留来源哈希和导出哈希。
`game.questionnaire_after_round` 默认 false；也可以启动时加 `--questionnaire`。
`game.seed=41` 为当前默认种子，`game.fps=60` 为界面刷新频率；游戏固定步长由 `configs/scene.json` 决定。
