# Telos 架构文档

> **Telos** (τέλος) — 希腊语"终极目的"。从感知到行动的完整闭环 Agent。

---

## 设计哲学

```
                         ┌─────────────────────────────────────┐
                         │         认知层 (Cognition)           │
                         │                                     │
                         │  当前: DeepSeek/Kimi API             │
                         │  进化: 经验 → 反思 → 策略改进 (RL)   │
                         │  未来: Jetson Orin 本地部署模型       │
                         └──────────────┬──────────────────────┘
                                        │ HTTPS
     ┌──────────────────────────────────┼──────────────────────┐
     │                         端侧 (Edge CPU)                  │
     │                                                          │
     │  ┌──────────────────────────────────────────────┐       │
     │  │         感知层 (Perception — 可扩展)          │       │
     │  │                                              │       │
     │  │  ☑ 视觉  ☑ 语音  ☑ 本体感                   │       │
     │  │  ☐ 激光雷达  ☐ 力反馈  ☐ 热成像  ☐ ...       │       │
     │  └──────────────────┬───────────────────────────┘       │
     │                     ▼                                   │
     │  ┌──────────────────────────────────────────────┐       │
     │  │          记忆系统 (Memory)                     │       │
     │  │                                              │       │
     │  │  感觉缓冲(ms) → 工作记忆(秒-分) → 情景记忆(时)│       │
     │  │          └→ 程序记忆/技能库 (永久)             │       │
     │  └──────────────────┬───────────────────────────┘       │
     │                     ▼                                   │
     │  ┌──────────────────────────────────────────────┐       │
     │  │          认知引擎 (Cognition Engine)           │       │
     │  │  Prompt构建 → API调用 → 决策解析 → 反思学习   │       │
     │  └──────────────────┬───────────────────────────┘       │
     │                     ▼                                   │
     │  ┌──────────────────────────────────────────────┐       │
     │  │          Agent 状态机 (混合模式)               │       │
     │  │  底层: IDLE/EXECUTING/EMERGENCY/SPEAKING      │       │
     │  │  上层: 每步推理，灵活决策                      │       │
     │  └──────────────────┬───────────────────────────┘       │
     │                     ▼                                   │
     │  ┌──────────────────────────────────────────────┐       │
     │  │         执行层 (Actuators — 7原语, 可扩展)    │       │
     │  │  RotaryVel│RotaryPos│Linear│Binary│Pump│Grip│ │       │
     │  │  EnergyBeam│ ☐ 未来原语                       │       │
     │  └──────────────────┬───────────────────────────┘       │
     │                     │ UART/SPI                          │
     └─────────────────────┼───────────────────────────────────┘
                           │
     ┌─────────────────────┼───────────────────────────────────┐
     │      STM32 (实时小脑) + ESP32 (通信网关)                 │
     │                                                          │
     │  STM32: PID闭环 · IMU解算 · 硬件急停 · 看门狗            │
     │  ESP32: WiFi MQTT · OTA · 辅助传感器                    │
     │                                                          │
     │  ☐ 电源系统: 太阳能充电 · 电池管理 (后续讨论)            │
     └──────────────────────────────────────────────────────────┘
```

---

## 核心决策记录

| # | 决策点 | 选择 | 理由 |
|------|------|------|------|
| 1 | LLM 延迟期间 | 保持当前动作继续执行 | 防止机器人频繁停顿 |
| 2 | 观测触发策略 | 固定间隔(500ms) + 关键事件即时触发 | 兼顾效率与响应 |
| 3 | 语音交互 | 持续监听，随时打断 | 类语音助手体验 |
| 4 | 多模态融合 | 图片(vision message) + 文本(context) | 充分利用 VL 模型能力 |
| 5 | LLM 错误处理 | 本地安全接管 + 语音播报 + 周期重试 | 安全优先，尽力恢复 |
| 6 | Agent 状态机 | 混合模式：底层状态 + 上层灵活推理 | 安全可控 + 认知灵活 |

---

## 1. 感知层 — 多模态统一接口

### 1.1 核心抽象

```python
class PerceptionChannel(Protocol):
    name: str           # "vision" | "voice" | "proprio" | "lidar" | ...
    priority: int       # 采集优先级 (数字越小越早采集)

    def start(self) -> bool
    def stop(self) -> None
    def capture(self) -> dict
    def health(self) -> dict
```

### 1.2 内置通道

| 通道 | 输入 | 输出 | 频率 | 说明 |
|------|------|------|------|------|
| Vision | 摄像头帧 (RGB 640×480) | Base64 JPEG + 分辨率 | 30fps 采集, 按需发送 | 不本地推理 |
| Voice | 麦克风音频流 | ASR 识别文本 | 持续 VAD | 唤醒词可选 |
| Proprio | IMU + 编码器 + 电池 | 速度/航向/倾角/电量/电机状态 | 100Hz (STM32回传) | 直接从STM32刷新 |

### 1.3 扩展通道 (未来)

| 通道 | 硬件 | 使用场景 |
|------|------|---------|
| Lidar | RPLidar / 激光雷达 | 室内SLAM、精确避障 |
| Force | 力传感器 / 电流估计 | 采摘力控、碰撞检测 |
| Thermal | MLX90640 红外阵列 | 夜巡、设备过热检测 |
| GPS/RTK | GNSS 模块 | 室外农田厘米级定位 |
| Gas | VOC/CO₂/湿度传感器 | 环境监测 |

### 1.4 多模态融合策略

```
视觉 (图片)        → 作为 Vision Message → 直接发送 VL 模型 (Base64)
语音 (文本)        → 作为 User Message → 拼接到对话上下文
本体感 (结构数据)   → 作为 Context → 拼接到系统状态段
其他模态 (未来)     → 转文本描述 → 拼接到 Context
```

**发送决策：** 固定 500ms 周期采样 + 关键事件（障碍物/碰撞/语音指令/倾覆）立即触发。

---

## 2. 记忆系统 — 认知科学启发

### 2.1 四级记忆模型

```
时间尺度
  │
  │  ┌─────────────────────────────────────────────┐
  │  │            感觉缓冲 (Sensory Buffer)          │
  │  │  保留: ms 级                                 │
  │  │  内容: 原始感知帧 (最新一帧图像/音频/IMU)     │
  │  │  实现: 内存环形缓冲区, 1-3 帧                │
  │  └─────────────────┬───────────────────────────┘
  │                    ▼
  │  ┌─────────────────────────────────────────────┐
  │  │            工作记忆 (Working Memory)          │
  │  │  保留: 秒 ~ 分钟                             │
  │  │  内容: 当前任务上下文、最近N个动作、当前目标  │
  │  │  容量: 7±2 个信息块                          │
  │  │  实现: 内存 dict, 键值对 + 时间戳            │
  │  └─────────────────┬───────────────────────────┘
  │                    ▼
  │  ┌─────────────────────────────────────────────┐
  │  │            情景记忆 (Episodic Memory)         │
  │  │  保留: 小时 ~ 天                             │
  │  │  内容: 任务轨迹 (时间+状态+动作+结果)        │
  │  │  实现: SQLite 持久化, 支持按任务/时间查询     │
  │  └─────────────────┬───────────────────────────┘
  │                    ▼
  │  ┌─────────────────────────────────────────────┐
  │  │            程序记忆 (Procedural Memory)       │
  │  │  保留: 永久                                   │
  │  │  内容: 成功策略/技能/校准参数/优化后的Prompt  │
  │  │  实现: JSON 技能库 + 向量索引 (未来)          │
  └─────────────────────────────────────────────────┘
```

### 2.2 与认知科学的对应

| 认知概念 | Telos 实现 | 依据 |
|---------|-----------|------|
| 感觉记忆 (Sperling, 1960) | SensoryBuffer: 环形缓冲 1-3 帧 | 视觉感觉记忆 ~300ms |
| 工作记忆 (Baddeley, 2000) | WorkingMemory: 7±2 槽位 | 经典容量限制 |
| 情景记忆 (Tulving, 1972) | EpisodicMemory: SQLite 轨迹 | 时间+地点+事件 |
| 程序记忆 (Anderson, 1983) | ProceduralMemory: 技能 JSON | 习得技能自动执行 |
| 记忆巩固 (McClelland, 1995) | 成功轨迹 → 提取策略 → 写入程序记忆 | 海回体→皮层迁移类比 |

---

## 3. 认知层 — LLM + 强化学习

### 3.1 Prompt 工程

```
System Prompt:
├── 角色定义: "你是机器人 Telos 的认知核心..."
├── 能力清单: 从 Executor.get_capabilities() 动态注入
├── 输出格式: JSON {thought, action_type, actions:[...]}
├── 安全约束: 速度上限、能量上限、禁止动作
└── 反思提示: "回顾最近的动作，是否有改进空间?"

User Message (每次推理):
├── 任务: 当前目标任务描述
├── 状态: 从 Observation.to_prompt_text() 生成
├── 记忆: 工作记忆摘要 + 上次反思结论
└── [可选] 图片: vision message (Base64 JPEG)
```

### 3.2 推理输出格式

```json
{
  "thought": "前方5米有蓝色障碍物，需要右转绕行...",
  "action_type": "move|speak|actuate|wait|ask",
  "actions": [
    {"actuator": "left_motor", "action": "set_speed", "params": {"rpm": 500}},
    {"actuator": "right_motor", "action": "set_speed", "params": {"rpm": 300}}
  ]
}
```

### 3.3 LLM 延迟处理

LLM 调用期间（200-500ms）：
- **动作**: 保持当前动作持续执行
- **安全**: STM32 + 端侧规则继续监控
- **标记**: Agent 状态 = EXECUTING, 子标记 = WAITING_LLM
- **超时**: 30s 无响应 → 降级为本地规则 + 语音播报

### 3.4 自我进化 — 强化学习循环

```
             ┌── 执行任务 ──────────────────┐
             │                              │
             ▼                              │
    ┌────────────────┐                      │
    │  任务完成/失败  │                      │
    └───────┬────────┘                      │
            ▼                               │
    ┌────────────────┐        ┌──────────┐  │
    │  反思评估       │───────▶│ 存入情景  │  │
    │  LLM 回顾轨迹   │        │ 记忆     │  │
    └───────┬────────┘        └──────────┘  │
            ▼                               │
    ┌────────────────┐                      │
    │  提取改进策略   │                      │
    │  "下次遇到X，   │                      │
    │   应该先做Y"    │                      │
    └───────┬────────┘                      │
            ▼                               │
    ┌────────────────┐                      │
    │  更新程序记忆   │                      │
    │  调整 Prompt /  │                      │
    │  参数 / 阈值    │                      │
    └───────┬────────┘                      │
            │                               │
            └── 执行下一个任务 ──────────────┘
```

**进化内容：**
- Prompt 优化：将成功策略作为 few-shot 示例注入
- 参数调优：调整安全阈值、动作参数
- 技能积累：成功动作序列存入程序记忆，下次直接复用

---

## 4. 执行层 — 7 种原语 (可扩展)

### 4.1 统一接口

```python
class Actuator(Protocol):
    name: str
    state: ActuatorState  # idle | busy | error | disconnected

    def init(self) -> bool
    def get_capability(self) -> ActuatorCapability
    def get_state(self) -> dict
    def emergency_stop(self) -> None
```

### 4.2 7 种原语

| # | 原语 | 控制量 | 硬件示例 | 可扩展动作 |
|------|------|------|------|------|
| 1 | `RotaryVelocity` | 转速 (rpm) | VESC、DC电机 | set_speed, stop |
| 2 | `RotaryPosition` | 角度 (°) | 舵机、步进 | set_angle |
| 3 | `LinearPosition` | 位置 (mm) | 推杆、丝杆 | set_position |
| 4 | `BinaryActuator` | 开/关 | 电磁阀、继电器 | on, off, toggle |
| 5 | `Pump` | 流量 (L/min) | 水泵、气泵 | set_flow, stop |
| 6 | `Gripper` | 抓取/释放 | 夹爪 | grasp, release |
| 7 | `EnergyBeam` | 功率 (W) | 激光、红外 | fire, stop |

**组合规则：** 任意复杂执行器 = 上述原语的自由组合。如 "喷雾杆" = RotaryPosition×2 + Pump + BinaryActuator。

### 4.3 新增原语流程

1. 实现 `Actuator` 接口
2. 添加该原语的安全约束
3. 注册到 `Executor`
4. 自动在下次 Prompt 中注入能力描述

---

## 5. Agent 主循环

### 5.1 状态机

```
                    ┌─────────┐
            ┌──────▶│  IDLE   │◀──────────┐
            │       └────┬────┘           │
            │            │ 收到任务        │
            │            ▼                │
            │       ┌─────────┐           │
            │       │EXECUTING│           │
            │       └────┬────┘           │
            │    ┌───────┼───────┐        │
            │    ▼       ▼       ▼        │
            │ ┌─────┐┌──────┐┌───────┐   │
            │ │移动 ││操作  ││等待LLM│   │
            │ └──┬──┘└──┬───┘└───┬───┘   │
            │    │       │       │       │
            │    └───────┼───────┘       │
            │            ▼               │
            │       ┌──────────┐         │
            │       │ SPEAKING │─────────┤ (说完回到 EXECUTING)
            │       └──────────┘         │
            │                            │
            │       ┌──────────┐         │
            └───────│EMERGENCY │─────────┘ (恢复后回到 IDLE)
                    └──────────┘
                       ▲   ▲
                       │   │
            STM32急停 ─┘   └── 端侧无法处理
```

### 5.2 主循环伪代码

```python
class TelosAgent:
    def step(self) -> None:
        # 1. 感知 — 按优先级采集所有通道
        obs = self.perception.observe()

        # 2. 更新感觉缓冲 + 工作记忆
        self.memory.sensory_buffer.write(obs)
        self.memory.working_memory.update(obs)

        # 3. 判断是否需要发送给 LLM
        if self._should_send_to_llm(obs):
            # 4. 构建 Prompt → 调用 LLM
            decision = self.cognition.think(obs, task, capabilities, memory)

            # 5. 安全检查
            decision = self.safety.validate(decision)

            # 6. 执行动作序列
            for action in decision.actions:
                result = self.executor.execute(action)

            # 7. 记录情景记忆
            self.memory.episodic.record(step, decision, results)

            # 8. 如果任务结束，触发反思
            if task_complete or task_failed:
                self._reflect_and_learn()

    def _should_send_to_llm(self, obs) -> bool:
        """500ms 间隔 || 关键事件"""
        return (
            (now - self._last_llm_call > 500ms) or
            obs.has_obstacle() or
            obs.has_voice_command() or
            obs.has_collision()
        )
```

### 5.3 频率表

| 循环 | 频率 | 执行内容 |
|------|------|---------|
| STM32 控制 | 100-1000 Hz | PID 闭环、IMU 解算、硬件急停 |
| 端侧感知 | 30-100 Hz | 传感器采集、状态刷新 |
| 端侧安全 | 100 Hz | 规则校验（速度/倾角/障碍） |
| LLM 推理 | ~2 Hz (500ms间隔) | 场景理解 + 动作规划 |
| 反思学习 | 每任务结束 | 轨迹回顾 + 策略提取 |
| 语音 VAD | 持续 | 检测人声 → ASR |

---

## 6. 安全系统

### 6.1 三层防御

| 层级 | 位置 | 机制 | 延迟 | 职责 |
|------|------|------|------|------|
| L1 云端 | LLM Prompt | 安全约束注入 | — | 禁止不安全动作的生成 |
| L2 端侧 | Agent Loop | 速度/能量/范围校验 | <1ms | 拦截 LLM 的不安全输出 |
| L3 STM32 | 固件 | 急停 · 看门狗 · 倾覆检测 | <100μs | 最后的物理安全底线 |

### 6.2 LLM 故障处理

```
LLM API 调用失败/超时 →
  1. 立即: 切换到本地安全规则 (保持安全、停止危险动作)
  2. 语音: "API 请求超时，正在重试..."
  3. 重试: 指数退避 (1s → 2s → 4s → 8s)
  4. 恢复: API 恢复后回到正常循环
  5. 持久故障: 降级为纯本地规则模式，持续报警
```

### 6.3 STM32 急停矩阵

| 条件 | 动作 | 恢复方式 |
|------|------|---------|
| 前方 TOF < 30cm | 立即刹车 | 障碍消失后自动恢复 |
| 倾角 > 45° | 全部断电 | 手动复位 |
| 看门狗超时 | 系统复位 | 自动重启 |
| 电机过流 | 停止该电机 | 30s冷却后重试 |
| 急停按钮 | 全部断电 | 按钮复位 |

---

## 7. 通信协议

### 7.1 端侧 ↔ 云端

```
POST /v1/chat/completions (OpenAI 兼容)
Headers: Authorization: Bearer <API-KEY>
Body: {
  "model": "deepseek-chat",
  "messages": [
    {"role": "system", "content": "<能力清单+安全约束>"},
    {"role": "user", "content": [
      {"type": "text", "text": "<任务+状态+记忆>"},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
    ]}
  ]
}
```

### 7.2 端侧 ↔ STM32

```
帧格式 (紧凑二进制):
┌──────┬──────┬──────────┬──────────┬──────┐
│SYNC  │ ID   │ CMD      │ PAYLOAD  │ CRC  │
│0xAA  │ 1B   │ 1B       │ N bytes  │ 2B   │
└──────┴──────┴──────────┴──────────┴──────┘

ID:   0x01=左电机, 0x02=右电机, 0x03=舵机1, ...
CMD:  0x10=速度控制, 0x20=位置控制, 0x30=急停, 0xFF=状态查询
```

---

## 8. 硬件演进

| 阶段 | 硬件 | 能力 |
|------|------|------|
| **当前** | STM32 + ESP32 + CPU + 云端 API | 远程推理、轻量本地安全 |
| **未来** | + Jetson Orin | 本地视觉推理(YOLO/SLAM)、本地小 LLM(3B-8B)、端云混合 |

---

## 9. 项目结构

```
telos/
├── telos/
│   ├── agent.py              # TelosAgent: 主循环 + 状态机
│   ├── observation.py        # Observation / PerceptionChannel 接口
│   ├── perception/
│   │   ├── manager.py        # PerceptionManager: 多通道汇集
│   │   ├── vision.py         # 视觉通道 (摄像头→Base64)
│   │   ├── voice.py          # 语音通道 (ASR输入 + TTS输出)
│   │   └── proprio.py        # 本体感通道 (IMU/编码器/电池)
│   ├── cognition/
│   │   └── engine.py         # 认知引擎 (Prompt → LLM → 决策)
│   ├── actuators/
│   │   ├── base.py           # Actuator / Executor 接口
│   │   └── primitives.py     # 7 种原语实现
│   ├── memory/
│   │   └── memory.py         # 感觉缓冲 / 工作记忆 / 情景记忆 / 程序记忆
│   ├── task/
│   │   └── task_manager.py   # 任务分解 / DAG 调度 / 子任务生命周期
│   ├── safety/
│   │   └── safety.py         # 三层安全校验 + 降级管理
│   ├── spatial/
│   │   ├── costmap.py        # 局部代价地图 (实时避障)
│   │   └── topology.py       # 拓扑 + 语义地图 (全局导航)
│   ├── power/
│   │   └── power_manager.py  # 电源管理 / 太阳能充电 / 电量策略
│   ├── interaction/
│   │   ├── approval.py       # 审批机制 (危险动作确认)
│   │   └── explain.py        # 行为解释 (可解释AI)
│   ├── integration/
│   │   └── home_assistant.py # 外部系统集成
│   ├── comm/
│   │   └── stm32.py          # STM32 二进制通信协议
│   ├── observability/
│   │   ├── logger.py         # 分层日志系统
│   │   └── metrics.py        # 关键指标采集
│   └── utils/
│       └── config.py         # 配置管理
├── sim/
│   └── environment.py        # 仿真环境统一接口
├── docs/
│   ├── architecture.md       # 本文档 (总体架构 — 20 章)
│   ├── actuators.md          # 7 原语详细规格 (+ 扩展机制)
│   ├── perception.md         # 感知通道详细规格
│   ├── cognition.md          # Prompt 工程 + RL 循环
│   ├── tasks.md              # 任务系统设计
│   ├── safety.md             # 安全与错误处理设计
│   ├── protocol.md           # 通信协议规范
│   ├── extending.md          # 扩展指南
│   └── deployment.md         # 部署指南
├── main.py
├── pyproject.toml
└── README.md
```

---

## 10. 任务系统

### 10.1 为什么需要任务系统

当前 `AgentConfig.task = "探索环境"` 只是一段字符串。真实场景中，复杂任务（如"去B区给西红柿喷药"）LLM 无法一步完成——它不知道 B 区在哪、不知道西红柿长什么样、不知道喷药需要多少流量。任务系统负责**分解、调度、跟踪**，让 LLM 专注于"当前子目标"的决策。

### 10.2 任务层次结构

```
用户指令: "去B区给西红柿喷药"
     │
     ▼
┌──────────────────────────────┐
│        任务分解 (LLM)         │
│                              │
│  1. 导航到B区                 │
│  2. 在B区扫描识别西红柿        │
│  3. 对每株西红柿喷洒农药       │
│  4. 报告完成                  │
└──────────┬───────────────────┘
           │
           ▼
┌──────────────────────────────┐
│       子任务 DAG              │
│                              │
│  [导航B区] ──→ [扫描B区]     │
│                  │           │
│                  ▼           │
│             [喷洒] ←── 循环   │
│                  │           │
│                  ▼           │
│             [报告]           │
└──────────────────────────────┘
```

### 10.3 子任务数据结构

```python
@dataclass
class SubTask:
    id: str
    description: str          # "在B区扫描识别西红柿"
    status: TaskStatus        # pending | running | done | failed | cancelled
    dependencies: list[str]   # 依赖的前置子任务ID
    priority: int             # 数字越小越优先
    retry_count: int = 0
    max_retries: int = 3

    # 完成条件 — 可被验证
    completion_criteria: dict  # {"type": "scan_and_detect", "target": "tomato"}

    # 失败时的回退
    on_fail: str              # "skip" | "retry" | "abort" | "ask_user"

@dataclass
class Task:
    id: str
    user_command: str          # 原始用户指令
    subtasks: list[SubTask]
    status: TaskStatus
    created_at: float
```

### 10.4 任务生命周期

```
用户指令
  │
  ▼
LLM 分解 → 生成子任务 DAG
  │
  ▼
任务队列 (优先级排序)
  │
  ├── 取最高优先级的 pending 子任务
  │     │
  │     ▼
  │  子任务作为"当前目标"注入 LLM Context
  │     │
  │     ▼
  │  Agent 循环执行 → 检查 completion_criteria
  │     │
  │     ├── 满足 → 标记 done → 触发下一个子任务
  │     │
  │     └── 不满足 → 重试 (最多 max_retries 次)
  │                    │
  │                    └── 耗尽 → on_fail 策略
  │
  ▼
全部子任务 done → 任务完成 → 语音报告
```

### 10.5 中断与抢占

- **中断**: 更高优先级任务入队时（用户说"停！先去做X"），当前子任务挂起，保存状态，切换到新任务
- **抢占**: 当前子任务被打断后，恢复时从挂起点继续（依赖情景记忆中的轨迹状态）
- **安全中断最高优先级**: EMERGENCY 状态直接清除任务队列

### 10.6 与 LLM 的接口

任务系统每次只向 LLM 暴露**当前子任务**的上下文，而不是整个 DAG：

```
System Prompt 注入:
  当前子任务: "扫描B区找到所有西红柿"
  进度: 子任务 2/4
  前置条件: [导航B区 ✓]
  完成标准: 识别到 ≥3 株西红柿
  
User Message 注入:
  任务: 扫描B区找到所有西红柿
  状态: <Observation>
```

这样 LLM 每次只需要聚焦一个具体目标，不需要理解全局 DAG。

---

## 11. 错误处理体系

### 11.1 设计原则

> **错误是常态，不是异常。** 机器人运行在不可控的物理世界中，传感器会坏、网络会断、电机会堵转。系统必须在设计阶段就假设每一个组件都可能以各种方式出错。

### 11.2 错误分类

| 类别 | 示例 | 检测方式 | 恢复策略 |
|------|------|---------|---------|
| **传感器错误** | 摄像头断连、TOF 读取超时、IMU 漂移 | 超时/校验和/合理性检查 | 降级运行、标记故障传感器 |
| **执行器错误** | 电机堵转、舵机卡死、泵空转 | 电流异常(>额定2x)/速度反馈异常 | 停该执行器、语音报警、尝试反向释放 |
| **通信错误** | STM32 无响应、WiFi 断连、API 超时 | 超时/ACK 缺失/序列号跳跃 | 重试→降级→安全停止 |
| **感知错误** | ASR 误识别、视觉目标误判 | 置信度阈值、时序一致性检查 | 请求确认、使用上下文纠正 |
| **资源错误** | 电量低于20%、存储满、温度过高 | 阈值监控、趋势预测 | 低电量返航、清理旧日志、散热 |
| **逻辑错误** | 动作序列矛盾、LLM 输出格式错误 | 输出校验、JSON 解析失败 | 拒绝执行、请求重新生成 |

### 11.3 错误处理流程

```
错误发生
  │
  ▼
┌──────────────────┐
│ 1. 检测 & 分类   │ ← 每个组件都有 health() 方法
└────────┬─────────┘
         ▼
┌──────────────────┐
│ 2. 隔离          │ ← 错误只影响该组件，不传播
│   actuator.set   │    其他执行器继续运行
│   _error_flag()  │
└────────┬─────────┘
         ▼
┌──────────────────┐
│ 3. 恢复尝试      │
│  Level 1: 自动重试│ ← 3次，指数退避
│  Level 2: 自我修复│ ← 电机反转释放堵转/重启传感器
│  Level 3: 降级    │ ← 失去视觉→改用超声+本体感
│  Level 4: 安全停止│ ← 无法降级→语音报警+等待指令
└────────┬─────────┘
         ▼
┌──────────────────┐
│ 4. 记录 & 学习   │ ← 错误类型+上下文写入情景记忆
│  供 RL 反思分析   │    下次遇到类似场景提前预警
└──────────────────┘
```

### 11.4 组件健康矩阵

每个组件必须实现 `health()` 方法：

```python
class ComponentHealth:
    status: HealthStatus    # healthy | degraded | failed
    error_count: int
    last_error: Optional[str]
    last_success: float     # 上次成功操作的时间戳
    self_check_results: dict  # 各项自检的详细结果

# 每个 Actuator / PerceptionChannel / Comm 都必须实现
def health(self) -> ComponentHealth: ...
```

### 11.5 ASR 误识别的特殊处理

语音指令误识别可能导致危险动作。需要双重确认：

```python
class VoiceChannel:
    def capture(self) -> dict:
        text = asr_result

        # 危险指令检测
        if any(word in text for word in ["急停", "停止", "关", "救命"]):
            return {"text": text, "is_critical": True, "confirmed": True}

        # 普通指令 — 低置信度时请求确认
        if confidence < 0.7:
            TTS.speak(f"你刚才说的是 '{text}' 吗？")
            return {"text": text, "needs_confirmation": True}

        return {"text": text}
```

---

## 12. 初始化与自检系统

### 12.1 为什么需要

机器人不是按下开关就能用的。上电后：执行器不在零位、IMU 需要标定、STM32 需要握手、能力清单需要构建。没有系统的启动流程，机器人会在错误的状态下开始执行任务。

### 12.2 启动序列

```
┌──────────────────────────────────────────────────────────────┐
│                     启动自检序列 (Boot Sequence)              │
│                                                              │
│  Phase 1: 通信建立 (100ms)                                    │
│    STM32 握手 → 读取固件版本 → 确认通信链路                   │
│    ESP32 握手 → 确认 WiFi 连接 → 获取 IP                      │
│                                                              │
│  Phase 2: 传感器自检 (500ms)                                   │
│    摄像头 → 采集测试帧 → 检查亮度/对比度                       │
│    IMU → 读取静止数据 → 零偏标定                               │
│    TOF → 测试读数 → 检查范围                                   │
│    麦克风 → 录音静音片段 → 检查噪声底                         │
│                                                              │
│  Phase 3: 执行器自检 (1-3s)                                    │
│    每个执行器依次执行:                                         │
│      init() → 回零点 → 微动测试 → 检测电流 → 回零              │
│    motor:  1° 微动 → 电流正常? → 回零                          │
│    servo:  小角度摆动 → 确认响应 → 回零                        │
│    pump:   短时脉冲(100ms) → 电流正常? → 停止                  │
│    laser:  仅电路自检，不发射 (安全!)                           │
│                                                              │
│  Phase 4: 能力清单构建                                         │
│    收集所有通过自检的执行器 → 生成能力列表                       │
│    收集所有通过自检的传感器 → 生成感知清单                      │
│    确定运行模式: full / degraded (部分组件失败)                  │
│                                                              │
│  Phase 5: 就绪声明                                             │
│    语音播报: "系统初始化完成，3 个执行器，4 个传感器就绪"       │
│    检测到故障: "警告: 右电机自检失败，已禁用。建议检查。"       │
│    状态: IDLE，等待任务                                        │
└──────────────────────────────────────────────────────────────┘
```

### 12.3 降级运行

如果部分组件自检失败，系统进入降级模式：

```python
class DegradedMode:
    """系统降级运行配置"""
    missing_camera → 禁用视觉感知，仅用本体感+TOF
    missing_one_motor → 限制速度 50%，禁用转弯
    missing_TOF → 降低前进速度至 0.3m/s
    missing_STM32 → 无法运行，必须修复
    degraded → 语音告知用户当前能力受限
```

### 12.4 热插拔与动态重检

- 执行器在运行中被拔出/插入 → 检测到连接状态变化 → 触发热自检 → 更新能力清单
- 传感器恢复 → 重新校准 → 从降级恢复到完全能力

---

## 13. 可观测性

### 13.1 设计目标

> **不出问题时不需要看，出问题时能回溯到每毫秒每条指令。**

### 13.2 分层日志

```python
# Level 0: 关键事件 (始终记录)
logger.event("system.boot", {"actuators": 3, "sensors": 4, "mode": "full"})
logger.event("task.start", {"task_id": "t1", "subtask": "导航B区"})
logger.event("task.done", {"task_id": "t1", "duration": 45.2})
logger.event("error.collision", {"speed": 0.5, "obstacle": "wall"})
logger.event("emergency.stop", {"reason": "tilt", "angle": 47.2})

# Level 1: 决策轨迹 (每次LLM调用记录)
logger.decision({
    "step": 42, "timestamp": 1234567890.123,
    "observation_summary": "...",
    "llm_thought": "前方有障碍物，需要右转...",
    "actions": [{"actuator": "left_motor", "action": "set_speed", "params": ...}],
    "llm_latency_ms": 380
})

# Level 2: 执行细节 (每个动作执行记录)
logger.action({
    "step": 42, "actuator": "left_motor",
    "action": "set_speed", "params": {"rpm": 500},
    "result": "ok", "state_after": {"rpm": 498}
})

# Level 3: 调试 (开发时开启，运行时关闭)
logger.debug("perception.vision.capture: 224KB JPEG, 32ms")
logger.debug("memory.working.update: 5 items in buffer")
```

### 13.3 关键指标

| 指标 | 含义 | 告警阈值 |
|------|------|---------|
| `llm_latency_p50` | LLM 响应中位数 | > 2s 告警 |
| `llm_error_rate` | LLM API 调用失败率 | > 10% |
| `action_success_rate` | 动作执行成功率 | < 95% |
| `stm32_heartbeat_age` | 距上次 STM32 心跳的时间 | > 1s (断连) |
| `battery_level` | 当前电量 | < 20% 低电量 |
| `motor_temp_max` | 最高电机温度 | > 80°C |
| `derived_speed` | 实际速度与指令速度偏差 | > 20% (打滑/堵转) |

### 13.4 轨迹回放

```python
# 存储: 每条 epirodic_memory 记录含完整的 Observation + Decision + Results
# 回放: 加载指定时间段的记录 → 逐帧重现在前端
# 用途: 分析为什么在某处做了某个决策、LLM 思考过程可视化
```

---

## 14. 人机协同

### 14.1 四种协同模式

```
完全自主 ←──────────────────────────────→ 完全手动

  AUTO          SUPERVISED         APPROVAL          MANUAL
  全自动        监督模式            审批模式           手动模式
  ──────────    ──────────         ──────────         ──────────
  机器人独立    机器人决策+执行    机器人提议         人类通过操纵杆
  决策+执行    人类可随时打断      人类确认后执行     直接控制

  适用:         适用:              适用:              适用:
  导航、探索    日常任务            危险动作           紧急情况
  (低风险)      (中风险)           (激光、化学品)     (系统故障)
```

### 14.2 模式切换

```
用户语音: "进入审批模式"  →  mode = APPROVAL
用户语音: "自己来吧"      →  mode = AUTO
急停按钮按下              →  mode = MANUAL (硬件强制)
系统检测到需要确认        →  暂时进入 APPROVAL (单次)
```

### 14.3 审批机制

```python
class ApprovalManager:
    def request_approval(self, action: dict, reason: str) -> bool:
        """请求用户确认危险动作"""
        TTS.speak(f"需要确认: {reason}。允许吗?")

        # 等待语音回复 (yes/no) 或超时
        response = self._wait_for_confirmation(timeout=10.0)
        if response is None:
            return False  # 超时 = 拒绝
        return response

    # 需要审批的动作
    requires_approval = [
        "EnergyBeam.fire",   # 激光发射
        "Pump.set_flow > 0.5",  # 大流量喷洒
        "speed > 1.0",       # 高速移动
    ]
```

### 14.4 行为解释 (Explainable Agent)

每次决策后，Agent 应该能解释自己的行为：

```
用户: "你为什么停下来?"
Agent: "两秒前TOF检测到前方25cm有障碍物，
        我判断无法绕过，正在等待你的指令。"

实现: LLM 的 thought 字段转为语音播报
     情景记忆支持 "最近N步做了什么" 的查询
```

### 14.5 远程监控面板 (未来)

```
┌────────────────────────────────────────────────┐
│  Telos Monitor                    [ AUTO ▼ ]   │
│                                                │
│  ┌──────────────┐  ┌──────────────────────────┐│
│  │  摄像头画面    │  │  机器人状态               ││
│  │              │  │  速度: 0.3 m/s            ││
│  │   [实时]     │  │  航向: 145°               ││
│  │              │  │  电量: 87% ████████░░     ││
│  │              │  │  模式: SUPERVISED         ││
│  └──────────────┘  └──────────────────────────┘│
│                                                │
│  ┌────────────────────────────────────────────┐│
│  │  最近决策                                    ││
│  │  [12:03:42] 右转30°避开椅子  ✓              ││
│  │  [12:03:40] LLM决策: 前方障碍，右转          ││
│  │  [12:03:37] 前进0.3m/s  ✓                   ││
│  └────────────────────────────────────────────┘│
│                                                │
│  ┌──────────┐ ┌──────────┐ ┌────────────────┐  │
│  │   急停    │ │  回充    │ │  任务: 探索...  │  │
│  └──────────┘ └──────────┘ └────────────────┘  │
└────────────────────────────────────────────────┘
```

---

## 15. 世界模型与空间表征

### 15.1 为什么需要

当前记忆系统只存储"事件"，不存储"空间"。没有空间表征的机器人：
- 每次任务从零探索
- 不知道 B 区在自己北边还是东边
- 无法做"回充电桩"这种需要位置记忆的任务
- 每次绕同一个障碍物像第一次见到

### 15.2 三层空间表征

```
Layer 1: 局部代价地图 (Local Costmap)
  ─────────────────────────────────
  范围: 机器人周围 5m × 5m
  分辨率: 5cm/格
  更新: 实时 (TOF/超声/摄像头)
  用途: 即时避障、局部路径规划
  实现: 2D 占据栅格 (numpy array)
  存储: 内存，不持久化

Layer 2: 拓扑地图 (Topological Map)
  ─────────────────────────────────
  范围: 整个工作区域
  内容: 节点=关键位置(充电桩/房间入口/作业区)
         边=可通行路径 + 距离
  更新: 每次任务发现新节点或路径
  用途: 全局导航 ("去B区" = 在拓扑图上找路径)
  实现: NetworkX 图 + JSON 持久化
  存储: SQLite / JSON 文件

Layer 3: 语义地图 (Semantic Map)
  ─────────────────────────────────
  范围: 整个工作区域
  内容: 房间标签("厨房"/"B区")、物体位置("西红柿在第3行")
        危险区域("斜坡"/"水坑")
  更新: LLM 识别 + 人工标注
  用途: 人类可理解的场景 ("去厨房" = 找标签为"厨房"的节点)
  实现: 拓扑图节点带标签 + 物体索引
  存储: SQLite
```

### 15.3 空间与记忆的整合

```
情景记忆中的每条轨迹记录:
  {step: 42, action: "前进", position: (1.5, 3.2, 0°), ...}
                      ↑
                  从 STM32 里程计/IMU 推算的位姿

任务结束后:
  轨迹点序列 → 更新拓扑地图 (添加新发现的路径)
  LLM 识别场景 → 标注语义 ("这个区域有很多西红柿")
```

### 15.4 开机恢复

```
系统启动 →
  加载上次保存的拓扑地图 (JSON)
  加载语义标注 (SQLite)
  ├── 有上次地图 → "我在哪?" → 扫描周围特征 → 定位 → 继续
  └── 无地图 → "第一次来" → 从零建图
```

---

## 16. 电源管理

### 16.1 设计目标

- 支持太阳能充电（你明确提出的需求）
- 电量感知：每个决策都要考虑功耗
- 自动返航：低电量时安全返回充电桩
- 电池健康：长期管理充放电，延长电池寿命

### 16.2 电源状态模型

```python
@dataclass
class PowerState:
    battery_level: float          # 0-100%
    voltage: float                # 电池电压 (V)
    current_draw: float           # 当前总电流 (A)
    solar_power: float            # 太阳能板输出功率 (W)

    # 计算
    estimated_runtime: float      # 当前功耗下剩余时间 (分钟)
    is_charging: bool
    charging_source: str          # "solar" | "dock" | "none"

class PowerBudget:
    """功耗预算 — 每个决策前检查"""
    available: float              # 当前可用功率 (W)
    allocated: dict[str, float]   # {"motors": 80, "laser": 150, "compute": 20}
```

### 16.3 低电量策略

```
电量阈值  行为
────────  ──────────────────────────────
> 50%     正常模式，所有功能可用
30-50%    节能模式，限制大功率执行器 (激光、高速移动)
20-30%    警告模式，语音提醒用户，禁用激光
10-20%    返航模式，中断当前任务 → 导航到充电桩
< 10%     紧急模式，关闭所有非必要组件 → 原地等待救援
```

### 16.4 太阳能充电策略

```
太阳能板 ──→ MPPT 充电控制器 ──→ 电池

充电状态下的行为:
  ┌──────────────────────────────────┐
  │ 充电中 (太阳能):                   │
  │  - 电池 > 80%: 正常运行             │
  │  - 电池 50-80%: 轻量任务 (巡逻/监测) │
  │  - 电池 < 50%: 待机充电，不执行任务   │
  │                                   │
  │ 充电中 (座充):                     │
  │  - 休眠充电，直到电池 > 90%         │
  │  - 期间: 软件更新、日志清理、        │
  │           离线分析、模型同步         │
  └──────────────────────────────────┘
```

### 16.5 决策中的功耗感知

LLM 的认知推理应该考虑功耗：

```
System Prompt 注入:
  当前功耗预算: 150W 可用
  左电机(50W) + 右电机(50W) + 激光(100W) = 200W → 超预算!
  → 如果使用激光，必须降低速度 (电机各 25W)
```

---

## 17. 多智能体协调 (演进)

### 17.1 演进路径

```
Phase 1 (当前): 单机器人完整闭环 ← 我们现在在这里
Phase 2: 双机器人共享地图 (拓扑地图同步)
Phase 3: 多机器人任务分配 (分工: A导航+B采集+C搬运)
Phase 4: 群体涌现 (隐式通信/环境痕迹/角色自适应)
```

### 17.2 预留的扩展点

```python
class AgentNetwork:
    """多智能体协调网络 — Phase 2+ 使用"""
    agents: dict[str, AgentInfo]   # 已知的其他Agent
    shared_map: TopologicalMap     # 共享拓扑地图
    message_queue: asyncio.Queue   # 异步消息

    def discover_agents(self) -> list[AgentInfo]: ...
    def broadcast(self, msg: dict) -> None: ...
    def request_help(self, task: str) -> Optional[str]: ...
```

### 17.3 通信方式

| 层 | 方式 | 延迟 | 用途 |
|------|------|------|------|
| 环境痕迹 | 拓扑地图标注 "Agent A 在探索 B 区" | 秒级 | 避免重复探索 |
| 直接消息 | MQTT广播 → 所有 Agent | 100ms | 任务分配、求助 |
| 云端协调 | 共享任务队列 | 500ms+ | 全局任务调度 |

---

## 18. 仿真测试环境 (演进)

### 18.1 为什么需要

- 真实机器人测试成本高（碰撞损坏、时间消耗、需要物理空间）
- 算法迭代需要快速反馈
- RL 反思循环需要大量试错

### 18.2 仿真方案

```
Phase 1 (当前): Dry-run 模式 — 不接硬件，不调 API，只验证架构逻辑
Phase 2: 简单仿真 — Python 内建 2D Grid 仿真 (复用 SomatoMind env/gridworld.py)
Phase 3: 物理仿真 — ROS2 + Gazebo / MuJoCo (配合 robot 项目)
```

### 18.3 仿真接口抽象

```python
class Environment(Protocol):
    """仿真与真实环境的统一接口"""
    def step(self, actions: list[dict]) -> Observation: ...
    def reset(self) -> Observation: ...

# 真实环境
class RealEnvironment(Environment):
    # 使用 PerceptionManager + Executor + STM32

# 仿真环境
class SimEnvironment(Environment):
    # 使用仿真引擎 + 虚拟传感器 + 虚拟执行器
```

---

## 19. 对话身份一致性 (演进)

### 19.1 问题

Telos 的语音交互可能听起来像 "冷冰冰的工具" 或 "人格分裂"——每次调用 LLM 都是独立上下文，没有稳定的人格。

### 19.2 设计

```
System Prompt 固定的人格定义:
  "你是 Telos，一个友好、细心、略带幽默感的机器人助手。
   你说话简洁，不喜欢啰嗦。
   你对自己不确定的事情会坦率承认。
   你的主人是张帅清，你叫他"帅清"。
   
   今天的日期是 {date}，时间是 {time}。
   你上次执行任务是在 {last_task_time}，完成了 {last_task_result}。"
```

### 19.3 跨会话记忆

情景记忆中的"对话历史"跨会话保留：
```
用户: "记住，厨房的花瓶是易碎品"
→ 存入语义地图: kitchen.vase = fragile
→ 下次靠近花瓶时自动减速
```

---

## 20. 外部系统集成 (演进)

### 20.1 待集成的系统

| 系统 | 集成方式 | 用途 |
|------|---------|------|
| Home Assistant | REST API | 控制智能家居设备、获取传感器数据 |
| 天气 API | HTTP GET | 户外任务前检查天气 |
| 邮件/通知 | SMTP | 完成任务/异常时通知 |
| GitHub | API | OTA 更新代码和配置 |
| 手机 App | MQTT | 推送通知、接收指令 |

### 20.2 集成接口设计

```python
class Integration(Protocol):
    name: str
    def connect(self) -> bool: ...
    def disconnect(self) -> None: ...
    def health(self) -> dict: ...

class HomeAssistantIntegration(Integration):
    """与 Home Assistant 集成 — 复用 CareLink 项目配置"""
    name = "home_assistant"
    def get_sensor(self, entity_id: str) -> dict: ...
    def call_service(self, domain: str, service: str, data: dict) -> bool: ...
```
