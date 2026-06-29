# 认知引擎规格

> Prompt 工程 + 多模态推理 + RL 反思进化。全部由云端 LLM API 驱动。

---

## 目录

1. [引擎架构](#1-引擎架构)
2. [System Prompt 模板](#2-system-prompt-模板)
3. [多模态输入构建](#3-多模态输入构建)
4. [决策输出解析](#4-决策输出解析)
5. [安全约束注入](#5-安全约束注入)
6. [RL 反思循环](#6-rl-反思循环)
7. [API 调用管理](#7-api-调用管理)
8. [Vision 模式选择](#8-vision-模式选择)

---

## 1. 引擎架构

```
CognitionEngine
  ├── SystemPromptBuilder   ← 能力清单 + 安全 + 人格 + 输出格式
  ├── MultimodalBuilder     ← Observation → vision msg + text context
  ├── DecisionParser        ← LLM raw text → CognitionDecision
  ├── SafetyValidator       ← 速度/能量/范围 校验
  └── ReflectionEngine      ← 任务结束 → 轨迹回顾 → 策略提取
```

---

## 2. System Prompt 模板

### 2.1 完整模板 (动态构建)

```
<personality>
{personality_prompt}              ← 由 PersonalityManager 动态生成
</personality>

<capabilities>
你控制以下执行器:
{actuator_capabilities}           ← 从 Executor.get_capabilities() 生成
</capabilities>

<safety>
硬性安全约束:
  - 最大速度: {speed_limit} m/s
  - 最大能量输出: {energy_limit} W
  - 以下动作需要用户确认: {approval_actions}
  - STM32 独立监控急停条件
</safety>

<output_format>
你必须以 JSON 格式输出决策:
```json
{
  "thought": "你的推理过程 (用中文)",
  "action_type": "move|speak|actuate|wait|ask",
  "actions": [
    {
      "actuator": "执行器名称",
      "action": "动作名称",
      "params": {"参数名": 值}
    }
  ],
  "speech": "你说的话 (仅 action_type=speak)"
}
```

action_type 说明:
  - move: 控制底盘移动
  - speak: 语音回复用户 (需填写 speech 字段)
  - actuate: 操作执行器 (喷雾/抓取/激光等)
  - wait: 等待观察，不变速
  - ask: 需要向用户提问
</output_format>
```

### 2.2 能力清单生成

```python
def build_capabilities_text(executor: Executor) -> str:
    lines = []
    for cap in executor.get_capabilities():
        actions = ", ".join(cap.actions)
        constraints = ", ".join(f"{k}={v}" for k, v in cap.constraints.items())
        lines.append(
            f"- {cap.id} [{cap.type}]: {cap.description}\n"
            f"  动作: {actions}\n"
            f"  约束: {constraints}"
        )
    return "\n".join(lines)
```

---

## 3. 多模态输入构建

### 3.1 整体结构

```
User Message:
  ┌──────────────────────────────────┐
  │ [Vision Message]                  │  ← 图片 Base64 (如果 vision 正常)
  │  data:image/jpeg;base64,{b64}    │
  ├──────────────────────────────────┤
  │ [Text Message]                    │
  │  当前任务: {task}                  │
  │  机器人状态: {proprio_text}        │
  │  {voice_text}                     │  ← 如果有语音
  │  {memory_context}                 │  ← 工作记忆 + 上次反思
  │  {power_context}                  │  ← 功耗预算 (如果有)
  └──────────────────────────────────┘
```

### 3.2 构建代码

```python
def build_user_message(self, obs: Observation, task: str,
                       memory: str, power: str = "") -> dict:
    text_parts = [f"## 当前任务\n{task}\n"]
    text_parts.append(f"## 机器人状态\n{obs.to_prompt_text()}")
    
    if obs.voice and obs.voice.get("text"):
        text_parts.append(f"\n## 用户语音\n{obs.voice['text']}")
    
    if memory:
        text_parts.append(f"\n## 记忆\n{memory}")
    
    if power:
        text_parts.append(f"\n## 功耗\n{power}")
    
    content = [{"type": "text", "text": "\n".join(text_parts)}]
    
    # 图像作为 vision message
    if obs.has_image():
        content.insert(0, {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{obs.vision['image_b64']}"}
        })
    
    return {"role": "user", "content": content}
```

### 3.3 Token 预估

| 内容 | 预估 Token |
|------|-----------|
| System Prompt | ~400 |
| 能力清单 (3个执行器) | ~150 |
| 机器人状态 | ~80 |
| 图像 640×480 JPEG | ~500-800 (VL 模型) |
| 语音文本 (一句话) | ~10 |
| 记忆上下文 | ~100 |
| **总计** | **~1300** |

---

## 4. 决策输出解析

### 4.1 正常输出

```json
{
  "thought": "用户说去B区看看。我先检查当前位置——在A区入口。B区在拓扑地图上位于当前位置东北方向约15米。前方视野清晰，没有障碍物。我应该先转向东北方向，然后前进。",
  "action_type": "move",
  "actions": [
    {"actuator": "left_motor", "action": "set_speed", "params": {"rpm": 500}},
    {"actuator": "right_motor", "action": "set_speed", "params": {"rpm": 300}}
  ]
}
```

### 4.2 语音输出

```json
{
  "thought": "刚刚完成了B区扫描，检测到5株西红柿。现在向用户汇报。",
  "action_type": "speak",
  "actions": [],
  "speech": "帅清，B区扫描完成啦！发现了5株西红柿，状态都不错。要我继续喷洒吗？"
}
```

### 4.3 解析容错

```python
def parse_decision(self, raw_text: str) -> CognitionDecision:
    """容错解析 LLM 输出"""
    
    # 1. 提取 JSON
    json_str = raw_text
    for marker in ["```json", "```"]:
        if marker in json_str:
            json_str = json_str.split(marker)[1].split("```")[0].strip()
            break
    
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        # 2. 降级: 尝试正则提取 action_type
        import re
        match = re.search(r'"action_type"\s*:\s*"(\\w+)"', raw_text)
        action_type = match.group(1) if match else "ask"
        
        return CognitionDecision(
            action_type=action_type,
            actions=[],
            thought=f"JSON解析失败，降级处理。原始: {raw_text[:200]}",
            parse_error=True
        )
    
    return CognitionDecision(
        action_type=data.get("action_type", "ask"),
        actions=data.get("actions", []),
        thought=data.get("thought", ""),
        speech=data.get("speech", ""),
    )
```

---

## 5. 安全约束注入

### 5.1 注入位置

安全约束通过两个渠道进入 LLM:

| 渠道 | 内容 | 作用 |
|------|------|------|
| System Prompt | "最大速度 1.0 m/s, 能量上限 100W" | 阻止 LLM 生成不安全指令 |
| 端侧校验 (L2) | 执行前规则校验 | 拦截 LLM "越狱" 的输出 |

### 5.2 端侧校验

```python
class SafetyValidator:
    def validate(self, decision: CognitionDecision,
                 actuators: dict) -> CognitionDecision:
        for action in decision.actions:
            params = action.get("params", {})
            
            # 速度钳制
            if "speed" in params or "rpm" in params:
                key = "speed" if "speed" in params else "rpm"
                limit = self.config.speed_limit
                if abs(params[key]) > limit:
                    params[key] = limit * (1 if params[key] > 0 else -1)
            
            # 能量钳制
            if "power" in params:
                if params["power"] > self.config.energy_limit:
                    params["power"] = self.config.energy_limit
            
            # 禁止动作检测
            full_action = f"{action['actuator']}.{action['action']}"
            if full_action in self.config.forbidden_actions:
                # 替换为 wait
                action["action"] = "wait"
                action["params"] = {}
        
        return decision
```

---

## 6. RL 反思循环

### 6.1 触发时机

```python
class ReflectionEngine:
    """任务结束后的反思学习"""
    
    def reflect(self, trajectory: list[dict],
                task_result: str  # "success" | "failed" | "interrupted"
                ) -> ReflectionResult:
        """回顾整个任务轨迹，提取改进策略"""
        
        prompt = self._build_reflection_prompt(trajectory, task_result)
        response = self._call_api(prompt)
        return self._parse_reflection(response)
```

### 6.2 反思 Prompt

```
你刚刚完成了一个任务。回顾整个过程，分析得失。

## 任务结果
{task_result} — {"成功" if success else "失败"}

## 任务轨迹
{trajectory_summary}
  步骤1: 前进 (成功)
  步骤2: 遇到障碍，右转 (成功)
  步骤3: 进入B区 (成功)
  ...
  步骤12: 喷洒完成 (成功)

## 反思要求
请分析:
1. 哪些做得好?
2. 哪些可以改进?
3. 如果下次遇到类似情况，应该怎么做?

输出 JSON:
```json
{
  "summary": "一句话总结",
  "good": ["做得好的方面"],
  "improve": ["可以改进的方面"],
  "lessons_learned": [
    {
      "situation": "进入未探索区域前",
      "action": "先降低速度到0.3m/s, 扫描前方2米", 
      "reason": "避免撞到隐藏障碍"
    }
  ]
}
```
```

### 6.3 反思结果应用

```python
@dataclass
class ReflectionResult:
    summary: str
    good: list[str]
    improve: list[str]
    lessons: list[dict]    # [{situation, action, reason}]

# 应用:
# 1. lessons → 存入程序记忆 (ProceduralMemory)
# 2. 下次相似场景 → 注入到 System Prompt 的 few-shot 示例
# 3. 持续失败的场景 → 调整人格 (更谨慎)

def apply_lessons(self, result: ReflectionResult):
    for lesson in result.lessons:
        self.procedural_memory.add_rule(
            trigger=lesson["situation"],
            action=lesson["action"],
            reason=lesson["reason"],
            confidence=0.7  # 初始置信度
        )
```

### 6.4 程序记忆格式

```python
class ProceduralMemory:
    """习得技能的持久存储"""
    
    def add_rule(self, trigger: str, action: str,
                 reason: str, confidence: float):
        self._rules.append({
            "trigger": trigger,
            "action": action,
            "reason": reason,
            "confidence": confidence,
            "times_used": 0,
            "success_rate": 0.0,
        })
    
    def get_applicable_rules(self, situation: str) -> list[dict]:
        """匹配当前场景的已有经验"""
        # 简单字符串匹配 → 未来用向量相似度
        return [r for r in self._rules
                if r["trigger"] in situation and r["confidence"] > 0.5]
    
    def update_rule(self, trigger: str, success: bool):
        """使用后更新置信度"""
        for rule in self._rules:
            if rule["trigger"] == trigger:
                rule["times_used"] += 1
                if success:
                    rule["success_rate"] = (
                        rule["success_rate"] * (rule["times_used"] - 1) + 1
                    ) / rule["times_used"]
                else:
                    rule["confidence"] *= 0.8  # 失败降低置信度
```

---

## 7. API 调用管理

### 7.1 重试策略

```python
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

class APIClient:
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=lambda e: isinstance(e, (httpx.TimeoutException,
                                        httpx.HTTPStatusError))
        and getattr(e, 'response', None) is not None
        and e.response.status_code >= 500
    )
    async def call(self, messages: list[dict]) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 1024,
                },
            )
            
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                raise RateLimitError(retry_after)
            
            resp.raise_for_status()
            return resp.json()
```

### 7.2 速率限制

```python
class RateLimiter:
    def __init__(self, max_per_minute: int = 120):
        self._max = max_per_minute
        self._window = []
    
    def acquire(self) -> bool:
        now = time.time()
        # 清理旧记录
        self._window = [t for t in self._window if now - t < 60]
        if len(self._window) >= self._max:
            return False  # 被限速
        self._window.append(now)
        return True
```

### 7.3 故障降级

```
LLM 调用失败:
  1 次失败 → 重试 (1s)
  2 次失败 → 重试 (2s)
  3 次失败 → 重试 (4s)
  全部失败 → 降级为本地规则:
    - 降低速度到安全值
    - 语音播报 "API 连接暂时中断，正在本地规则下运行"
    - 每 10s 尝试重新连接
```

---

## 8. Vision 模式选择

### 8.1 何时发送图片

| 场景 | 发送图片? | 理由 |
|------|---------|------|
| 用户语音指令 "去B区" | ❌ | LLM 只需要地图拓扑信息 |
| 前方 50cm 出现障碍物 | ✅ | LLM 需要看到障碍物是什么 |
| 固定 500ms 定时 | ✅ | 保持 LLM 了解当前场景 |
| 巡航模式 (无变化) | ❌ (跳跃帧) | 图片内容变化 < 5% 不重复发 |
| 用户问 "你看到了什么" | ✅ | 需要最新一帧 |

### 8.2 模型降级

```python
def select_model(self, obs: Observation) -> str:
    """选择合适的模型 — 算力/成本优化"""
    
    has_image = obs.has_image()
    is_complex = self._is_complex_task()
    
    if has_image and is_complex:
        return "deepseek-vl-pro"     # 视觉+推理
    elif has_image:
        return "deepseek-vl-lite"    # 视觉
    else:
        return "deepseek-chat"       # 纯文本，最快
```
