"""Observation — 多模态感知的统一数据结构与接口"""

from dataclasses import dataclass, field
from typing import Protocol, Optional


@dataclass
class Observation:
    """所有感知通道的汇集输出
    
    认知引擎接收 Observation → 生成理解 → 规划动作
    """
    vision: Optional[dict] = None
    voice: Optional[dict] = None
    proprio: Optional[dict] = None
    extra: dict = field(default_factory=dict)  # {"lidar": {...}, "thermal": {...}}

    def to_prompt_text(self) -> str:
        """转换为 LLM 可理解的文本描述"""
        parts = []

        if self.proprio:
            p = self.proprio
            parts.append(
                f"机器人状态: 速度={p.get('speed', '?')}m/s, "
                f"航向={p.get('heading', '?')}°, "
                f"电量={p.get('battery', '?')}%, "
                f"倾角=({p.get('roll', 0):.1f}, {p.get('pitch', 0):.1f})°"
            )

        if self.vision:
            v = self.vision
            if v.get("description"):
                parts.append(f"视觉: {v['description']}")
            if v.get("objects"):
                parts.append(f"检测到: {', '.join(v['objects'])}")

        if self.voice:
            vc = self.voice
            if vc.get("text"):
                parts.append(f"用户语音: {vc['text']}")

        for modal, data in self.extra.items():
            parts.append(f"[{modal}]: {data}")

        return "\n".join(parts)

    def has_image(self) -> bool:
        return self.vision is not None and "image_b64" in self.vision


class PerceptionChannel(Protocol):
    """所有感知通道必须实现此接口
    
    新增模态只需实现此协议并 register()，不改架构。
    """
    name: str
    priority: int = 0  # 数字越小越优先

    def start(self) -> bool: ...
    def stop(self) -> None: ...
    def capture(self) -> dict: ...
    def health(self) -> dict: ...
