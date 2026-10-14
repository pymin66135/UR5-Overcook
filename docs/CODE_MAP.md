# 代码怎么读

建议先看入口，再看流程，最后看算法。所有第一方模块都在 `src/ur5game/`。

1. `run.py`：选择 game、standalone、check 等入口。
2. `apps/realtime.py`：读配置、加载 AI、创建记录目录、组装游戏和机器人服务。
3. `game/application.py`：每帧处理键盘，推进游戏，发布游戏状态，绘制进度。
4. `robot/service.py`：后台控制线程。准备 → READY → F6 → 连接 → 同步界面 → ON；主线程只提交请求。
5. `robot/session.py`：连接、静止基线、激活、采样、算法调用、发送和停止。
6. `robot/admittance.py`：单纯导纳计算，输入 Sample，输出 Output；没有机器人通信。

## 调用关系

```text
run.py
 ├─ apps/standalone.py ───────────┐
 └─ apps/realtime.py              │
      ├─ game/application.py      │
      │    ├─ game/controls.py    │
      │    ├─ game/ai_partner.py  │
      │    ├─ game/renderer.py    │
      │    └─ records.py          │
      └─ robot/service.py        │
           ├─ interaction/       │
           └─ robot/session.py ◀─┘
                ├─ ur5_device.py + hex_sensor.py
                ├─ admittance.py
                └─ homing.py
```

## 每个文件负责什么

| 文件 | 职责 |
| --- | --- |
| `config.py` | 分组 JSON、LoadProfile（负载档位）、Limits（限制参数）、统一校验与路径解析 |
| `robot/types.py` | Sample（实测采样）、Output（算法输出），明确 m/rad/s/N 单位 |
| `robot/guards.py` | 数值策略及范围检查；当前完整策略写在配置中 |
| `robot/ur5_device.py` | Dashboard、RTDE 读取与速度发送；不处理游戏 |
| `robot/hex_sensor.py` | HEX UDP 请求、六维数据解码及坐标符号 |
| `robot/session.py` | 操作设备并执行控制算法的单次会话 |
| `robot/service.py` | 后台线程、UI 同步、F6/F7/H 请求、故障锁定和状态记录 |
| `robot/admittance.py` | X/Y 导纳积分、速度/加速度限制、边界减速、平滑变参 |
| `robot/homing.py` | 保存/读取固定起点，H 制动后主动归位 |
| `interaction/stroke_cycle.py` | 按实际位置切换 PUSH/到位停留/RETURN，统计完整行程 |
| `interaction/load_mapping.py` | 上一时间窗的持物移动量 → 下一时间窗的负载档位 |
| `game/environment.py` | 地图、物品、烹饪、订单、交互和行为事件 |
| `game/controls.py` | 键盘、配方选择、暂停、固定步长时钟 |
| `game/policy.py` | 原模型网络结构、原观测编码、原动作转换 |
| `game/ai_partner.py` | 校验模型、加载只读权重、依据观察选择动作 |
| `game/renderer.py` | 原游戏画面和机器人进度面板 |
| `records.py` | 游戏事件、配置快照、摘要、CSV 索引 |
| `questionnaire/nasa_tlx.py` | 六项问卷、显式作答、评分和文件保存 |

`RobotService` 不再继承旧的 `TransportBridge`。时间窗负载映射与行程切换各自独立，机器人速度采样和发送在一个线程中串行执行。

同一组导纳参数同时供独立窗口和游戏使用。游戏负责选择负载档位，不直接发送机器人速度。

## 从旧文件迁入的位置

| 原文件 | 新位置 |
| --- | --- |
| `ur5_pilot/control.py` | `config.py` + `robot/types.py` + `robot/admittance.py` |
| `ur5_pilot/hardware.py` | `config.py` + `robot/ur5_device.py` + `robot/session.py` |
| `ur5_pilot/game_bridge.py` | `robot/service.py` + `interaction/stroke_cycle.py` + `game/renderer.py` |
| `ur5_pilot/bridge.py` 的负载映射 | `interaction/load_mapping.py` |
| `forced_coop/__main__.py` | `game/application.py` + `game/controls.py` + `records.py` |
| `rl.py` 和 `ppo_runtime.py` 的推理部分 | `game/policy.py` + `game/ai_partner.py` |
| `experiment_ui.py`/`experiment_store.py` 的 TLX 部分 | `questionnaire/nasa_tlx.py` |

`vendor/` 是保留许可证的原 Overcooked 运行依赖与图片字体。一般无需修改。
`tools/export_models.py` 是一次性迁移工具：读取原工程经过校验的本地模型，生成权重和模型说明。正常运行不调用它。
