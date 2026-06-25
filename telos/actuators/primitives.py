"""RotaryVelocity — 连续旋转速度控制

对应硬件: VESC / DC电机 / BLDC
应用场景: 底盘驱动、刀片旋转、风扇
"""

from .base import ActuatorCapability, ActuatorState


class RotaryVelocity:
    """连续旋转，速度控制"""

    name: str
    state: ActuatorState = ActuatorState.DISCONNECTED
    _speed: float = 0.0  # rpm
    _max_speed: float = 3000.0

    def __init__(self, name: str, max_speed: float = 3000.0,
                 forward_action: str = "set_speed",
                 reverse_action: str = "set_speed"):
        self.name = name
        self._max_speed = max_speed
        self._forward_action = forward_action
        self._reverse_action = reverse_action

    def init(self) -> bool:
        self.state = ActuatorState.IDLE
        return True

    def get_capability(self) -> ActuatorCapability:
        return ActuatorCapability(
            id=self.name,
            name=self.name,
            type="rotary_velocity",
            description=f"连续旋转电机，最大 {self._max_speed} rpm",
            actions=["set_speed", "stop", "get_speed"],
            constraints={"max_speed": self._max_speed, "unit": "rpm"},
        )

    def get_state(self) -> dict:
        return {"name": self.name, "state": self.state.value, "speed": self._speed}

    def emergency_stop(self) -> None:
        self._speed = 0.0
        self.state = ActuatorState.IDLE

    def set_speed(self, rpm: float) -> dict:
        """设置转速 (rpm)，正=正转，负=反转"""
        self._speed = max(-self._max_speed, min(self._max_speed, rpm))
        self.state = ActuatorState.BUSY
        return {"speed": self._speed}

    def stop(self) -> dict:
        self._speed = 0.0
        self.state = ActuatorState.IDLE
        return {"speed": 0.0}

    def get_speed(self) -> float:
        return self._speed


class RotaryPosition:
    """角位置定位

    对应硬件: 舵机 / 步进电机(位置模式)
    应用场景: 转向、关节角度、振镜偏转
    """

    name: str
    state: ActuatorState = ActuatorState.DISCONNECTED
    _angle: float = 0.0  # 度
    _min_angle: float = -90.0
    _max_angle: float = 90.0

    def __init__(self, name: str, min_angle: float = -90.0, max_angle: float = 90.0):
        self.name = name
        self._min_angle = min_angle
        self._max_angle = max_angle

    def init(self) -> bool:
        self.state = ActuatorState.IDLE
        return True

    def get_capability(self) -> ActuatorCapability:
        return ActuatorCapability(
            id=self.name, name=self.name, type="rotary_position",
            description=f"角度定位，范围 [{self._min_angle}, {self._max_angle}]°",
            actions=["set_angle", "get_angle"],
            constraints={"min": self._min_angle, "max": self._max_angle, "unit": "degree"},
        )

    def get_state(self) -> dict:
        return {"name": self.name, "state": self.state.value, "angle": self._angle}

    def emergency_stop(self) -> None:
        self.state = ActuatorState.IDLE

    def set_angle(self, deg: float) -> dict:
        self._angle = max(self._min_angle, min(self._max_angle, deg))
        self.state = ActuatorState.BUSY
        return {"angle": self._angle}

    def get_angle(self) -> float:
        return self._angle


class LinearPosition:
    """直线位置控制

    对应硬件: 电动推杆 / 丝杆步进 / 气缸+位置反馈
    应用场景: 升降台、夹爪开合、推板
    """

    name: str
    state: ActuatorState = ActuatorState.DISCONNECTED
    _position: float = 0.0  # mm
    _min_position: float = 0.0
    _max_position: float = 100.0

    def __init__(self, name: str, min_pos: float = 0.0, max_pos: float = 100.0):
        self.name = name
        self._min_position = min_pos
        self._max_position = max_pos

    def init(self) -> bool:
        self.state = ActuatorState.IDLE
        return True

    def get_capability(self) -> ActuatorCapability:
        return ActuatorCapability(
            id=self.name, name=self.name, type="linear_position",
            description=f"直线位置，行程 [{self._min_position}, {self._max_position}] mm",
            actions=["set_position", "get_position"],
            constraints={"min": self._min_position, "max": self._max_position, "unit": "mm"},
        )

    def get_state(self) -> dict:
        return {"name": self.name, "state": self.state.value, "position": self._position}

    def emergency_stop(self) -> None:
        self.state = ActuatorState.IDLE

    def set_position(self, mm: float) -> dict:
        self._position = max(self._min_position, min(self._max_position, mm))
        self.state = ActuatorState.BUSY
        return {"position": self._position}

    def get_position(self) -> float:
        return self._position


class BinaryActuator:
    """开关控制

    对应硬件: 电磁阀 / 继电器 / 电磁铁 / MOSFET / 电磁锁
    应用场景: 阀门开闭、电源通断、锁止
    """

    name: str
    state: ActuatorState = ActuatorState.DISCONNECTED
    _on: bool = False

    def __init__(self, name: str):
        self.name = name

    def init(self) -> bool:
        self.state = ActuatorState.IDLE
        return True

    def get_capability(self) -> ActuatorCapability:
        return ActuatorCapability(
            id=self.name, name=self.name, type="binary",
            description="开关型执行器",
            actions=["on", "off", "toggle"],
            constraints={},
        )

    def get_state(self) -> dict:
        return {"name": self.name, "state": self.state.value, "on": self._on}

    def emergency_stop(self) -> None:
        self._on = False
        self.state = ActuatorState.IDLE

    def on(self) -> dict:
        self._on = True
        self.state = ActuatorState.BUSY
        return {"on": True}

    def off(self) -> dict:
        self._on = False
        self.state = ActuatorState.IDLE
        return {"on": False}

    def toggle(self) -> dict:
        return self.on() if not self._on else self.off()


class Pump:
    """流体/气体输出控制

    对应硬件: 水泵 / 蠕动泵 / 离心泵 / 气泵 / 真空泵
    应用场景: 喷雾、灌溉、吸尘、吸取
    """

    name: str
    state: ActuatorState = ActuatorState.DISCONNECTED
    _flow: float = 0.0  # 流量
    _max_flow: float = 1.0
    _flow_unit: str = "L/min"

    def __init__(self, name: str, max_flow: float = 1.0, unit: str = "L/min"):
        self.name = name
        self._max_flow = max_flow
        self._flow_unit = unit

    def init(self) -> bool:
        self.state = ActuatorState.IDLE
        return True

    def get_capability(self) -> ActuatorCapability:
        return ActuatorCapability(
            id=self.name, name=self.name, type="pump",
            description=f"流体控制，最大 {self._max_flow} {self._flow_unit}",
            actions=["set_flow", "stop"],
            constraints={"max_flow": self._max_flow, "unit": self._flow_unit},
        )

    def get_state(self) -> dict:
        return {"name": self.name, "state": self.state.value, "flow": self._flow}

    def emergency_stop(self) -> None:
        self._flow = 0.0
        self.state = ActuatorState.IDLE

    def set_flow(self, rate: float) -> dict:
        self._flow = max(0.0, min(self._max_flow, rate))
        self.state = ActuatorState.BUSY if self._flow > 0 else ActuatorState.IDLE
        return {"flow": self._flow}

    def stop(self) -> dict:
        self._flow = 0.0
        self.state = ActuatorState.IDLE
        return {"flow": 0.0}


class Gripper:
    """抓取控制

    对应硬件: 夹爪（舵机驱动、气动、电动）
    应用场景: 采摘果实、取物、搬运
    """

    name: str
    state: ActuatorState = ActuatorState.DISCONNECTED
    _closed: bool = False
    _max_force: float = 10.0  # N
    _has_force_feedback: bool = False

    def __init__(self, name: str, max_force: float = 10.0, force_feedback: bool = False):
        self.name = name
        self._max_force = max_force
        self._has_force_feedback = force_feedback

    def init(self) -> bool:
        self.state = ActuatorState.IDLE
        return True

    def get_capability(self) -> ActuatorCapability:
        actions = ["grasp", "release"]
        if self._has_force_feedback:
            actions.append("grasp_with_force")
        return ActuatorCapability(
            id=self.name, name=self.name, type="gripper",
            description=f"抓取，最大力 {self._max_force}N" +
                        ("，带力反馈" if self._has_force_feedback else ""),
            actions=actions,
            constraints={"max_force": self._max_force, "force_unit": "N"},
        )

    def get_state(self) -> dict:
        return {"name": self.name, "state": self.state.value, "closed": self._closed}

    def emergency_stop(self) -> None:
        self._closed = False
        self.state = ActuatorState.IDLE

    def grasp(self) -> dict:
        self._closed = True
        self.state = ActuatorState.BUSY
        return {"closed": True}

    def release(self) -> dict:
        self._closed = False
        self.state = ActuatorState.IDLE
        return {"closed": False}

    def grasp_with_force(self, force: float) -> dict:
        f = max(0.0, min(self._max_force, force))
        self._closed = True
        self.state = ActuatorState.BUSY
        return {"closed": True, "force": f}


class EnergyBeam:
    """定向能量输出

    对应硬件: 激光二极管 / CO₂激光管 / 红外加热器 / UV杀菌灯
    应用场景: 激光除草、红外加热、紫外消毒
    """

    name: str
    state: ActuatorState = ActuatorState.DISCONNECTED
    _power: float = 0.0  # W
    _max_power: float = 150.0
    _wavelength: str = "unknown"  # nm or band

    def __init__(self, name: str, max_power: float = 150.0, wavelength: str = "unknown"):
        self.name = name
        self._max_power = max_power
        self._wavelength = wavelength

    def init(self) -> bool:
        self.state = ActuatorState.IDLE
        return True

    def get_capability(self) -> ActuatorCapability:
        return ActuatorCapability(
            id=self.name, name=self.name, type="energy_beam",
            description=f"定向能量输出，最大 {self._max_power}W, 波长 {self._wavelength}",
            actions=["fire", "stop", "set_power"],
            constraints={"max_power": self._max_power, "unit": "W", "wavelength": self._wavelength},
        )

    def get_state(self) -> dict:
        return {"name": self.name, "state": self.state.value, "power": self._power}

    def emergency_stop(self) -> None:
        """硬件级关断 — 激光安全最高优先级"""
        self._power = 0.0
        self.state = ActuatorState.IDLE

    def set_power(self, watts: float) -> dict:
        self._power = max(0.0, min(self._max_power, watts))
        return {"power": self._power}

    def fire(self, power: float = None, duration_ms: int = 500) -> dict:
        """发射指定功率和时长"""
        if power is not None:
            self.set_power(power)
        if self._power > 0:
            self.state = ActuatorState.BUSY
        return {"power": self._power, "duration_ms": duration_ms}

    def stop(self) -> dict:
        self._power = 0.0
        self.state = ActuatorState.IDLE
        return {"power": 0.0}
