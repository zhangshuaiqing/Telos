# Telos 架构文档

> **Telos** (τέλος) — 希腊语"终极目的"。从感知到行动的完整闭环。

---

## 设计哲学

```
                    ┌───────────────────────────────────────────┐
                    │              云端 (Cloud)                  │
                    │                                           │
                    │  ┌─────────────────────────────────────┐  │
                    │  │         认知引擎 (Cognition)         │  │
                    │  │                                     │  │
                    │  │  Observation → LLM → Decision       │  │
                    │  │  场景理解 · 任务规划 · 反思评估      │  │
                    │  │                                     │  │
                    │  │  全部由 DeepSeek/Kimi API 驱动       │  │
                    │  └─────────────────────────────────────┘  │
                    └──────────────┬────────────────────────────┘
                                   │ HTTPS
                    ┌──────────────┴────────────────────────────┐
                    │            端侧 (Edge CPU)                  │
                    │                                            │
                    │  ┌──────────┐ ┌──────────┐ ┌──────────┐   │
                    │  │  视觉    │ │  语音    │ │ 本体感   │   │
                    │  │ Vision   │ │ Voice    │ │Proprio   │   │
                    │  │ Pipeline │ │ Pipeline │ │ Pipeline │   │
                    │  └────┬─────┘ └────┬─────┘ └────┬─────┘   │
                    │       └────────────┼────────────┘          │
                    │                    ▼                       │
                    │  ┌─────────────────────────────────────┐  │
                    │  │        PerceptionManager             │  │
                    │  │   统一 Observation → 认知引擎         │  │
                    │  └─────────────────────────────────────┘  │
                    │                    │                       │
                    │  ┌─────────────────┴───────────────────┐  │
                    │  │          Agent Loop (Telos)          │  │
                    │  │                                     │  │
                    │  │  1. 感知 → observe()                 │  │
                    │  │  2. 记忆 → 更新工作/情景记忆          │  │
                    │  │  3. 认知 → 云端 LLM 推理             │  │
                    │  │  4. 决策 → 安全校验 → 原语调用        │  │
                    │  │  5. 执行 → STM32 → 硬件              │  │
                    │  └─────────────────┬───────────────────┘  │
                    └────────────────────┼──────────────────────┘
                                         │ UART/SPI
                    ┌────────────────────┴──────────────────────┐
                    │             STM32 (实时小脑)               │
                    │                                           │
                    │  接收原语指令 → PID → VESC/舵机/泵/...     │
                    │  传感器采集 → IMU/编码器 → 状态回传        │
                    │  硬件安全 → 急停/倾覆/看门狗               │
                    └───────────────────────────────────────────┘
```

**核心原则：**
1. **完整闭环** — 感知→认知→执行→回馈，不做切割
2. **低算力优先** — 端侧只做轻量采集和规则安全，重推理走云端 API
3. **可扩展** — 新感知通道、新执行器只需实现统一接口，不改架构
4. **安全分层** — 云端: 推理建议 | 端侧: 安全校验 | STM32: 硬件急停

---

## 1. 感知层 — 多模态统一接口

```python
class PerceptionChannel(Protocol):
    """所有感知通道实现此接口"""
    name: str
    priority: int    # 采集优先级

    def start(self) -> bool
    def stop(self) -> None
    def capture(self) -> dict
    def health(self) -> dict
```

**内置通道：** Vision / Voice / Proprioception  
**扩展通道：** 只需写一个新类并 `register()`，无需改动架构

---

## 2. 认知层 — 云端 LLM 推理

```
Observation → [场景理解] → [任务规划] → [动作决策] → ActionSequence
```

- 场景理解：多模态原始数据 → 结构化语义描述
- 任务规划：目标 → 子目标分解 → 原语序列
- 动作决策：原语序列 → 带参数的具体指令

---

## 3. 执行层 — 7 种原语

| 原语 | 控制方式 | 硬件示例 |
|------|---------|---------|
| `RotaryVelocity` | 连续旋转速度 | VESC、DC电机 |
| `RotaryPosition` | 角位置定位 | 舵机、步进电机 |
| `LinearPosition` | 直线位置控制 | 电动推杆、丝杆 |
| `BinaryActuator` | 开关控制 | 电磁阀、继电器、电磁铁 |
| `Pump` | 流量/压力控制 | 水泵、真空泵、气泵 |
| `Gripper` | 抓取+力反馈 | 平行夹爪、自适应手指 |
| `EnergyBeam` | 定向能量输出 | 激光、红外、紫外 |

**组合规则：** 任意执行器 = 上述原语的自由组合

---

## 4. 通信协议 — 端 ↔ 云 ↔ STM32

```
云端 ← HTTP POST (JSON + Base64 图像) → 端侧CPU
端侧CPU ← UART/SPI (紧凑二进制) → STM32
端侧CPU ← MQTT (WiFi) → ESP32 → 传感器
```

---

## 5. 数据流 — 一个完整循环

```
1. 感知采集: 摄像头帧 + 麦克风音频 + IMU/编码器
2. 感知处理: 压缩图像 → Base64, VAD→ASR→文本
3. 认知推理: Observation → 云端LLM → {"action_sequence": [...原语...]}
4. 安全校验: 速度上限? 前方障碍? 能量安全?
5. 执行下发: 原语指令 → STM32 → PID闭环 → 硬件
6. 状态回传: STM32 → 端侧 → 更新记忆 → 进入下一循环
```

---

## 6. 记忆系统

| 记忆类型 | 存储位置 | 生命周期 | 内容 |
|---------|---------|---------|------|
| 工作记忆 | 内存 `dict` | 当前任务 | 目标、上下文、最近动作 |
| 情景记忆 | SQLite | 会话内 | 轨迹日志 (时间戳+状态+动作) |
| 对话历史 | 内存 `list` | 会话内 | LLM 多轮对话上下文 |

---

## 7. 项目结构

```
telos/
├── telos/
│   ├── __init__.py
│   ├── agent.py              # TelosAgent 主循环
│   ├── observation.py        # Observation / PerceptionChannel 接口
│   ├── perception/
│   │   ├── __init__.py
│   │   ├── manager.py        # PerceptionManager
│   │   ├── vision.py         # 视觉通道
│   │   ├── voice.py          # 语音通道 (ASR输入 + TTS输出)
│   │   └── proprio.py        # 本体感通道
│   ├── cognition/
│   │   ├── __init__.py
│   │   └── engine.py         # 认知引擎 (云端LLM)
│   ├── actuators/
│   │   ├── __init__.py
│   │   ├── base.py           # Actuator 统一接口
│   │   ├── rotary_velocity.py
│   │   ├── rotary_position.py
│   │   ├── linear_position.py
│   │   ├── binary.py
│   │   ├── pump.py
│   │   ├── gripper.py
│   │   ├── energy_beam.py
│   │   └── registry.py       # 执行器注册与发现
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── working.py        # 工作记忆
│   │   └── episodic.py       # 情景记忆 (SQLite)
│   ├── comm/
│   │   ├── __init__.py
│   │   └── stm32.py          # STM32 UART 通信
│   └── utils/
│       ├── __init__.py
│       └── config.py         # 配置管理
├── docs/
│   └── architecture.md       # 本文档
├── tests/
├── main.py                   # 入口
├── pyproject.toml
└── README.md
```

---

## 8. 安全分层

| 层 | 位置 | 机制 | 延迟 |
|------|------|------|------|
| 云端 | LLM | 推理时的安全约束 prompt | — |
| 端侧 | Agent Loop | 执行前规则校验 (速度/范围/能量上限) | <1ms |
| STM32 | 固件 | 硬件急停、看门狗、倾覆检测 | <100μs |
