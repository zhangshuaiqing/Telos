"""感知层 — 统一管理所有感知通道"""

from telos.observation import Observation, PerceptionChannel


class PerceptionManager:
    """汇集所有感知通道，生成统一 Observation"""

    def __init__(self):
        self._channels: dict[str, PerceptionChannel] = {}

    def register(self, channel: PerceptionChannel) -> None:
        self._channels[channel.name] = channel

    def observe(self) -> Observation:
        """采集所有通道，生成 Observation"""
        obs = Observation()
        for name, ch in sorted(self._channels.items(),
                               key=lambda x: x[1].priority):
            try:
                data = ch.capture()
            except Exception:
                data = {"error": f"{name} 采集失败"}

            if name == "vision":
                obs.vision = data
            elif name == "voice":
                obs.voice = data
            elif name == "proprio":
                obs.proprio = data
            else:
                obs.extra[name] = data
        return obs

    def start_all(self) -> dict[str, bool]:
        return {name: ch.start() for name, ch in self._channels.items()}

    def stop_all(self) -> None:
        for ch in self._channels.values():
            try:
                ch.stop()
            except Exception:
                pass

    def channel_names(self) -> list[str]:
        return list(self._channels.keys())
