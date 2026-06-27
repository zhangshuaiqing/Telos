# 执行器原语规格

> 所有执行器实现 `Actuator` 协议。7 种原语覆盖所有机械+能量输出场景，任意复杂执行器 = 原语的自由组合。

---

## 目录

1. [RotaryVelocity — 连续旋转速度](#1-rotaryvelocity--连续旋转速度)
2. [RotaryPosition — 角位置定位](#2-rotaryposition--角位置定位)
3. [LinearPosition — 直线位置控制](#3-linearposition--直线位置控制)
4. [BinaryActuator — 开关控制](#4-binaryactuator--开关控制)
5. [Pump — 流体输出控制](#5-pump--流体输出控制)
6. [Gripper — 抓取控制](#6-gripper--抓取控制)
7. [EnergyBeam — 定向能量输出](#7-energybeam--定向能量输出)
8. [原语组合模式](#8-原语组合模式)
9. [新增原语检查清单](#9-新增原语检查清单)

---

## 0. 通用接口

所有原语必须实现：

```python
class Actuator(Protocol):
    name: str
    state: ActuatorState       # idle | busy | error | disconnected

    def init(self) -> bool: ...               # 初始化 + 自检
    def get_capability(self) -> ActuatorCapability: ...  # 能力描述
    def get_state(self) -> dict: ...          # 当前状态快照
    def emergency_stop(self) -> None: ...     # 硬件急停

class ActuatorCapability:
    id: str
    name: str
    type: str
    description: str
    actions: list[str]          # 可用动作列表
    constraints: dict           # 参数约束
```

### 通用状态转换

```
                    ┌──────────────────────────────┐
                    │         DISCONNECTED          │
                    │    (初始状态 / 通信断开)        │
                    └──────────────┬───────────────┘
                                   │ init() 成功
                                   ▼
                    ┌──────────────────────────────┐
          ┌────────│            IDLE               │──────────┐
          │        │    (就绪，无活动)               │          │
          │        └──────────────┬───────────────┘          │
          │                       │ 动作触发                   │ 恢复
          │                       ▼                          │
          │        ┌──────────────────────────────┐          │
          │        │            BUSY               │          │
          │        │    (正在执行动作)               │          │
          │        └──────────────┬───────────────┘          │
          │                       │ 异常                      │
          │                       ▼                          │
          │        ┌──────────────────────────────┐          │
          └───────▶│           ERROR              │──────────┘
                   │    (故障 — 需复位或替换)       │
                   └──────────────────────────────┘
```

---

## 1. RotaryVelocity — 连续旋转速度

### 1.1 描述
控制连续旋转的电机转速。用于底盘驱动、刀片、风扇、离心泵等。

### 1.2 硬件映射

| 接口 | 硬件 | 转速范围 | 控制方式 |
|------|------|---------|---------|
| VESC/CAN | BLDC 轮毂电机 | 0-4000 rpm | CAN 帧 → 电流/速度环 |
| PWM | DC 有刷电机 | 0-5000 rpm | 占空比 0-100% |
| GPIO | 继电器控制电机 | 0 / 额定转速 | on/off |
| 无反馈 | 开环控制 | — | 无转速确认 |

### 1.3 参数规格

```yaml
RotaryVelocity:
  name: "left_motor"
  max_rpm: 3000.0           # 最大转速
  default_rpm: 0.0          # 默认停止
  acceleration: 500.0       # 加速度限制 (rpm/s), 0=无限制
  has_feedback: true        # 是否有转速反馈 (编码器/Hall)
  reverse_supported: true   # 是否支持反向旋转
  pid:                      # 速度 PID (若有闭环)
    kp: 0.5
    ki: 0.1
    kd: 0.05
    integral_limit: 100
```

### 1.4 接口

| 动作 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `set_speed(rpm)` | float, ±max_rpm | `{"speed": actual_rpm}` | 设置目标转速，正=正转，负=反转 |
| `stop()` | — | `{"speed": 0.0}` | 停止 (速度降到 0) |
| `get_speed()` | — | float (当前实际转速) | 读取编码器反馈 (无反馈则返回目标值) |

### 1.5 状态转换

```
IDLE ──set_speed(≠0)──▶ BUSY
BUSY ──stop()─────────▶ IDLE
BUSY ──set_speed(0)───▶ IDLE
BUSY ──current > 2x额定─▶ ERROR
ANY  ──emergency_stop()─▶ IDLE (speed=0)
```

### 1.6 错误模式

| 错误 | 检测 | 恢复 |
|------|------|------|
| 过流 | I_actual > I_rated × 2 | 立即停机 → 30s 冷却 → 重试 ×1 |
| 堵转 | 目标速度-实际速度 > 30% × 1s | 停机 → 反转 100ms 释放 → 重试 |
| 无反馈 | 编码器数据超时 500ms | 降级为开环控制，限制速度 50% |
| 过热 | 温度 > 80°C | 限制速度 30%，语音警告 |
| 通信超时 | STM32 心跳丢失 > 500ms | emergency_stop |

### 1.7 安全约束

```python
class RotaryVelocitySafety:
    def validate(self, command: dict) -> bool:
        rpm = command.get("params", {}).get("rpm", 0)
        # 1. 速度上限
        if abs(rpm) > self.max_rpm:
            return False
        # 2. 加速度限制
        if self.acceleration > 0:
            max_change = self.acceleration * dt
            if abs(rpm - self._current_rpm) > max_change:
                rpm = self._current_rpm + sign(rpm) * max_change
        # 3. 电机温度
        if self._temp > 80 and abs(rpm) > self.max_rpm * 0.5:
            rpm = self.max_rpm * 0.3 * sign(rpm)
        return True
```

### 1.8 测试

```python
def test_rotary_velocity_set_speed():
    motor = RotaryVelocity("test_motor", max_rpm=3000)
    motor.init()
    result = motor.set_speed(1500)
    assert result["speed"] == 1500
    assert motor.state == ActuatorState.BUSY

def test_rotary_velocity_clamps_speed():
    motor = RotaryVelocity("test_motor", max_rpm=3000)
    motor.init()
    result = motor.set_speed(5000)  # 超出上限
    assert result["speed"] == 3000  # 被钳制

def test_emergency_stop_resets_to_idle():
    motor = RotaryVelocity("test_motor", max_rpm=3000)
    motor.init()
    motor.set_speed(2000)
    motor.emergency_stop()
    assert motor._speed == 0.0
    assert motor.state == ActuatorState.IDLE
```

---

## 2. RotaryPosition — 角位置定位

### 2.1 描述
控制旋转角度到指定位置。用于舵机、步进电机(位置模式)、云台、振镜。

### 2.2 硬件映射

| 接口 | 硬件 | 角度范围 | 精度 |
|------|------|---------|------|
| PWM 50Hz | 标准舵机 (SG90/MG996) | 0-180° | ~1° |
| PWM 333Hz | 数字舵机 | 0-270° | ~0.5° |
| STEP/DIR | 步进电机 | 无限制 (多圈) | 取决于细分 |
| CAN | 智能舵机 (Dynamixel) | 0-360° | 0.088° |

### 2.3 参数规格

```yaml
RotaryPosition:
  name: "steering"
  min_angle: -90.0          # 最小角度 (度)
  max_angle: 90.0           # 最大角度 (度)
  default_angle: 0.0        # 默认角度 (零点)
  speed: 60.0               # 转动速度 (°/s)
  has_feedback: false       # 是否有角度反馈
  multi_turn: false         # 是否支持多圈
```

### 2.4 接口

| 动作 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `set_angle(deg)` | float, [min, max] | `{"angle": target}` | 转动到指定角度 |
| `get_angle()` | — | float (当前角度) | 读取位置反馈 |
| `set_zero()` | — | `{"zeroed": true}` | 当前角度标记为零点 |

### 2.5 状态转换

```
IDLE ──set_angle(≠current)──▶ BUSY
BUSY ──到达目标─────────────▶ IDLE
BUSY ──超时 (未到达)────────▶ ERROR
BUSY ──过流─────────────────▶ ERROR
ANY  ──emergency_stop()─────▶ IDLE (保持当前位置)
```

### 2.6 错误模式

| 错误 | 检测 | 恢复 |
|------|------|------|
| 卡死 | 角度不变 × 1s 且电流高 | 小幅度来回摆动 → 重试 → 报警 |
| 过冲 | 实际角度 > 目标 + 5° | 反向微调 |
| 掉电偏位 | 重启后角度漂移 | init() 时执行回零 (限位开关或硬止点) |
| 齿轮打滑 | 无反馈时的隐性错误 | 定期回零检测偏差 |

### 2.7 测试

```python
def test_rotary_position_clamps_angle():
    servo = RotaryPosition("test", min_angle=-90, max_angle=90)
    servo.init()
    result = servo.set_angle(120)  # 超出范围
    assert result["angle"] == 90   # 被钳制

def test_rotary_position_tracks_state():
    servo = RotaryPosition("test")
    servo.init()
    servo.set_angle(45)
    assert servo.get_state()["angle"] == 45
```

---

## 3. LinearPosition — 直线位置控制

### 3.1 描述
控制直线运动的行程。用于电动推杆、丝杆、升降平台、夹爪开合。

### 3.2 硬件映射

| 接口 | 硬件 | 行程 | 精度 |
|------|------|------|------|
| 方向+PWM | 电动推杆 | 50-500mm | 限位开关精度 |
| STEP/DIR | 丝杆步进 | 不限制 | μm 级 |
| CAN | 智能线性执行器 | 按型号 | <0.1mm |
| 气动+位置传感器 | 气缸 | 固定行程 | mm 级 |

### 3.3 参数规格

```yaml
LinearPosition:
  name: "lift"
  min_position: 0.0          # 最小位置 (mm)
  max_position: 200.0        # 最大位置 (mm)
  default_position: 0.0      # 默认位置
  speed: 50.0                # 移动速度 (mm/s)
  has_feedback: true         # 是否有位置反馈
  has_limit_switches: true   # 是否有限位开关
```

### 3.4 接口

| 动作 | 参数 | 返回值 |
|------|------|--------|
| `set_position(mm)` | float, [min, max] | `{"position": target}` |
| `get_position()` | — | float |
| `home()` | — | `{"position": 0.0}` — 回零点 |

### 3.5 错误模式

| 错误 | 检测 | 恢复 |
|------|------|------|
| 限位触发 | 硬限位开关 | 立即停止 → 反向微动脱离 → 回零 |
| 过载 | 电流>额定 + 位置不变 | 停机 → 反向脱离 → 报警 |

### 3.6 测试

```python
def test_linear_homing():
    linear = LinearPosition("lift", min_pos=0, max_pos=200)
    linear.init()
    result = linear.home()
    assert result["position"] == 0.0

def test_linear_position_limits():
    linear = LinearPosition("lift", min_pos=0, max_pos=200)
    linear.init()
    result = linear.set_position(250)
    assert result["position"] == 200  # clamped
```

---

## 4. BinaryActuator — 开关控制

### 4.1 描述
二进制开关状态。最简单的原语。用于电磁阀、继电器、电磁铁、MOSFET 开关、电磁锁。

### 4.2 硬件映射

| 接口 | 硬件 | 特性 |
|------|------|------|
| GPIO | 继电器模块 | 5V/12V 控制 |
| GPIO+MOSFET | 电磁阀 | 直接驱动 |
| GPIO | LED / 蜂鸣器 | 状态指示 |
| GPIO | 电磁铁/电磁锁 | 保持电流 |

### 4.3 参数规格

```yaml
BinaryActuator:
  name: "valve"
  default_state: off          # 默认状态
  type: "latching"            # latching (双稳态) | momentary (需要持续供电)
  max_on_duration: null       # 最大连续开启时间 (秒), null=不限
```

### 4.4 接口

| 动作 | 参数 | 返回值 |
|------|------|--------|
| `on()` | — | `{"on": true}` |
| `off()` | — | `{"on": false}` |
| `toggle()` | — | `{"on": prev_state}` |
| `pulse(duration_ms)` | int | `{"pulsed": true}` |

### 4.5 状态转换

```
IDLE ──on()──────────────────▶ BUSY
BUSY ──off()─────────────────▶ IDLE
BUSY ──max_on_duration到达───▶ IDLE (自动)
BUSY ──pulse() 时间到────────▶ IDLE
```

### 4.6 错误模式

| 错误 | 检测 | 恢复 |
|------|------|------|
| MOSFET 击穿 | 指令off但电流>0 | 切断该通道电源（继电器级联断开） |
| 继电器粘连 | 指令off但导通 | 振动或替换 |

### 4.7 测试

```python
def test_binary_toggle():
    b = BinaryActuator("test")
    b.init()
    assert b.get_state()["on"] == False
    b.on()
    assert b.get_state()["on"] == True
    b.off()
    assert b.get_state()["on"] == False

def test_binary_pulse():
    b = BinaryActuator("test")
    b.init()
    result = b.pulse(100)
    assert result["pulsed"] == True
```

---

## 5. Pump — 流体输出控制

### 5.1 描述
控制液体或气体的流量。用于水泵、蠕动泵、气泵、真空泵。

### 5.2 硬件映射

| 类型 | 硬件 | 流量范围 | 驱动方式 |
|------|------|---------|---------|
| 离心泵 | 直流有刷/无刷水泵 | 0.1-10 L/min | PWM/MOSFET |
| 蠕动泵 | 步进电机+软管滚轮 | 0.01-1 L/min | 步进 STEP/DIR |
| 隔膜泵 | 直流泵 | 0.5-5 L/min | PWM |
| 气泵 | 直流气泵 | — | MOSFET 开关 |
| 比例阀 | 电磁比例阀 | 取决于管道压力 | PWM 电流控制 |

### 5.3 参数规格

```yaml
Pump:
  name: "spray_pump"
  max_flow: 1.0              # 最大流量
  flow_unit: "L/min"         # 流量单位
  min_flow: 0.0              # 最小可调流量
  type: "centrifugal"        # centrifugal | peristaltic | diaphragm
  self_priming: true         # 是否自吸
  dry_run_protection: true   # 是否保护空转
```

### 5.4 接口

| 动作 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `set_flow(rate)` | float, [0, max] | `{"flow": rate}` | 设置目标流量 |
| `stop()` | — | `{"flow": 0.0}` | 停止 |
| `prime()` | — | `{"primed": true}` | 自吸排气 |
| `get_flow()` | — | float | 当前流量 (需要流量传感器) |

### 5.5 错误模式

| 错误 | 检测 | 恢复 |
|------|------|------|
| 空转 | 电流 < 空载电流 | 立即停止 → 报警 |
| 堵塞 | 电流 > 额定 2x + 流量 0 | 停机 → 反转排气 → 重试 |
| 泄漏 | 流量与预期偏差 > 30% | 报警 (不停机 — 可能只是轻微漏) |

### 5.6 测试

```python
def test_pump_flow_clamped():
    pump = Pump("test", max_flow=1.0)
    pump.init()
    result = pump.set_flow(1.5)
    assert result["flow"] == 1.0  # clamped

def test_pump_stop():
    pump = Pump("test", max_flow=1.0)
    pump.init()
    pump.set_flow(0.5)
    pump.stop()
    assert pump.get_state()["flow"] == 0.0
```

---

## 6. Gripper — 抓取控制

### 6.1 描述
控制机械手爪的开合和抓取力。用于采摘果实、搬运物体、工具交换。

### 6.2 硬件映射

| 类型 | 硬件 | 抓取力 | 反馈 |
|------|------|--------|------|
| 平行夹爪 | 舵机/步进驱动 | 5-50N | 电流估计 |
| 自适应手指 | 欠驱动连杆 | — | 被动 |
| 气动吸盘 | 真空泵+吸盘 | 取决于负压 | 压力传感器 |
| 电磁爪 | 电磁铁 | 取决于电流 | 无 |

### 6.3 参数规格

```yaml
Gripper:
  name: "gripper"
  max_force: 20.0            # 最大抓取力 (N)
  stroke: 50.0               # 开合行程 (mm)
  has_force_feedback: false  # 是否有力传感器
  type: "parallel"           # parallel | adaptive | suction | magnetic
```

### 6.4 接口

| 动作 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `grasp()` | — | `{"closed": true}` | 关闭夹爪 |
| `release()` | — | `{"closed": false}` | 打开夹爪 |
| `grasp_with_force(force)` | float, [0, max] | `{"closed": true, "force": F}` | 以指定力抓取 |
| `set_opening(mm)` | float | `{"opening": mm}` | 设置开口大小 |

### 6.5 错误模式

| 错误 | 检测 | 恢复 |
|------|------|------|
| 未抓到物体 | 抓取后力 < 预期 | 语音"没抓到" → 释放 → 重试 ×2 |
| 抓太紧 | 力 > max_force → 可能损坏物体 | 力反馈: 到达目标力即停止 |
| 物体滑落 | 力突降 | 重新抓取 |

### 6.6 测试

```python
def test_gripper_grasp_and_release():
    g = Gripper("test")
    g.init()
    result = g.grasp()
    assert result["closed"] == True
    assert g.state == ActuatorState.BUSY
    g.release()
    assert g.state == ActuatorState.IDLE

def test_gripper_force_limit():
    g = Gripper("test", max_force=20)
    g.init()
    result = g.grasp_with_force(50)
    assert result["force"] <= 20
```

---

## 7. EnergyBeam — 定向能量输出

### 7.1 描述
定向发射能量束（激光、红外、紫外）。用于激光除草、红外加热、紫外消毒。

> **⚠ 最高安全等级操作。需要多层确认。**

### 7.2 硬件映射

| 类型 | 硬件 | 功率 | 波长 |
|------|------|------|------|
| 半导体激光 | 激光二极管 | 1-150W | 450nm (蓝) / 808nm (红外) |
| CO₂ 激光 | CO₂ 激光管 | 40-150W | 10600nm |
| 红外加热 | 红外 LED 阵列 / 加热管 | 50-500W | 红外 |
| UV 消毒 | UV-C LED | 10-50W | 254nm |

### 7.3 参数规格

```yaml
EnergyBeam:
  name: "laser_weeder"
  max_power: 150.0           # 最大功率 (W)
  wavelength: "450nm"        # 波长
  type: "laser"              # laser | infrared | uv
  requires_approval: true    # 是否需要用户审批
  max_fire_duration_ms: 2000 # 单次最长照射时间
  cooldown_ms: 500           # 两次发射间最短冷却时间
  safety:                    # 安全约束
    min_safe_distance: 500   # 最小安全距离 (mm)
    auto_shutoff_tilt: 30    # 倾角超过此值自动关断 (°)
    key_switch_required: true # 是否需要物理钥匙
    protective_housing_required: true # 是否需要防护罩
```

### 7.4 接口

| 动作 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `set_power(watts)` | float, [0, max] | `{"power": watts}` | 设置功率 (不发射) |
| `fire(duration_ms)` | int | `{"fired": true}` | 发射指定时长 |
| `fire_at(power, duration_ms)` | float, int | `{"fired": true}` | 设置功率并发射 |
| `stop()` | — | `{"fired": false}` | 立即停止发射 |

### 7.5 安全流程

```
fire() 请求
  │
  ├── STM32 已连接且无急停? ──NO──▶ 拒绝: "无法发射, 硬件未就绪"
  │
  ├── 倾角 < safety.auto_shutoff_tilt? ──NO──▶ 拒绝: "倾角过大, 无法发射"
  │
  ├── 审批模式? ──YES──▶ 语音请求确认: "即将发射150W激光, 允许吗?"
  │                    ├── 用户说"是" → 继续
  │                    └── 超时/拒绝 → 取消
  │
  ├── TOF 检测安全距离? ──NO──▶ 拒绝: "前方太近, 无法发射"
  │
  ├── 防护罩关闭? ──NO──▶ 拒绝: "防护罩未关闭"
  │
  ▼
STM32 Enable 信号 → 发射
  │
  ├── 持续时间超 max_fire_duration_ms → 自动关断
  ├── 倾角/距离/电流 任意异常 → 硬件关断 (L3安全)
  └── 正常完成 → stop()
```

### 7.6 错误模式

| 错误 | 检测 | 恢复 |
|------|------|------|
| 过热 | 激光器温度 > 60°C | 强制冷却 60s → 不可用 |
| 反射 | 功率回读异常 (有反射) | 立即关断 → 调整角度 |
| 光纤断 | 功率监测 = 0 | 关断 → 报警 → 须更换 |

### 7.7 测试

```python
def test_energy_beam_requires_approval():
    laser = EnergyBeam("test", max_power=150)
    laser.init()
    # 审批模式下的测试见 approval 模块

def test_energy_beam_power_limit():
    laser = EnergyBeam("test", max_power=150)
    laser.init()
    result = laser.set_power(300)
    assert result["power"] <= 150

def test_energy_beam_emergency_stop():
    laser = EnergyBeam("test", max_power=150)
    laser.init()
    laser.set_power(100)
    laser.emergency_stop()
    assert laser._power == 0.0
    assert laser.state == ActuatorState.IDLE

def test_energy_beam_refuses_without_safety():
    """没有硬件安全确认不能发射"""
    laser = EnergyBeam("test", max_power=150)
    laser.init()
    result = laser.fire(1000)  # no hardware safety check
    assert result["power"] == 0  # refused
```

---

## 8. 原语组合模式

任意复杂执行器 = 上述原语的自由组合。通过 `class` 封装：

```python
class Sprayer:
    """喷雾器 = 2DOF 旋转 + 泵 + 阀"""
    def __init__(self):
        self.pan = RotaryPosition("spray_pan", -90, 90)
        self.tilt = RotaryPosition("spray_tilt", 0, 60)
        self.pump = Pump("spray_pump", max_flow=1.0)
        self.valve = BinaryActuator("spray_valve")

    def get_capability(self) -> ActuatorCapability:
        return ActuatorCapability(
            id="sprayer", name="喷雾器", type="composite",
            description="农药喷洒装置: 2DOF方向 + 流量控制",
            actions=["spray_at"],
            constraints={"max_flow": 1.0, "pan_range": [-90, 90]}
        )

    def spray_at(self, pan: float, tilt: float, flow: float, duration: float):
        self.pan.set_angle(pan)
        self.tilt.set_angle(tilt)
        self.pump.set_flow(flow)
        self.valve.on()
        time.sleep(duration)
        self.valve.off()
        self.pump.stop()
```

### 常见组合模式

| 组合 | 原语配方 | 应用 |
|------|---------|------|
| 差速底盘 | RotaryVelocity × 2 | 所有移动 |
| 喷雾器 | RotaryPosition × 2 + Pump + BinaryActuator | 农药/浇花 |
| 采摘手 | RotaryPosition + LinearPosition + Gripper | 果实采摘 |
| 吸尘器 | LinearPosition + Pump(-) + RotaryVelocity | 清洁 |
| 激光除草 | RotaryPosition × 2 (振镜) + EnergyBeam | 精准除草 |
| 自卸斗 | LinearPosition (推杆) | 运输倾倒 |
| 云台相机 | RotaryPosition × 2 | 全景扫描 |

---

## 9. 新增原语检查清单

如果需要添加第 8 种原语：

- [ ] 确定它在 7 原语之外（不是已有原语的组合）
- [ ] 定义参数规格 (min/max/default)
- [ ] 实现 Actuator 协议的所有方法
- [ ] 定义状态转换图和错误模式
- [ ] 添加安全约束 (L2 端侧校验)
- [ ] 添加 STM32 固件支持
- [ ] 编写单元测试 (至少: 参数钳制、状态转换、急停)
- [ ] 更新 Executor 的注册逻辑
- [ ] 更新认知引擎 Prompt 中的能力清单生成逻辑
- [ ] 更新本文档
