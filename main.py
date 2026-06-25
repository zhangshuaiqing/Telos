"""Telos Agent — 启动入口

用法:
  python main.py              # 默认配置启动
  python main.py --task "..." # 指定任务
"""

import os
import argparse
from telos import TelosAgent, AgentConfig
from telos.perception.vision import VisionChannel
from telos.perception.voice import VoiceChannel
from telos.perception.proprio import ProprioChannel
from telos.actuators.primitives import (
    RotaryVelocity, RotaryPosition, LinearPosition,
    BinaryActuator, Pump, Gripper, EnergyBeam,
)


def build_agent(config: AgentConfig) -> TelosAgent:
    """构建一个配置好感知和执行器的 Agent"""
    agent = TelosAgent(config)

    # 注册感知通道
    agent.register_perception(VisionChannel())
    agent.register_perception(VoiceChannel())
    agent.register_perception(ProprioChannel())

    # 注册执行器 (示例 — 实际使用时替换为真实硬件)
    agent.register_actuator(RotaryVelocity("left_motor"))
    agent.register_actuator(RotaryVelocity("right_motor"))
    agent.register_actuator(RotaryPosition("steering"))

    return agent


def main():
    parser = argparse.ArgumentParser(description="Telos Robot Agent")
    parser.add_argument("--task", default="探索并理解周围环境",
                        help="任务描述")
    parser.add_argument("--steps", type=int, default=100,
                        help="最大步数")
    parser.add_argument("--provider", default="deepseek",
                        help="LLM 提供商 (deepseek/kimi)")
    parser.add_argument("--model", default="deepseek-chat",
                        help="模型名称")
    parser.add_argument("--dry-run", action="store_true",
                        help="空跑模式 — 不使用真实 API")
    args = parser.parse_args()

    config = AgentConfig(
        task=args.task,
        max_steps=args.steps,
        llm_provider=args.provider,
        llm_model=args.model,
    )

    agent = build_agent(config)

    if args.dry_run:
        print("🔬 空跑模式 — 执行一次闭环演示")
        obs = agent.perception.observe()
        print(f"\n📡 感知结果:")
        print(obs.to_prompt_text())

        caps = agent.executor.get_capabilities()
        print(f"\n🔧 可用执行器: {len(caps)} 个")
        for c in caps:
            print(f"  - {c.id} [{c.type}]: {c.description}")

        print(f"\n🧠 任务: {config.task}")
        print("(跳过 LLM API 调用)")
    else:
        print(f"🤖 Telos Agent 启动")
        print(f"   任务: {config.task}")
        print(f"   模型: {config.llm_provider}/{config.llm_model}")
        agent.run()
        print(f"\n✅ 完成，共 {agent.step_count} 步")


if __name__ == "__main__":
    main()
