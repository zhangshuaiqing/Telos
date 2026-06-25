"""执行器原语 — 7 种基本执行类型的统一接口

所有执行器实现 Actuator 协议。7 种原语覆盖所有机械+能量输出场景。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Optional, Any


class ActuatorState(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    DISCONNECTED = "disconnected"


@dataclass
class ActuatorCapability:
    """执行器能力描述 — 供云端 LLM 了解可用工具"""
    id: str
    name: str
    type: str
    description: str
    actions: list[str] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)


class Actuator(Protocol):
    """所有执行器原语必须实现此接口"""

    name: str
    state: ActuatorState

    def init(self) -> bool: ...
    def get_capability(self) -> ActuatorCapability: ...
    def get_state(self) -> dict: ...
    def emergency_stop(self) -> None: ...


@dataclass
class ExecutorResult:
    """执行结果"""
    success: bool
    actuator_id: str
    action: str
    params: dict
    error: Optional[str] = None
    state_after: Optional[dict] = None


class Executor:
    """执行器编排器
    
    接收 Agent 的动作决策 → 安全校验 → 分发到对应执行器 → 收集结果
    """

    def __init__(self):
        self._actuators: dict[str, Actuator] = {}

    def register(self, actuator: Actuator) -> None:
        self._actuators[actuator.name] = actuator

    def get_capabilities(self) -> list[ActuatorCapability]:
        return [a.get_capability() for a in self._actuators.values()]

    def execute(self, command: dict) -> ExecutorResult:
        """执行一条指令

        command: {"actuator": "spray_pump", "action": "set_flow",
                   "params": {"flow": 0.5}}
        """
        actuator_id = command.get("actuator")
        if actuator_id not in self._actuators:
            return ExecutorResult(
                success=False, actuator_id=actuator_id,
                action=command.get("action", ""), params=command.get("params", {}),
                error=f"执行器 '{actuator_id}' 未注册"
            )

        act = self._actuators[actuator_id]
        action = command.get("action", "")
        params = command.get("params", {})

        try:
            # 调用具体执行器的方法
            method = getattr(act, action, None)
            if method is None:
                raise ValueError(f"执行器 '{actuator_id}' 不支持动作 '{action}'")
            result_value = method(**params)
            return ExecutorResult(
                success=True, actuator_id=actuator_id,
                action=action, params=params,
                state_after={"result": result_value}
            )
        except Exception as e:
            return ExecutorResult(
                success=False, actuator_id=actuator_id,
                action=action, params=params, error=str(e)
            )

    def emergency_stop_all(self) -> None:
        """全部急停"""
        for act in self._actuators.values():
            try:
                act.emergency_stop()
            except Exception:
                pass
