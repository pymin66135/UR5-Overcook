# UR5 + Overcooked 实验运行与数据采集

本工程包含最新 7×9 游戏、high/low 已有 AI、UR5/HEX 控制、独立三档体验、NASA-TLX 和数据记录。AI 只执行推理，模型参数不更新。

直接使用现有 `overcooked` 环境。工程可以整体移动；游戏代码、模型和图片均从本目录读取。只需启动 `run.py`，不需要导入旧 NeuTRL 的 `src`。

## 启动

PowerShell：

```powershell
cd F:\BCI\NeuTRL\ur5_overcooked
$py = 'D:\miniconda3\envs\overcooked\python.exe'

# 游戏 + high AI，不连接机器人
& $py -X utf8 .\run.py game --ai high

# 游戏 + 真实 UR5/HEX；READY 后按一次 F6
& $py -X utf8 .\run.py game --ai high --enable

# low AI：把 high 改成 low
& $py -X utf8 .\run.py game --ai low --enable

# 独立三档体验：READY 后按住 Space
& $py -X utf8 .\run.py standalone --enable

# 只读连接和基线检查
& $py -X utf8 .\run.py check --seconds 3
& $py -X utf8 .\run.py sensor-check --seconds 3
```

`game` 不加 `--enable` 时，不创建机器人或传感器连接。`standalone` 不加 `--enable` 时读取实机状态、不发运动指令；完全离线查看独立窗口用 `standalone --offline-ui`。

这里的 `check` 是只读机器人状态和力基线采样；不自动上电、解锁或启动示教器程序。UR5 当前需要处于 RUNNING/NORMAL，控制程序检查示教器没有其他程序运行后建立控制接口。

## 操作

| 场景 | 操作 |
| --- | --- |
| 游戏人物 P0（蓝色） | WASD/方向键移动；Space 交互；1/2/3 选配方；0 自动选配方 |
| 游戏 UR5 | F6 明确开始、F7 停止、H 主动归位 |
| 游戏帮助/暂停/退出 | F1 / P / Esc；这些操作及失焦会停止机器人，重开后才能再次开始 |
| 正常推拉循环 | 实际推出到终点 → 停稳停留 → 人工拉回 → 下一次推出 |
| 独立三档 | 1/2/3 档位；P 推出；R 人工返回；W 等待；H 主动归位 |
| 独立输出 | READY 后重新按住 Space；松开、S 或 Esc 停止 |

游戏无需按住 Shift。默认 X 行程 10 cm，终点停稳后停留 0.4 秒。H 归位使用保存的起点；正常 RETURN 由人拉回。

## 修改参数

只编辑 **`configs/experiment.json`**，保存后退出重启。

- `load_profiles`：轻/中/重三档各自的质量、阻尼、刚度。
- `motion`：X 行程、Y 范围、速度、加速度、力阈值、滤波与 Y 回中。
- `homing`：H 回位速度、容差、时限、接触力阈值。
- `checks.safety_policy`：集中设置基线、时序和配置允许范围。
- `robot`：UR5/HEX 地址、坐标符号、真实工具质量和 TCP。
- `interaction`：游戏搬运量映射与终点停留。

参数解释见 [PARAMETERS.md](docs/PARAMETERS.md)。目录和阅读顺序见 [CODE_MAP.md](docs/CODE_MAP.md)。

**固定起点在 `configs/home.json`。** 已迁入原来的保存位置，程序启动时读取它；不会自动移动或重新捕获位置。需要重新记录时，先自行备份/重命名旧 `home.json`，然后把机械臂放到新起点：

```powershell
& $py -X utf8 .\run.py standalone --save-start
```

这条命令只读取位置，不发运动指令。

## 问卷与数据

默认连续体验不弹问卷。需要本轮结束后填写六项 Raw NASA-TLX：

```powershell
& $py -X utf8 .\run.py game --ai high --enable --questionnaire
```

机器人先停止并关闭连接，再显示问卷。所有六项初始未作答，鼠标点选 0–100 分、步长 5；Enter 提交，Esc 保存未完成答案。Performance（表现）0=完美、100=失败；总分是六项的算术平均。

每局写入 `results/game/<时间_编号>/`：

```text
config_snapshot.json             # 本轮参数快照
experience_status.json           # AI 档位、是否启用、故障、完整行程数
game/*.events.jsonl              # 游戏观察、动作、交互事件与时间
game/*.summary.json              # 得分、订单、行为摘要、模型校验值
game/*.config.json               # 场景、控制者、元信息
game/episodes.csv                # 本次结果索引
ur5/*_transport.jsonl            # 负载映射、控制状态和推拉阶段
ur5/*_ur5_01.jsonl               # 实机采样、力、位置、速度与指令（实机激活后）
nasa_tlx.json                    # 请求问卷时生成，保存每项评分
```

独立三档实机轨迹写入 `results/ur5_three_levels_*.jsonl`。不启用实机或只预览界面时，没有伪造的机器人采样。
当前记录仍标记为连续体验，不包含旧批次的排程、额外尾段或休息协议。

## 离线查看与验证

```powershell
& $py -X utf8 .\run.py game --ai high --screenshot .\docs\game_preview.png
& $py -X utf8 .\run.py standalone --offline-ui --screenshot .\docs\standalone_preview.png
& $py -X utf8 .\run.py questionnaire --screenshot .\docs\tlx_preview.png
& $py -B -X utf8 -m unittest discover -s tests -q
```

界面预览与测试记录有相应标记。迁移对照结果见 [VALIDATION.md](docs/VALIDATION.md)。本次构建未连接 UR5/HEX，实机启动流程和手感需现场验证。

已有环境无需重新安装。新电脑可以使用 `pip install -e .` 安装工程和依赖，连接机器人另需 `ur-rtde`。模型、配置、vendor 图片资源均需要随本文件夹一起保留；以 `run.py` 为统一入口。
