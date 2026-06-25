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
│   ├── safety/
│   │   └── safety.py         # 三层安全校验
│   ├── comm/
│   │   └── stm32.py          # STM32 二进制通信协议
│   └── utils/
│       └── config.py         # 配置管理
├── docs/
│   ├── architecture.md       # 本文档 (总体架构)
│   ├── actuators.md          # 7 原语详细规格
│   ├── perception.md         # 感知通道详细规格
│   ├── cognition.md          # Prompt 工程 + RL 循环
│   ├── safety.md             # 安全系统设计
│   ├── protocol.md           # 通信协议规范
│   ├── extending.md          # 扩展指南
│   └── deployment.md         # 部署指南
├── main.py
├── pyproject.toml
└── README.md
```

---

## 10. 待后续讨论

- ☐ 电源系统 — 太阳能充电方案、电池管理
- ☐ 循环细节 — Agent 循环的精确时序和并发模型
- ☐ 程序记忆的具体实现 — JSON 技能库 vs 向量检索
- ☐ RL 反思的具体格式 — 如何自动化提取改进策略
- ☐ STM32 固件架构 — 原语指令的固件层实现
