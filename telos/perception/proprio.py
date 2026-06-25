"""本体感通道 — IMU / 编码器 / 电池状态"""


class ProprioChannel:
    """本体感觉输入 — 速度、姿态、电量等"""

    name = "proprio"
    priority = 2

    def __init__(self):
        self._active = False
        self._speed = 0.0       # m/s
        self._heading = 0.0     # 度 (0=正北)
        self._battery = 100.0   # %
        self._roll = 0.0        # 度
        self._pitch = 0.0       # 度
        self._motors = {}       # {"left": {"rpm": 100, "current": 2.0}, "right": ...}

    def start(self) -> bool:
        self._active = True
        return True

    def stop(self) -> None:
        self._active = False

    def capture(self) -> dict:
        return {
            "speed": self._speed,
            "heading": self._heading,
            "battery": self._battery,
            "roll": self._roll,
            "pitch": self._pitch,
            "motors": self._motors,
        }

    def update(self, speed: float = None, heading: float = None,
               battery: float = None, roll: float = None,
               pitch: float = None, motors: dict = None) -> None:
        """从 STM32 遥测更新状态"""
        if speed is not None: self._speed = speed
        if heading is not None: self._heading = heading
        if battery is not None: self._battery = battery
        if roll is not None: self._roll = roll
        if pitch is not None: self._pitch = pitch
        if motors is not None: self._motors = motors

    def health(self) -> dict:
        return {"name": "proprio", "active": self._active,
                "battery": self._battery}
