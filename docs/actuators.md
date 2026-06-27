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
10. [功耗规格](#10-功耗规格)
11. [STM32 命令字映射](#11-stm32-命令字映射)
12. [标定参数格式](#12-标定参数格式)

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

---

## 10. 功耗规格

### 10.1 设计目的

Ch16 电源管理的功耗预算是以每个执行器为基础的。LLM 做决策时需要知道"开激光就不能同时高速行驶"。每个原语必须声明功耗。

### 10.2 各原语功耗

| 原语 | 硬件示例 | 待机 (W) | 额定 (W) | 峰值 (W) | 电机效率 |
|------|---------|---------|---------|---------|---------|
| RotaryVelocity (小) | DC 电机 12V 10W | 0 | 10 | 15 | 70% |
| RotaryVelocity (中) | BLDC 轮毂 150W | 2 | 150 | 300 | 85% |
| RotaryVelocity (大) | BLDC 500W | 5 | 500 | 1000 | 90% |
| RotaryPosition | 舵机 MG996 | 0.1 | 3 | 6 | — |
| RotaryPosition | 步进 NEMA17 | 1 | 10 | 15 | 60% |
| LinearPosition | 电动推杆 12V | 0 | 30 | 50 | 65% |
| BinaryActuator | 电磁阀 | 0 | 5 | 8 | — |
| BinaryActuator | 继电器 | 0.5 | 1 | 1 | — |
| Pump (小) | 蠕动泵 12V | 0 | 10 | 15 | — |
| Pump (中) | 隔膜泵 12V | 0.5 | 50 | 70 | — |
| Pump (大) | 离心泵 24V | 1 | 200 | 350 | — |
| Gripper | 舵机夹爪 | 0.1 | 3 | 6 | — |
| EnergyBeam | 激光 150W | 2 | 150 | 180 | 30-50% |
| EnergyBeam | UV LED 30W | 0.5 | 30 | 35 | 40% |
| EnergyBeam | 红外加热 300W | 1 | 300 | 320 | 90% |

### 10.3 功耗声明接口

```python
@dataclass
class PowerSpec:
    standby_w: float = 0.0        # 待机功耗 (启用但未动作)
    rated_w: float = 0.0          # 额定功率 (正常负载)
    peak_w: float = 0.0           # 峰值功率 (启动/堵转)
    efficiency: float = 1.0       # 转换效率 (0-1)
    voltage: float = 12.0         # 额定电压 (V)

# 每个 Actuator 必须实现
class RotaryVelocity:
    def get_power_spec(self) -> PowerSpec:
        return PowerSpec(
            standby_w=2.0,
            rated_w=abs(self._speed) / self._max_speed * 150,
            peak_w=300,
            efficiency=0.85,
            voltage=24.0
        )

    def get_current_power(self) -> float:
        """当前实际功耗"""
        if self.state == ActuatorState.IDLE:
            return self.get_power_spec().standby_w
        load_ratio = abs(self._speed) / max(1, self._max_speed)
        return load_ratio * self.get_power_spec().rated_w
```

### 10.4 系统级功耗汇总

```python
class PowerBudget:
    """每个决策前检查总功耗是否超预算"""
    
    def __init__(self, total_budget_w: float):
        self.total_budget = total_budget_w
    
    def check(self, planned_actions: list[dict]) -> dict:
        """预估动作序列的总功耗"""
        total = 0.0
        breakdown = {}
        
        for action in planned_actions:
            act_name = action["actuator"]
            actuator = self.actuators[act_name]
            power = actuator.get_current_power()
            total += power
            breakdown[act_name] = power
        
        return {
            "total_w": total,
            "breakdown": breakdown,
            "within_budget": total <= self.total_budget,
            "headroom_w": self.total_budget - total,
        }
```

---

## 11. STM32 命令字映射

### 11.1 通信帧格式 (重述)

```
┌──────┬──────┬──────────┬──────────┬──────────┬──────┐
│SYNC  │ ADDR │ TYPE     │ CMD      │ PAYLOAD  │ CRC  │
│0xAA  │ 1B   │ 1B       │ 1B       │ N bytes  │ 2B   │
└──────┴──────┴──────────┴──────────┴──────────┴──────┘

SYNC:  0xAA — 帧同步头
ADDR:  执行器地址 (0x01-0x7F, 0x00=广播)
TYPE:  原语类型
CMD:   具体命令
PAYLOAD: 可变长度数据
CRC:   CRC-16/MODBUS (全帧校验，不含SYNC)
```

### 11.2 原语类型编码

| TYPE | 原语 | 说明 |
|------|------|------|
| `0x01` | RotaryVelocity | 连续旋转+速度 |
| `0x02` | RotaryPosition | 角度定位 |
| `0x03` | LinearPosition | 直线定位 |
| `0x04` | BinaryActuator | 开关 |
| `0x05` | Pump | 流体控制 |
| `0x06` | Gripper | 抓取 |
| `0x07` | EnergyBeam | 能量束 |
| `0x00` | 广播/系统 | 急停、查询、心跳 |

### 11.3 各原语命令字

#### 11.3.1 RotaryVelocity (TYPE=0x01)

| CMD | 名称 | Payload | 响应 | 说明 |
|------|------|------|------|------|
| `0x10` | SET_SPEED | int16 (rpm, 小端) | ACK + int16 (实际rpm) | 正=正转, 负=反转 |
| `0x11` | STOP | — | ACK | 减速到0 |
| `0x12` | GET_SPEED | — | ACK + int16 (rpm) | 查询当前转速 |
| `0x1F` | GET_STATE | — | ACK + state_byte | 查询状态 |

```
示例: 左电机 (ADDR=0x01) 设置 1200 rpm
  →  0xAA 01 01 10  04B0   [CRC]
              设置  +1200 (0x04B0=1200 小端)
  
  响应: 0xAA 01 01 10  04B0  04AD [CRC]
                       设置  实际1197rpm (0x04AD)
```

#### 11.3.2 RotaryPosition (TYPE=0x02)

| CMD | 名称 | Payload | 说明 |
|------|------|------|------|
| `0x20` | SET_ANGLE | int16 (角度×10, 小端) | 如45.5° → 455 (0x01C7) |
| `0x21` | GET_ANGLE | — | 返回 int16 (角度×10) |
| `0x22` | SET_ZERO | — | 当前位置标记为0点 |

#### 11.3.3 LinearPosition (TYPE=0x03)

| CMD | 名称 | Payload | 说明 |
|------|------|------|------|
| `0x30` | SET_POSITION | int16 (mm×10, 小端) | 如125.5mm → 1255 |
| `0x31` | GET_POSITION | — | 返回 int16 |
| `0x32` | HOME | — | 回零点 |

#### 11.3.4 BinaryActuator (TYPE=0x04)

| CMD | 名称 | Payload | 说明 |
|------|------|------|------|
| `0x40` | ON | — | 开启 |
| `0x41` | OFF | — | 关闭 |
| `0x42` | TOGGLE | — | 翻转 |
| `0x43` | PULSE | uint16 (ms, 小端) | 开启N毫秒后自动关 |

#### 11.3.5 Pump (TYPE=0x05)

| CMD | 名称 | Payload | 说明 |
|------|------|------|------|
| `0x50` | SET_FLOW | uint16 (流量×100, 小端) | 如0.5 L/min → 50 |
| `0x51` | STOP | — | 停止 |
| `0x52` | GET_FLOW | — | 返回 uint16 |

#### 11.3.6 Gripper (TYPE=0x06)

| CMD | 名称 | Payload | 说明 |
|------|------|------|------|
| `0x60` | GRASP | — | 抓取 |
| `0x61` | RELEASE | — | 释放 |
| `0x62` | GRASP_FORCE | uint16 (力×10, N, 小端) | 以指定力抓取 |

#### 11.3.7 EnergyBeam (TYPE=0x07)

| CMD | 名称 | Payload | 说明 |
|------|------|------|------|
| `0x70` | SET_POWER | uint16 (W, 小端) | 设置功率 |
| `0x71` | FIRE | uint16 (ms, 小端) | 发射指定时长 |
| `0x72` | STOP | — | 停止发射 |

> ⚠ **EnergyBeam 需要双因子触发**: 软件命令 + STM32 硬件 Enable 引脚同时为高才发射。

#### 11.3.8 系统命令 (TYPE=0x00, ADDR=0x00 广播)

| CMD | 名称 | Payload | 说明 |
|------|------|------|------|
| `0xFF` | EMERGENCY_STOP | — | 所有执行器急停 (广播) |
| `0xFE` | HEARTBEAT_REQ | — | 请求心跳 |
| `0xFD` | HEARTBEAT_ACK | uint8 (状态) | 心跳回复 |
| `0xFC` | QUERY_CAPABILITY | — | 查询该地址执行器能力 |
| `0xFB` | CAPABILITY_REPORT | 结构化数据 | 回传能力描述 |
| `0xFA` | SHUTDOWN | — | 准备断电 |

### 11.4 响应帧格式

```
┌──────┬──────┬──────────┬──────────┬──────────┬──────────┬──────┐
│SYNC  │ ADDR │ TYPE     │ CMD      │ STATUS   │ DATA     │ CRC  │
│0xAA  │ 1B   │ 1B       │ 1B       │ 1B       │ N bytes  │ 2B   │
└──────┴──────┴──────────┴──────────┴──────────┴──────────┴──────┘

STATUS:
  0x00 = ACK (成功)
  0x01 = NACK (参数错误)
  0x02 = BUSY (前一个命令未完成)
  0x03 = ERROR (故障, DATA字段含错误码)
  0x04 = NOT_READY (未初始化或自检中)
```

### 11.5 端侧发送示例

```python
class STM32CommandEncoder:
    """将 Actuator 动作编码为 STM32 二进制帧"""
    
    def encode(self, action: dict) -> bytes:
        actuator_id = action["actuator"]  # "left_motor"
        action_name = action["action"]    # "set_speed"
        params = action.get("params", {})
        
        addr = self.get_address(actuator_id)
        actuator_type = self.get_type(actuator_id)
        cmd_code, payload = self._encode_action(actuator_type, action_name, params)
        
        frame = bytes([0xAA, addr, actuator_type, cmd_code]) + payload
        crc = crc16_modbus(frame)  # 校验 SYNC 之后的所有字节
        return frame + crc.to_bytes(2, 'little')
    
    def _encode_action(self, atype: int, action: str, params: dict):
        if atype == 0x01:  # RotaryVelocity
            if action == "set_speed":
                rpm = int(params["rpm"])
                return 0x10, rpm.to_bytes(2, 'little', signed=True)
            elif action == "stop":
                return 0x11, b''
        elif atype == 0x02:  # RotaryPosition
            if action == "set_angle":
                angle_x10 = int(params["deg"] * 10)
                return 0x20, angle_x10.to_bytes(2, 'little', signed=True)
        # ... 其他原语和动作
    
    def decode_response(self, frame: bytes) -> dict:
        """解析 STM32 的响应帧 → Python dict"""
        addr, atype, cmd, status = frame[1], frame[2], frame[3], frame[4]
        data = frame[5:-2]  # 去掉 CRC
        
        return {
            "address": addr,
            "type": atype,
            "command": cmd,
            "status": ["ACK", "NACK", "BUSY", "ERROR", "NOT_READY"][status],
            "data": data.hex(),
        }
```

---

## 12. 标定参数格式

### 12.1 为什么需要标定

执行器的参数不是"装上去就对"的。同一型号的不同个体有差异，安装时也有机械偏差。标定数据是让软件"认识"具体硬件的桥梁。

### 12.2 各原语需标定的参数

| 原语 | 标定项 | 标定方法 | 持久化 |
|------|--------|---------|--------|
| RotaryVelocity | PID 增益 (kp/ki/kd) | 阶跃响应测试 → 调参 | ✅ |
| RotaryVelocity | 编码器零点偏移 | 静止时读编码器值 | ✅ |
| RotaryPosition | 机械零点 | 限位开关 + 回零 | ✅ |
| RotaryPosition | 角度映射 (PWM→角度) | 三点标定 (0°, 90°, 180°) | ✅ |
| LinearPosition | 机械零点 + 限位 | 硬限位触发位置 | ✅ |
| LinearPosition | 步进/mm 换算比 | 移动100mm → 计数脉冲 | ✅ |
| BinaryActuator | — (无需标定) | — | — |
| Pump | 流量曲线 (PWM→L/min) | 量筒实测 | ✅ |
| Gripper | 开合力曲线 | 力传感器标定 | ✅ |
| EnergyBeam | 功率校准 | 功率计实测 vs 设定值 | ✅ |
| 全局 | 电流传感器零偏 | 断电时读取 ADC 值 | ✅ |

### 12.3 标定数据格式

```yaml
# /etc/telos/calibration.yaml
# 每个执行器单独一段，用执行器 name 做 key

left_motor:
  type: rotary_velocity
  pid:
    kp: 0.52
    ki: 0.08
    kd: 0.03
    integral_limit: 100
  encoder_offset: 17       # 静止时编码器读数 (应归0)
  deadband_rpm: 5          # 死区: 低于此转速电机不转
  forward_reverse_symmetric: false  # 正反转不对称
  calibration_date: "2026-06-23"
  calibrated_by: "zhangshuaiqing"

steering:
  type: rotary_position
  pwm_angle_map:           # 三点标定
    0: [500, 0.0]          # [PWM值, 实际角度°]
    1: [1500, 90.0]
    2: [2500, 180.0]
  home_offset: -2.3        # 零点偏移 (°)
  gear_backlash: 1.5       # 齿轮回差 (°)
  calibration_date: "2026-06-23"

spray_pump:
  type: pump
  flow_curve:              # PWM占空比 → 流量映射
    - [0.0, 0.0]           # [占空比%, 流量 L/min]
    - [30.0, 0.15]
    - [60.0, 0.45]
    - [100.0, 0.92]
  calibration_date: "2026-06-23"

laser_weeder:
  type: energy_beam
  power_calibration:       # 设定值 → 实际输出
    - [10, 9.2]            # [设定W, 实测W]
    - [50, 48.7]
    - [100, 101.3]
    - [150, 147.5]
  calibration_date: "2026-06-23"
  calibrated_by: "optical_power_meter_sn123"
```

### 12.4 标定数据结构

```python
@dataclass
class CalibrationData:
    """标定数据的运行时表示"""
    actuator_name: str
    actuator_type: str
    params: dict               # 原始标定数据
    timestamp: float
    checksum: str              # SHA256 防篡改
    
    @classmethod
    def load(cls, path: str) -> dict[str, "CalibrationData"]:
        """加载所有执行器的标定数据"""
        import yaml
        with open(path) as f:
            raw = yaml.safe_load(f)
        return {name: cls(name, cfg["type"], cfg, ...) 
                for name, cfg in raw.items()}
    
    def apply(self, actuator: Actuator) -> None:
        """将标定数据应用到执行器实例"""
        if self.actuator_type == "rotary_velocity":
            actuator.pid = PID(**self.params["pid"])
            actuator.encoder_offset = self.params["encoder_offset"]
        elif self.actuator_type == "pump":
            actuator.flow_curve = self.params["flow_curve"]
        # ... 其他类型

class CalibrationManager:
    """标定管理器 — 加载、验证、热更新"""
    
    def __init__(self, path: str = "/etc/telos/calibration.yaml"):
        self._path = path
        self._data: dict[str, CalibrationData] = {}
    
    def load(self) -> None:
        self._data = CalibrationData.load(self._path)
    
    def apply_to(self, executor: Executor) -> list[str]:
        """对所有已注册执行器应用标定，返回应用失败的列表"""
        failed = []
        for actuator in executor.actuators():
            cal = self._data.get(actuator.name)
            if cal:
                try:
                    cal.apply(actuator)
                except Exception as e:
                    failed.append(f"{actuator.name}: {e}")
            else:
                failed.append(f"{actuator.name}: 无标定数据，使用默认值")
        return failed
    
    def validate_checksum(self) -> bool:
        """验证标定文件完整性"""
        import hashlib
        with open(self._path, 'rb') as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        return actual == self._manifest_checksum
```

### 12.5 标定流程集成

标定流程集成在 Ch12 启动自检中：

```
Phase 3: 执行器自检
  ├── 加载标定数据
  │     ├── 文件存在? ──NO──▶ 使用默认值 + 标记 "未标定"
  │     ├── SHA256 校验通过? ──NO──▶ 告警 "标定文件损坏"
  │     └── 应用标定 → 所有执行器
  │
  ├── 执行自检 (用标定后的参数)
  │
  └── 标定状态报告:
       "3 个执行器已标定, 1 个使用默认值 (steering 未标定)"
```
