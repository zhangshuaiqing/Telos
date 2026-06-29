# 感知通道规格

> 所有感知通道实现 `PerceptionChannel` 协议。PerceptionManager 汇集多通道输出为统一 `Observation`。

---

## 目录

1. [通用接口](#1-通用接口)
2. [VisionChannel — 视觉通道](#2-visionchannel--视觉通道)
3. [VoiceChannel — 语音通道](#3-voicechannel--语音通道)
4. [ProprioChannel — 本体感通道](#4-propriochannel--本体感通道)
5. [PerceptionManager — 多通道汇集](#5-perceptionmanager--多通道汇集)
6. [扩展通道模板](#6-扩展通道模板)
7. [通道自检与降级](#7-通道自检与降级)
8. [多模态融合发送策略](#8-多模态融合发送策略)

---

## 1. 通用接口

```python
class PerceptionChannel(Protocol):
    name: str              # "vision" | "voice" | "proprio" | "lidar" | ...
    priority: int          # 采集优先级 (0=最先, 用于避免竞争)
    
    def start(self) -> bool: ...      # 启动采集
    def stop(self) -> None: ...       # 停止采集
    def capture(self) -> dict: ...    # 采集一帧 → 结构化数据
    def health(self) -> dict: ...     # 自检状态


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"    # 部分功能可用
    FAILED = "failed"        # 完全不可用
    UNKNOWN = "unknown"      # 未启动自检
```

---

## 2. VisionChannel — 视觉通道

### 2.1 功能

采集摄像头帧，压缩为 JPEG Base64，供 VL 模型分析。

**关键设计：** 端侧不做模型推理。只做采集+压缩。语义理解全部交给云端 VL 模型。

### 2.2 配置

```yaml
perception:
  vision:
    enabled: true
    camera_id: 0              # /dev/video0
    resolution: [640, 480]    # 宽×高 (32的倍数，适配 VL 模型)
    quality: 70               # JPEG 质量 1-100 (70 = 良好压缩比)
    max_fps: 30               # 采集帧率上限
    auto_exposure: true       # 自动曝光
    auto_white_balance: true  # 自动白平衡
```

### 2.3 输出格式

```python
# capture() 返回:
{
    "image_b64": "iVBORw0KGgo...",   # JPEG Base64 字符串
    "width": 640,
    "height": 480,
    "format": "jpeg",
    "timestamp": 1719123456.789,      # Unix 时间戳
    "exposure": 0.016,                # 曝光时间 (秒)
    "brightness": 128,                # 平均亮度 (0-255)
    "error": null,                    # null 或错误信息
}
```

### 2.4 采集流程

```
┌─────────────────────────────────────────────────┐
│              VisionChannel 采集流程               │
│                                                 │
│  1. 打开摄像头 (start)                            │
│     cv2.VideoCapture(camera_id)                  │
│     → 设置分辨率 → 设置帧率                       │
│                                                 │
│  2. 循环采集 (另外线程, 30fps)                     │
│     read() → RGB 帧 → 保持最新一帧在内存           │
│     → 不压缩、不发送 (等 PerceptionManager 来取)   │
│                                                 │
│  3. capture() 被调用时                             │
│     取最新帧 → resize 到目标分辨率                  │
│     → cv2.imencode(".jpg", quality)              │
│     → base64.b64encode → 返回                    │
│                                                 │
│  4. 自检 (health)                                 │
│     采集测试帧 → 检查亮度范围 [30, 240]            │
│     → 太暗: "光线不足，建议补光"                   │
│     → 太亮: "过曝，降低曝光值"                     │
└─────────────────────────────────────────────────┘
```

### 2.5 性能指标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 采集延迟 | < 15ms | cv2.read() 一帧 |
| 压缩延迟 | < 20ms | 640×480 JPEG quality=70 |
| Base64 延迟 | < 5ms | ~40KB 字符串 |
| 总 capture() 延迟 | < 40ms | 采集+压缩+编码 |
| 图像大小 | 20-50KB | quality=70, 典型场景 |
| 内存占用 | < 5MB | 环形缓冲 3 帧 |

### 2.6 错误处理

| 错误 | 检测 | 恢复 |
|------|------|------|
| 摄像头未连接 | cv2.VideoCapture 返回 False | 报告 FAILED，降级无视觉运行 |
| 帧读取超时 | read() 连续 3 次返回 None | 重启摄像头 → 重试 ×2 → 报告 FAILED |
| 图像全黑/全白 | 亮度 < 10 或 > 245 | 报告 DEGRADED，继续采集 |
| USB 带宽不足 | 帧率骤降 > 50% | 降分辨率到 320×240 |

### 2.7 测试

```python
def test_vision_capture_returns_base64_jpeg():
    ch = VisionChannel()
    result = ch.capture()
    assert "image_b64" in result
    # 解码验证
    raw = base64.b64decode(result["image_b64"])
    assert raw[:2] == b'\xff\xd8'  # JPEG 魔数

def test_vision_health_detects_dark_image():
    ch = VisionChannel()
    # Mock 一个暗帧
    ch._last_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    health = ch.health()
    assert health["status"] == "degraded"
    assert "光线不足" in health.get("warning", "")

def test_vision_static_load():
    result = VisionChannel.load_image("tests/fixtures/test_frame.jpg")
    assert "image_b64" in result
```

---

## 3. VoiceChannel — 语音通道

### 3.1 功能

持续监听麦克风，VAD (语音活动检测) 触发 ASR，将语音指令转为文本。

> **根据 Ch3 决策（持续监听 + 随时打断），VoiceChannel 始终在后台运行。**

### 3.2 配置

```yaml
perception:
  voice:
    enabled: true
    vad_aggressiveness: 2    # 0-3, 越高越激进的静音切除
    asr_provider: "aliyun"   # aliyun | whisper_api | ...
    asr_language: "zh-CN"
    wake_word: null          # 不使用唤醒词 (null = 持续监听)
    max_listen_sec: 15       # 单次最长监听时间
    silence_timeout_sec: 1.5 # 静音多久判定为说话结束
    mic_device: "default"    # 麦克风设备
```

### 3.3 输出格式

```python
# capture() 返回:
{
    "text": "去B区看看",            # ASR 识别结果 (空字符串=无讲话)
    "is_critical": false,          # 是否为危险指令 (急停/停/关...)
    "confidence": 0.92,            # ASR 置信度
    "needs_confirmation": false,   # 是否需要二次确认 (低置信度)
    "audio_level": 0.3,            # 当前音量 (0-1)
    "is_speaking": false,          # 是否正在说话
}
```

### 3.4 采集流程

```
┌───────────────────────────────────────────────────┐
│              VoiceChannel 持续监听流程               │
│                                                   │
│  start()                                          │
│    → 打开麦克风 (pyaudio / sounddevice)             │
│    → 启动 VAD 循环 (webrtcvad)                      │
│    → 启动 ASR 客户端 (阿里云 WebSocket)              │
│                                                   │
│  VAD 循环 (持续, 20ms/帧):                           │
│    麦克风 → 16kHz mono 16bit → VAD 判断              │
│                                                   │
│    静音 → 有语音:                                    │
│      → 标记 is_speaking = True                     │
│      → 开始缓冲音频                                 │
│                                                   │
│    有语音 → 静音 (silence_timeout_sec):              │
│      → 结束缓冲                                     │
│      → 发送到 ASR                                   │
│      → 等待识别结果                                  │
│      → 更新 text / confidence                      │
│      → 标记 is_speaking = False                     │
│                                                   │
│  capture() 被调用时:                                  │
│    → 立即返回当前最新的 text (不等待)                  │
└───────────────────────────────────────────────────┘
```

### 3.5 危险指令检测

```python
CRITICAL_WORDS = ["急停", "停下", "停止", "别动", "救命", 
                   "停", "关", "关掉", "切断"]

def _classify_text(self, text: str) -> dict:
    """分析识别结果"""
    result = {"text": text}
    
    # 危险指令检测
    if any(word in text for word in CRITICAL_WORDS):
        result["is_critical"] = True
        result["needs_confirmation"] = False  # 危险指令直接执行
    
    # 低置信度需确认
    elif confidence < 0.7:
        result["needs_confirmation"] = True
    
    return result
```

### 3.6 TTS 输出 (语音合成)

```yaml
voice:
  tts_enabled: true
  tts_voice: "zh-CN-XiaoxiaoNeural"  # Edge TTS 语音
  tts_speed: 1.0                     # 语速 0.5-2.0
  tts_provider: "edge"               # edge | aliyun | piper (本地)
```

```python
class VoiceOutput:
    """TTS 合成 → 音频播放"""
    
    async def speak(self, text: str) -> None:
        # Edge TTS: HTTP 流式合成 → 边下边播
        communicate = edge_tts.Communicate(text, self.voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                self._play_chunk(chunk["data"])
    
    def speak_sync(self, text: str):
        """同步接口 — 内部跑 asyncio"""
        asyncio.run(self.speak(text))
```

### 3.7 性能指标

| 指标 | 目标值 |
|------|--------|
| VAD 延迟 | < 10ms (20ms 音频帧) |
| ASR 延迟 | 200-500ms (网络) |
| TTS 首字延迟 | < 200ms (流式) |
| 内存占用 | < 20MB |

### 3.8 错误处理

| 错误 | 检测 | 恢复 |
|------|------|------|
| 麦克风未连接 | pyaudio 打开失败 | 报告 FAILED，无语音输入 |
| ASR 超时 | 响应 > 5s | 放弃本次识别，返回空 text |
| ASR 服务不可用 | 连续 3 次失败 | 切换备用 ASR → 语音告知 |
| 低音量 | audio_level < 0.05 连续 30s | 提醒 "声音太小" |

### 3.9 测试

```python
def test_voice_critical_word_detection():
    ch = VoiceChannel()
    result = ch._classify_text("急停", confidence=0.9)
    assert result["is_critical"] == True
    assert result["needs_confirmation"] == False

def test_voice_low_confidence_needs_confirmation():
    ch = VoiceChannel()
    result = ch._classify_text("往前走", confidence=0.5)
    assert result["needs_confirmation"] == True

def test_voice_empty_on_silence():
    ch = VoiceChannel()
    result = ch.capture()  # 没人说话
    assert result["text"] == ""
```

---

## 4. ProprioChannel — 本体感通道

### 4.1 功能

从 STM32 接收遥测数据：速度、航向、IMU 姿态、电池、电机状态。

**特点：** 不主动采集。由 STM32 通信线程持续推送更新，capture() 只读最新快照。

### 4.2 输出格式

```python
# capture() 返回:
{
    "speed": 0.35,              # m/s
    "heading": 145.2,           # °, 0=正北
    "battery": 87,              # %
    "battery_voltage": 23.8,    # V
    "roll": 1.2,                # °
    "pitch": -0.5,              # °
    "yaw_rate": 2.1,            # °/s
    "motors": {
        "left":  {"rpm": 520, "current_a": 2.1, "temp_c": 45},
        "right": {"rpm": 510, "current_a": 2.0, "temp_c": 44},
    },
    "obstacle_front_cm": 150,   # TOF 前方障碍距离 (0=未安装)
    "stm32_connected": true,    # STM32 通信状态
    "heartbeat_age_ms": 8,      # 距上次心跳的毫秒数
    "timestamp": 1719123456.789,
}
```

### 4.3 更新机制

```python
class ProprioChannel:
    """被动更新 — 由 STM32 线程调用 update()"""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._data = { ... }  # 默认值
    
    def update(self, **kwargs):
        """STM32 线程调用 — 更新传感器数据"""
        with self._lock:
            for key, value in kwargs.items():
                if key in self._data:
                    self._data[key] = value
    
    def capture(self) -> dict:
        """PerceptionManager 调用 — 只读快照"""
        with self._lock:
            return copy.deepcopy(self._data)
```

### 4.4 性能指标

| 指标 | 目标值 |
|------|--------|
| STM32 遥测频率 | 100 Hz |
| update() 延迟 | < 50μs (锁+赋值) |
| capture() 延迟 | < 100μs (深拷贝) |
| 数据包大小 | ~80 bytes (二进制) |

### 4.5 测试

```python
def test_proprio_updates_thread_safe():
    ch = ProprioChannel()
    ch.start()
    ch.update(speed=0.5, heading=90.0, battery=85)
    data = ch.capture()
    assert data["speed"] == 0.5
    assert data["heading"] == 90.0

def test_proprio_default_values_before_update():
    ch = ProprioChannel()
    data = ch.capture()
    assert data["speed"] == 0.0
    assert data["battery"] == 100.0
    assert data["stm32_connected"] == False
```

---

## 5. PerceptionManager — 多通道汇集

### 5.1 功能

按优先级采集所有注册通道，组装为统一 `Observation`。

### 5.2 接口

```python
class PerceptionManager:
    def register(self, channel: PerceptionChannel) -> None
    def observe(self) -> Observation           # 采集所有通道
    def start_all(self) -> dict[str, bool]     # 启动所有通道
    def stop_all(self) -> None                 # 停止所有通道
    def health_all(self) -> dict[str, dict]    # 所有通道自检
    def channel_names(self) -> list[str]       # 已注册通道列表

# Observation:
@dataclass
class Observation:
    vision: Optional[dict] = None
    voice: Optional[dict] = None
    proprio: Optional[dict] = None
    extra: dict = field(default_factory=dict)  # {"lidar": {...}, "thermal": {...}}
    
    def to_prompt_text(self) -> str: ...
    def has_image(self) -> bool: ...
    def has_voice_command(self) -> bool: ...
    def has_obstacle(self) -> bool: ...
```

### 5.3 采集顺序

```python
def observe(self) -> Observation:
    obs = Observation()
    # 按 priority 排序 (0=最先)
    for name, ch in sorted(self._channels.items(), key=lambda x: x[1].priority):
        try:
            data = ch.capture()
        except Exception:
            data = {"error": f"{name} 采集失败"}
        self._assign(obs, name, data)
    return obs

# 优先级:
#   vision: 0 (优先 — 图像最大)
#   voice:  1
#   proprio: 2
#   lidar:  3 (未来)
```

### 5.4 测试

```python
def test_manager_observe_aggregates_all_channels():
    pm = PerceptionManager()
    pm.register(MockVisionChannel({"image_b64": "test"}))
    pm.register(MockVoiceChannel({"text": "你好"}))
    pm.register(MockProprioChannel({"speed": 0.5}))
    
    obs = pm.observe()
    assert obs.vision["image_b64"] == "test"
    assert obs.voice["text"] == "你好"
    assert obs.proprio["speed"] == 0.5

def test_manager_channel_failure_doesnt_block_others():
    pm = PerceptionManager()
    pm.register(FailingChannel("broken"))
    pm.register(MockProprioChannel({"speed": 0.5}))
    
    obs = pm.observe()
    assert "error" in obs.extra["broken"]
    assert obs.proprio["speed"] == 0.5  # 其他通道不受影响
```

---

## 6. 扩展通道模板

新增感知通道（如 Lidar）只需实现此模板：

```python
class NewChannel(PerceptionChannel):
    """新感知通道模板"""
    
    name = "my_sensor"       # ← 必须唯一
    priority = 5             # ← 采集顺序
    
    def __init__(self, device_path: str = "/dev/my_sensor"):
        self._device = device_path
        self._active = False
    
    def start(self) -> bool:
        """启动传感器"""
        try:
            self._init_hardware()
            self._active = True
            return True
        except Exception:
            return False
    
    def stop(self) -> None:
        self._active = False
        self._cleanup()
    
    def capture(self) -> dict:
        """采集一帧数据 → 结构化 dict"""
        if not self._active:
            return {"error": "通道未激活"}
        return {
            "raw_value": self._read_sensor(),
            "timestamp": time.time(),
        }
    
    def health(self) -> dict:
        return {
            "name": self.name,
            "status": "healthy" if self._active else "failed",
        }
```

**注册：**

```python
pm = PerceptionManager()
pm.register(NewChannel("/dev/my_sensor"))
# 自动参与 observe()。extra["my_sensor"] = capture() 的结果。
```

---

## 7. 通道自检与降级

### 7.1 启动时全检

```
PerceptionManager.start_all()
  ├── VisionChannel.start() → 打开摄像头 → 测试帧 → 通过?
  ├── VoiceChannel.start()  → 打开麦克风 → 测试录音 → 通过?
  └── ProprioChannel.start() → 标记启用 → 等待 STM32 数据

health_all() 报告:
  vision:  HEALTHY   (摄像头正常)
  voice:   HEALTHY   (麦克风正常)
  proprio: HEALTHY   (STM32 连接)
```

### 7.2 降级矩阵

| 故障通道 | 降级行为 | 影响 |
|---------|---------|------|
| vision FAILED | 无图像发送，仅用语音+本体感导航 | 失去视觉场景理解 |
| voice FAILED | 无语音输入，仅用视觉+本体感 | 失去语音指令 |
| proprio FAILED | 无本体感数据 → 无法确认速度/位置 | 必须停止运行 (依赖 STM32) |
| vision DEGRADED | 降低分辨率，继续采集 | 图像质量下降 |
| voice DEGRADED | ASR 切换备用服务 | 识别率可能下降 |

### 7.3 启动报告示例

```
语音播报:
  "系统就绪。三个传感器通过自检。
   摄像头工作正常，麦克风正常，与控制器通信正常。
   可以开始任务。"
  
故障时:
  "警告: 摄像头自检失败。将在没有视觉的情况下运行，
   导航精度会降低。建议检查摄像头连接。"
```

---

## 8. 多模态融合发送策略

> 根据 Ch3 决策: **固定间隔 (500ms) + 关键事件即时触发**

### 8.1 发送决策逻辑

```python
class SendDecisionEngine:
    """决定是否将当前 Observation 发送给 LLM"""
    
    def __init__(self, interval_ms: int = 500):
        self._interval = interval_ms
        self._last_send_time = 0.0
        self._last_sent_obs = None
    
    def should_send(self, obs: Observation) -> tuple[bool, str]:
        """返回 (是否发送, 原因)"""
        now = time.time()
        
        # 1. 危险指令 — 立即发送
        if obs.has_voice_command() and obs.voice.get("is_critical"):
            return True, "critical_voice"
        
        # 2. 前方障碍 — 立即发送
        if obs.has_obstacle():
            return True, "obstacle_detected"
        
        # 3. 碰撞 — 立即发送
        if obs.proprio and obs.proprio.get("collision_detected"):
            return True, "collision"
        
        # 4. 电量骤降 — 立即发送
        if obs.proprio and obs.proprio.get("battery", 100) < 15:
            return True, "low_battery"
        
        # 5. 定时发送 — 到时间了就发
        if (now - self._last_send_time) * 1000 >= self._interval:
            return True, "scheduled"
        
        # 6. 不需要发送
        return False, ""
    
    def mark_sent(self, obs: Observation):
        self._last_send_time = time.time()
        self._last_sent_obs = obs
```

### 8.2 发送格式

```
System Prompt (固定):
  <能力清单 + 安全约束 + 人格profile>

User Message (动态构建):
  [图片: Base64 JPEG]           ← vision message (如果 vision 正常)
  文本:                          ← 以下文本拼接
    当前任务: {task}
    机器人状态: {proprio}
    用户语音: {voice.text}       ← 如果有语音
    上一次反思: {memory.reflection}
    上次动作结果: {memory.last_result}
```

### 8.3 性能优化

| 优化 | 方法 |
|------|------|
| 跳跃帧 | 图像内容变化 < 5% → 不发送新图片 |
| 降低分辨率 | 巡航模式: 640×480 → 省 token; 遇到障碍: 恢复全分辨率 |
| 关键帧检测 | 只在"有意义"的帧发送 (识别到新物体/场景切换) |
