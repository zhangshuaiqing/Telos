"""TelosAgent — 完整闭环的主循环

世界 → 感知 → 认知 → 决策 → 执行 → 记忆 → 循环
"""

from dataclasses import dataclass, field
from typing import Optional

from telos.observation import Observation
from telos.perception.manager import PerceptionManager
from telos.cognition.engine import CognitionEngine, CognitionDecision
from telos.actuators.base import Executor, ExecutorResult
from telos.memory.memory import WorkingMemory, EpisodicMemory


@dataclass
class AgentConfig:
    """Agent 运行配置"""
    task: str = "探索并理解周围环境"  # 当前任务描述
    max_steps: int = 1000
    safety_speed_limit: float = 1.0  # m/s, 任何决策不能超过此速度
    safety_power_limit: float = 100.0  # W, 能量输出上限
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"


class TelosAgent:
    """Telos 核心 Agent — 完整闭环"""

    def __init__(self, config: AgentConfig = None):
        self.config = config or AgentConfig()
        self.perception = PerceptionManager()
        self.executor = Executor()
        self.cognition = CognitionEngine(
            provider=self.config.llm_provider,
            model=self.config.llm_model,
        )
        self.working_memory = WorkingMemory()
        self.episodic_memory = EpisodicMemory()
        self.step_count = 0
        self.running = False

    def register_perception(self, channel) -> None:
        self.perception.register(channel)

    def register_actuator(self, actuator) -> None:
        self.executor.register(actuator)

    def step(self) -> Optional[dict]:
        """单次闭环循环

        Returns: 执行结果汇总，或 None(停止)
        """
        self.step_count += 1

        # 1. 感知
        obs = self.perception.observe()

        # 2. 认知决策
        capabilities = self.executor.get_capabilities()
        memory_context = self.working_memory.summary()
        decision = self.cognition.think(
            obs=obs,
            task=self.config.task,
            capabilities=[
                {"id": c.id, "type": c.type, "description": c.description,
                 "actions": c.actions, "constraints": c.constraints}
                for c in capabilities
            ],
            memory_context=memory_context,
        )

        # 3. 安全检查
        decision = self._safety_check(decision)

        # 4. 执行动作序列
        results = []
        for action_cmd in decision.actions:
            result = self.executor.execute(action_cmd)
            results.append(result)
            self.episodic_memory.record(
                step=self.step_count,
                action_type=decision.action_type,
                action=action_cmd,
                result="ok" if result.success else "fail",
                error=result.error,
            )

        # 5. 更新工作记忆
        self.working_memory.add({
            "step": self.step_count,
            "action_type": decision.action_type,
            "actions": decision.actions,
            "thought": decision.thought,
        })

        # 6. 语音输出 (如果有)
        if decision.action_type == "speak" and decision.context.get("speech"):
            self._speak(decision.context["speech"])

        return {
            "step": self.step_count,
            "thought": decision.thought,
            "actions": decision.actions,
            "results": [{"success": r.success, "error": r.error} for r in results],
        }

    def run(self):
        """持续运行直到停止"""
        self.running = True
        self.perception.start_all()

        try:
            while self.running and self.step_count < self.config.max_steps:
                result = self.step()
                if result is None:
                    break
        finally:
            self.perception.stop_all()

    def stop(self):
        self.running = False
        self.executor.emergency_stop_all()

    # ── 安全校验 ──────────────────────────

    def _safety_check(self, decision: CognitionDecision) -> CognitionDecision:
        """校验并修正不安全的决策"""
        for action in decision.actions:
            params = action.get("params", {})

            # 速度上限
            if "speed" in params or "rpm" in params:
                speed_key = "speed" if "speed" in params else "rpm"
                if abs(params[speed_key]) > self.config.safety_speed_limit:
                    params[speed_key] = self.config.safety_speed_limit

            # 能量上限
            if "power" in params or "watts" in params:
                power_key = "power" if "power" in params else "watts"
                if params[power_key] > self.config.safety_power_limit:
                    params[power_key] = self.config.safety_power_limit

        return decision

    def _speak(self, text: str) -> None:
        """输出语音"""
        try:
            from telos.perception.voice import VoiceOutput
            VoiceOutput().speak_sync(text)
        except Exception:
            pass
