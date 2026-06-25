"""认知引擎 — 云端 LLM 推理

将 Observation 转化为结构化决策。
支持 DeepSeek / Kimi / OpenAI 兼容 API。
"""

import json
import os
from dataclasses import dataclass
from typing import Optional
from telos.observation import Observation


@dataclass
class CognitionDecision:
    """认知引擎的输出"""
    action_type: str  # "move", "speak", "actuate", "wait", "ask"
    actions: list[dict]  # 具体动作序列
    thought: str = ""  # LLM 的推理过程
    context: dict = None  # 附加上下文

    def __post_init__(self):
        if self.context is None:
            self.context = {}


class CognitionEngine:
    """基于云端 LLM API 的认知推理引擎"""

    CAPABILITIES_PROMPT = """
你是机器人大脑的认知核心。你基于多模态感知信息来做决定。

## 当前可用的执行器
{actuator_capabilities}

## 输出格式
请以 JSON 格式输出决策，格式为:
{{
  "thought": "你的推理过程",
  "action_type": "move|speak|actuate|wait|ask",
  "actions": [
    {{"actuator": "执行器名", "action": "动作名", "params": {{...}}}}
  ]
}}

规则:
- action_type "move": 控制底盘移动
- action_type "speak": 语音回复用户
- action_type "actuate": 操作执行器 (喷雾、抓取、激光等)
- action_type "wait": 等待观察
- action_type "ask": 需要向用户提问
- 动作序列按顺序执行
"""

    def __init__(self, provider: str = "deepseek",
                 model: str = "deepseek-chat",
                 api_key: str = None,
                 base_url: str = None):
        self.provider = provider
        self.model = model
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = base_url or "https://api.deepseek.com/v1"

    def think(self, obs: Observation,
              task: str,
              capabilities: list[dict],
              memory_context: str = "") -> CognitionDecision:
        """思考 → 决策"""
        prompt = self._build_prompt(obs, task, capabilities, memory_context)
        response_text = self._call_api(prompt)
        return self._parse_response(response_text)

    def _build_prompt(self, obs: Observation, task: str,
                      capabilities: list[dict], memory: str) -> str:
        """构建完整 prompt"""
        cap_text = "\n".join(
            f"- {c['id']} ({c['type']}): {c.get('description', '')}"
            for c in capabilities
        )

        system_prompt = self.CAPABILITIES_PROMPT.format(
            actuator_capabilities=cap_text
        )

        user_prompt = f"""## 当前任务
{task}

## 机器人状态
{obs.to_prompt_text()}

## 场景记忆
{memory or '(无)'}

请输出你的决策 (JSON):"""

        return system_prompt + "\n\n" + user_prompt

    def _call_api(self, prompt: str) -> str:
        """调用 LLM API"""
        try:
            import httpx

            messages = [
                {"role": "system", "content": prompt.split("## 当前任务")[0]},
                {"role": "user", "content": prompt.split("## 当前任务")[1]
                 if "## 当前任务" in prompt else prompt},
            ]

            # 如果有图片数据，作为 vision message
            # if obs.has_image():
            #     messages[1]["content"] = [
            #         {"type": "text", "text": user_text},
            #         {"type": "image_url", "image_url":
            #           {"url": f"data:image/jpeg;base64,{obs.vision['image_b64']}"}}
            #     ]

            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "messages": messages, "temperature": 0.3},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except ImportError:
            return '{"thought": "httpx未安装", "action_type": "ask", "actions": []}'
        except Exception as e:
            return f'{{"thought": "API错误: {e}", "action_type": "ask", "actions": []}}'

    def _parse_response(self, text: str) -> CognitionDecision:
        """解析 LLM 返回的结构化决策"""
        try:
            # 提取 JSON (可能被 markdown 代码块包裹)
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            data = json.loads(text.strip())
            return CognitionDecision(
                action_type=data.get("action_type", "ask"),
                actions=data.get("actions", []),
                thought=data.get("thought", ""),
            )
        except (json.JSONDecodeError, IndexError):
            return CognitionDecision(
                action_type="ask",
                actions=[],
                thought=f"无法解析: {text[:200]}",
            )
