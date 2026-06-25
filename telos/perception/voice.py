"""语音交互通道 — ASR输入 + TTS输出"""


class VoiceChannel:
    """语音输入/输出通道"""

    name = "voice"
    priority = 1

    def __init__(self, vad_aggressiveness: int = 2):
        self._active = False
        self._vad_level = vad_aggressiveness

    def start(self) -> bool:
        self._active = True
        return True

    def stop(self) -> None:
        self._active = False

    def capture(self) -> dict:
        """采集语音 → VAD → 返回已识别文本 (或空)"""
        return {"text": "", "intent": None}

    def listen_sync(self, timeout: float = 5.0) -> str:
        """阻塞式监听，返回识别文本"""
        return ""

    def health(self) -> dict:
        return {"name": "voice", "active": self._active}


class VoiceOutput:
    """TTS 语音合成 → 播放"""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural"):
        self.voice = voice

    async def speak(self, text: str) -> None:
        """异步合成并播放"""
        try:
            import edge_tts
            tts = edge_tts.Communicate(text, self.voice)
            await tts.save("temp_tts.mp3")
            # 播放 temp_tts.mp3 (需要 pygame 或其他)
        except ImportError:
            pass  # edge-tts 未安装时静默

    def speak_sync(self, text: str) -> None:
        import asyncio
        asyncio.run(self.speak(text))
