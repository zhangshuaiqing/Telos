# 通信协议规范

> 三层通信：云端 ↔ 端侧 (HTTP) | 端侧 ↔ STM32 (UART) | 端侧 ↔ ESP32 (MQTT)

---

## 目录

1. [通信架构总览](#1-通信架构总览)
2. [端侧 ↔ 云端 — HTTP/JSON](#2-端侧--云端--httpjson)
3. [端侧 ↔ STM32 — 二进制协议](#3-端侧--stm32--二进制协议)
4. [端侧 ↔ ESP32 — MQTT](#4-端侧--esp32--mqtt)
5. [心跳与超时 (三层)](#5-心跳与超时-三层)
6. [API 错误码](#6-api-错误码)
7. [协议版本与兼容](#7-协议版本与兼容)

---

## 1. 通信架构总览

```
         云端 LLM API
              │
     HTTPS (TLS 1.3)
              │
        端侧 CPU (Python Agent)
        ├── UART (115200 8N1) ── STM32
        └── WiFi MQTT ───────── ESP32
                                  │
                              I2C/SPI ── 辅助传感器
```

**设计原则：**
- 云端走 HTTPS — 数据完整 + 加密
- STM32 走 UART 二进制 — 低延迟 (< 1ms) + 紧凑 (每帧 < 20 字节)
- ESP32 走 MQTT — 标准 IoT 协议，质量可调
- 所有通信链路独立超时和重试

---

## 2. 端侧 ↔ 云端 — HTTP/JSON

### 2.1 请求

```
POST https://api.deepseek.com/v1/chat/completions
Headers:
  Authorization: Bearer sk-***  (环境变量 DEEPSEEK_API_KEY)
  Content-Type: application/json

Body:
{
  "model": "deepseek-chat",
  "temperature": 0.3,
  "max_tokens": 1024,
  "messages": [
    {
      "role": "system",
      "content": "<能力清单 + 安全约束 + 人格profile + 输出格式要求>"
    },
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "当前任务: {task}\n机器人状态: {proprio}\n用户语音: {voice.text}\n上次反思: {reflection}"
        },
        {
          "type": "image_url",
          "image_url": {"url": "data:image/jpeg;base64,{image_b64}"}
        }
      ]
    }
  ]
}
```

### 2.2 响应

```json
{
  "id": "chatcmpl-xxx",
  "choices": [{
    "message": {
      "content": "{\n  \"thought\": \"前方有障碍，需要右转绕行...\",\n  \"action_type\": \"move\",\n  \"actions\": [\n    {\"actuator\": \"left_motor\", \"action\": \"set_speed\", \"params\": {\"rpm\": 300}},\n    {\"actuator\": \"right_motor\", \"action\": \"set_speed\", \"params\": {\"rpm\": 500}}\n  ]\n}"
    },
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 1234, "completion_tokens": 89}
}
```

### 2.3 认知决策解析

```python
def parse_decision(response: dict) -> CognitionDecision:
    content = response["choices"][0]["message"]["content"]
    
    # 提取 JSON — 处理 markdown 代码块包裹
    json_str = content
    if "```json" in json_str:
        json_str = json_str.split("```json")[1].split("```")[0].strip()
    elif "```" in json_str:
        json_str = json_str.split("```")[1].split("```")[0].strip()
    
    data = json.loads(json_str)
    return CognitionDecision(
        action_type=data.get("action_type", "ask"),
        actions=data.get("actions", []),
        thought=data.get("thought", ""),
    )
```

### 2.4 动作输出格式

```json
{
  "action_type": "move | speak | actuate | wait | ask",
  "actions": [
    {
      "actuator": "left_motor",
      "action": "set_speed",
      "params": {"rpm": 300}
    }
  ]
}
```

### 2.5 错误响应

```json
// API 错误时 LLM 不返回 → 端侧自己处理
// 见 Ch11 错误处理体系
```

### 2.6 请求头规范

| Header | 值 | 说明 |
|--------|-----|------|
| `Authorization` | `Bearer sk-***` | API Key |
| `Content-Type` | `application/json` | |
| `User-Agent` | `Telos/0.1.0` | 标识自身 |
| `X-Request-ID` | UUID | 请求追踪 |

---

## 3. 端侧 ↔ STM32 — 二进制协议

> 详细命令字定义见 `docs/actuators.md` Ch11。

### 3.1 物理层

| 参数 | 值 |
|------|-----|
| 接口 | UART |
| 波特率 | 115200 |
| 数据位 | 8 |
| 校验位 | None |
| 停止位 | 1 |
| 流控 | 无 |

### 3.2 帧格式

```
命令帧 (端侧 → STM32):
┌──────┬──────┬──────────┬──────────┬──────────┬──────┐
│SYNC  │ ADDR │ TYPE     │ CMD      │ PAYLOAD  │ CRC  │
│0xAA  │ 1B   │ 1B       │ 1B       │ N bytes  │ 2B   │
└──────┴──────┴──────────┴──────────┴──────────┴──────┘

响应帧 (STM32 → 端侧):
┌──────┬──────┬──────────┬──────────┬──────────┬──────────┬──────┐
│SYNC  │ ADDR │ TYPE     │ CMD      │ STATUS   │ DATA     │ CRC  │
│0xAA  │ 1B   │ 1B       │ 1B       │ 1B       │ N bytes  │ 2B   │
└──────┴──────┴──────────┴──────────┴──────────┴──────────┴──────┘
```

**字段说明：**

| 字段 | 大小 | 范围 | 说明 |
|------|------|------|------|
| SYNC | 1B | 0xAA | 帧同步头 |
| ADDR | 1B | 0x01-0x7F | 执行器/传感器地址 |
| TYPE | 1B | 0x00-0x07 | 原语类型 (actuators.md Ch11.2) |
| CMD | 1B | 0x10-0xFF | 具体命令 |
| STATUS | 1B | 0x00-0x04 | 响应状态 (仅响应帧) |
| PAYLOAD | N | — | 可变长度数据 |
| DATA | N | — | 响应数据 (仅响应帧) |
| CRC | 2B | — | CRC-16/MODBUS (校验 ADDR..PAYLOAD) |

### 3.3 响应状态码

| STATUS | 名称 | 含义 |
|--------|------|------|
| 0x00 | ACK | 成功执行 |
| 0x01 | NACK | 参数错误 |
| 0x02 | BUSY | 前一个命令未完成 |
| 0x03 | ERROR | 硬件故障 |
| 0x04 | NOT_READY | 未初始化 |

**ERROR 子码 (DATA 字段第一个字节):**

| 子码 | 含义 |
|------|------|
| 0x01 | 过流 |
| 0x02 | 过温 |
| 0x03 | 堵转 |
| 0x04 | 编码器故障 |
| 0x05 | CAN 通信故障 |
| 0xFF | 未知错误 |

### 3.4 遥测帧 (STM32 → 端侧，主动推送)

```
┌──────┬──────┬──────────┬──────────┬──────────┬──────┐
│SYNC  │ ADDR │ TYPE=0x00│CMD=0x01  │ PAYLOAD  │ CRC  │
│0xAA  │ 0x00 │ 0x00     │ 0x01     │ 18 bytes │ 2B   │
└──────┴──────┴──────────┴──────────┴──────────┴──────┘

PAYLOAD 结构 (18字节):
  [0-1]  speed     int16 ×10  (m/s, 小端)    例: 35 → 0.35 m/s
  [2-3]  heading   uint16 ×10 (°, 小端)      例: 1452 → 145.2°
  [4]    battery   uint8      (%)             例: 87
  [5-6]  voltage   uint16 ×10 (V, 小端)       例: 238 → 23.8V
  [7-8]  roll      int16 ×10  (°, 小端)       例: 12 → 1.2°
  [9-10] pitch     int16 ×10  (°, 小端)
  [11]   l_rpm     uint8 ×20  (左电机 rpm×20)
  [12]   r_rpm     uint8 ×20  (右电机 rpm×20)
  [13-14] l_current uint16 ×100 (A, 小端)     例: 210 → 2.10A
  [15-16] r_current uint16 ×100 (A, 小端)
  [17]   tof_cm    uint8      (cm)             例: 150 → 150cm, 0=无效
```

> 遥测帧 ADDR=0x00 表示来源是 STM32 主控（非特定执行器）。

### 3.5 心跳帧

```
心跳请求 (端侧 → STM32, 每 100ms):
  0xAA 00 00 FE [CRC]
  
心跳回复 (STM32 → 端侧):
  0xAA 00 00 FE 00 NN [CRC]
                    NN = 状态字节:
                      bit0: 急停
                      bit1: 电机故障
                      bit2: 看门狗触发
                      bit3-7: 保留

心跳超时 > 1s → 端侧标记 STM32 DISCONNECTED → 进入紧急停止
```

### 3.6 端侧编码器

详见 `docs/actuators.md` Ch11.5。

---

## 4. 端侧 ↔ ESP32 — MQTT

### 4.1 主题结构

```
telos/{device_id}/telemetry    ← ESP32 → 端侧 (传感器数据)
telos/{device_id}/command      ← 端侧 → ESP32 (指令)
telos/{device_id}/status       ← ESP32 → 端侧 (设备状态)
telos/{device_id}/ota          ← 端侧 → ESP32 (固件更新)
```

### 4.2 遥测消息 (ESP32 → 端侧)

```json
// Topic: telos/esp32_01/telemetry
// QoS: 0, 每 1s 一次

{
  "device": "esp32_01",
  "uptime_s": 3600,
  "wifi_rssi": -45,
  "sensors": {
    "temperature_c": 32.5,
    "humidity": 65,
    "ambient_light": 500
  },
  "timestamp": 1719123456
}
```

### 4.3 指令消息 (端侧 → ESP32)

```json
// Topic: telos/esp32_01/command
// QoS: 1

{
  "cmd": "set_led",
  "params": {"color": "green", "pattern": "breathing"},
  "request_id": "uuid-123"
}
```

### 4.4 QoS 策略

| Topic | QoS | 说明 |
|-------|-----|------|
| telemetry | 0 | 传感器数据丢失可接受 |
| command | 1 | 至少一次送达 |
| status | 1 | |
| ota | 2 | 恰好一次 (固件完整性) |

### 4.5 Last Will 遗嘱

```
ESP32 连接时设置 LWT:
  telos/esp32_01/status → {"online": false}

ES32 意外断连 → MQTT Broker 自动发布遗嘱
端侧订阅 status → 检测到 {"online": false} → 告警
```

---

## 5. 心跳与超时 (三层)

### 5.1 各层心跳

| 链路 | 心跳频率 | 超时阈值 | 超时行为 |
|------|---------|---------|---------|
| 端侧 → 云端 | 按需 (LLM调用) | 30s (单次请求超时) | 指数退避重试 |
| 端侧 → STM32 | 100ms | 1s | 紧急停止 |
| 端侧 → ESP32 | MQTT keepalive 60s | 120s | 告警，不影响运行 |
| STM32 看门狗 | 硬件 1ms | ~10ms | 系统复位 |

### 5.2 STM32 心跳监控

```python
class STM32Heartbeat:
    def __init__(self, timeout_ms: int = 1000):
        self._timeout = timeout_ms
        self._last_heartbeat = 0.0
        self._missed_count = 0
    
    def beat_received(self):
        self._last_heartbeat = time.monotonic()
        self._missed_count = 0
    
    def check(self) -> bool:
        """返回 False = 心跳超时"""
        age_ms = (time.monotonic() - self._last_heartbeat) * 1000
        if age_ms > self._timeout:
            self._missed_count += 1
            return False
        return True
    
    @property
    def age_ms(self) -> float:
        return (time.monotonic() - self._last_heartbeat) * 1000
```

---

## 6. API 错误码

### 6.1 HTTP 错误 (端侧 → 云端)

| 状态码 | 含义 | 重试策略 |
|--------|------|---------|
| 200 | 成功 | — |
| 401 | API Key 无效 | 不重试，语音报警 |
| 429 | 速率限制 | 等待 Retry-After 秒 |
| 500 | 服务端错误 | 指数退避 ×3 |
| 502/503 | 网关/服务不可用 | 指数退避 ×3 |
| Timeout | 请求超时 | 指数退避 ×3 |

### 6.2 STM32 错误 (NACK/ERROR 子码)

| 子码 | 含义 | 端侧重试 |
|------|------|---------|
| NACK | 参数超出范围 | 钳制参数后重试 ×1 |
| 0x01 过流 | 电流异常 | 停止 → 冷却 → 重试 ×1 |
| 0x02 过温 | 温度过高 | 等待 60s → 重试 |
| 0x03 堵转 | 电机卡死 | 反转释放 → 重试 ×1 |
| BUSY | 前指令未完成 | 等待 100ms → 重试 ×3 |
| NOT_READY | 未初始化 | 等待 init 完成 |

---

## 7. 协议版本与兼容

### 7.1 版本号

```python
PROTOCOL_VERSION = "1.0.0"  # MAJOR.MINOR.PATCH

# STM32 握手时交换版本:
# 端侧: 0xAA 00 00 FC [PROTOCOL_VERSION_MAJOR] [PROTOCOL_VERSION_MINOR] [CRC]
# STM32: 固件版本号
# 不匹配 → 告警但尝试运行 (向下兼容)
```

### 7.2 版本兼容规则

| 变化 | MAJOR | MINOR | PATCH |
|------|------|------|------|
| 帧格式变化 | +1 | 0 | 0 |
| 新增命令字 | — | +1 | 0 |
| 错误码补充 | — | — | +1 |
