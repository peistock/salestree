# Hermes IM 集成搬运分析

> 分析时间：2026-07-10
> 目标：评估 Hermes Agent 的 IM（即时通讯）集成功能是否值得搬到销销

---

## 一、Hermes 的 IM 架构

### 1.1 支持的平台

| 平台 | 文件 | 行数 | 协议 |
|------|------|------|------|
| Telegram | `telegram.py` | - | Bot API |
| Discord | `discord.py` | - | Discord.py |
| Slack | `slack.py` | - | Slack SDK |
| WhatsApp | `whatsapp.py` + `whatsapp_cloud.py` | - | Baileys / Meta Cloud API |
| Signal | `signal.py` | - | signal-cli |
| **微信（个人号）** | `weixin.py` | **2379 行** | **iLink Bot API** |
| **企业微信** | - | - | - |
| QQ Bot | `qqbot/` | - | QQ 开放平台 |
| 腾讯元宝 | `yuanbao.py` | - | 腾讯 API |
| Matrix | - | - | Matrix 协议 |
| iMessage | `bluebubbles.py` | - | BlueBubbles |
| Microsoft Teams | `msgraph_webhook.py` | - | Graph API |

### 1.2 架构设计

```
┌─────────────────────────────────────────────────────────┐
│                    Gateway Runner                        │
│                  (gateway/run.py, 20983 行)              │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │  Telegram   │  │  Discord    │  │  WhatsApp   │    │
│  │  Adapter    │  │  Adapter    │  │  Adapter    │    │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘    │
│         │                │                │            │
│  ┌──────┴────────────────┴────────────────┴──────┐    │
│  │           BasePlatformAdapter                  │    │
│  │           (base.py, 5627 行)                   │    │
│  └─────────────────────┬─────────────────────────┘    │
│                        │                               │
│  ┌─────────────────────┴─────────────────────────┐    │
│  │           Session Manager                      │    │
│  │           (session.py)                         │    │
│  └─────────────────────┬─────────────────────────┘    │
│                        │                               │
│  ┌─────────────────────┴─────────────────────────┐    │
│  │           Agent Core                           │    │
│  │           (conversation_loop.py)               │    │
│  └───────────────────────────────────────────────┘    │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 1.3 核心抽象：BasePlatformAdapter

所有平台适配器继承自 `BasePlatformAdapter`（5627 行），实现以下接口：

```python
class BasePlatformAdapter(ABC):
    @abstractmethod
    async def connect(self) -> None: ...
    
    @abstractmethod
    async def disconnect(self) -> None: ...
    
    @abstractmethod
    async def send_message(self, target: str, content: str, ...) -> SendResult: ...
    
    @abstractmethod
    async def send_media(self, target: str, file_path: str, ...) -> SendResult: ...
    
    @abstractmethod
    async def start_polling(self) -> None: ...
    
    # ... 更多方法
```

### 1.4 插件系统

Hermes 支持通过插件添加新平台（`ADDING_A_PLATFORM.md`）：

```
~/.hermes/plugins/<platform>/
├── plugin.yaml      # 插件元数据
└── adapter.py       # 继承 BasePlatformAdapter
```

插件系统自动处理：
- 适配器创建和注册
- 配置解析
- 用户授权
- Cron 投递
- send_message 路由
- 系统提示词注入
- 状态显示
- 网关设置向导

---

## 二、Hermes 的微信适配器（weixin.py）

### 2.1 技术方案

| 维度 | 说明 |
|------|------|
| 协议 | Tencent iLink Bot API |
| 连接方式 | 长轮询 `getupdates` |
| 认证 | QR 码登录 |
| 媒体传输 | AES-128-ECB 加密 CDN |
| 依赖 | `aiohttp`, `cryptography` |

### 2.2 核心功能

- 接收文本/图片/语音/视频/文件消息
- 发送文本/图片/语音/文件消息
- QR 码登录流程
- 消息去重（`MessageDeduplicator`）
- Typing 状态指示
- 会话超时检测

### 2.3 与销销的差异

| 维度 | Hermes weixin.py | 销销 mind/wechat.py |
|------|------------------|---------------------|
| 目标平台 | 微信个人号 | 企业微信 + 公众号 |
| API | iLink Bot API | 企微自建应用 API + 公众号 API |
| 认证 | QR 码登录 | CorpID + Secret |
| 消息模式 | 长轮询 | Webhook 回调 |
| 加密 | AES-128-ECB | 企微加密（AES-CBC） |
| 媒体 | CDN 加密传输 | 企微媒体 API |

---

## 三、能否搬到销销？

### 3.1 短答案：**不能直接搬，但可以搬架构思想**

原因：
1. **目标平台不同**：Hermes 的 `weixin.py` 是微信个人号，销销用的是企业微信和公众号
2. **API 协议完全不同**：iLink Bot API vs 企微自建应用 API
3. **连接方式不同**：长轮询 vs Webhook 回调
4. **依赖不同**：Hermes 用 `aiohttp`（异步），销销用 `requests`（同步）

### 3.2 可以搬的架构思想

#### 3.2.1 BasePlatformAdapter 抽象

销销当前的 `mind/channel.py` 只有两个实现（`WeComChannel`、`MpChannel`），没有统一的抽象基类。

**可以搬**：定义 `BaseChannel` 抽象类，让所有通道实现统一接口。

```python
# 参考 Hermes 的 base.py 设计
class BaseChannel(ABC):
    @abstractmethod
    def send_text(self, user_id: str, text: str) -> dict: ...
    
    @abstractmethod
    def send_file(self, user_id: str, file_path: str, title: str = "") -> dict: ...
    
    @abstractmethod
    def send_image(self, user_id: str, image_path: str) -> dict: ...
    
    @abstractmethod
    def parse_message(self, raw_data: bytes) -> MessageEvent: ...
```

**好处**：未来加新通道（飞书、钉钉、Telegram）时，只需实现 `BaseChannel`。

#### 3.2.2 MessageEvent 标准化

Hermes 的 `MessageEvent` 是所有平台的统一消息格式：

```python
@dataclass
class MessageEvent:
    platform: str
    user_id: str
    content: str
    message_type: MessageType
    raw_data: dict
    # ...
```

**可以搬**：定义统一的 `MessageEvent`，让企微和公众号的消息都转成标准格式。

#### 3.2.3 消息去重（MessageDeduplicator）

Hermes 的 `MessageDeduplicator` 用 TTL 缓存去重，销销用内存 `set()`（有 bug）。

**可以搬**：搬 `MessageDeduplicator` 的 TTL 缓存逻辑，替换销销的 `_processed_msgs`。

#### 3.2.4 Typing 状态指示

Hermes 在处理消息时会发送 typing 状态，让用户知道 Agent 在工作。

**可以搬**：销销可以在企微里发送"正在输入..."状态（企微支持 `sendtyping`）。

#### 3.2.5 插件化通道管理

Hermes 的插件系统允许通过 `plugin.yaml` 注册新平台。

**可以搬**：销销可以设计类似的通道注册机制，让新通道以插件形式接入。

---

## 四、不该搬的

### 4.1 Gateway Runner（20983 行）

Hermes 的 gateway runner 是一个完整的网关系统，包含：
- 多平台路由
- 会话管理
- Cron 调度
- 流式输出
- Slash 命令
- 权限控制
- 状态监控

销销不需要这些。销销的 `main.py` 是一个轻量级 FastAPI 服务，只处理企微和公众号。

### 4.2 iLink Bot API 相关代码

Hermes 的 `weixin.py` 核心是 iLink Bot API 的封装，和企微 API 完全不同。搬这些代码没有意义。

### 4.3 异步架构

Hermes 全面使用 `asyncio`，销销使用同步 `requests` + `BackgroundTasks`。架构差异太大，强行搬会导致大量兼容性问题。

---

## 五、推荐的搬运策略

### 5.1 优先级 1：标准化通道抽象（1-2 天）

**目标**：定义 `BaseChannel` 抽象类 + `MessageEvent` 标准格式

**步骤**：
1. 定义 `mind/channel_base.py`（参考 Hermes `base.py` 的接口设计）
2. 改造 `WeComChannel` 和 `MpChannel` 继承 `BaseChannel`
3. 定义 `MessageEvent` 数据类
4. 改造 `main.py` 使用 `MessageEvent`

**好处**：
- 未来加飞书/钉钉/Telegram 只需实现 `BaseChannel`
- 消息处理逻辑统一，减少重复代码

### 5.2 优先级 2：消息去重升级（0.5 天）

**目标**：替换内存 `set()` 为 TTL 缓存

**步骤**：
1. 搬 Hermes 的 `MessageDeduplicator` 核心逻辑
2. 用 `cachetools.TTLCache` 或 Redis 实现
3. 替换 `main.py` 的 `_processed_msgs`

### 5.3 优先级 3：Typing 状态（0.5 天）

**目标**：在处理消息时发送 typing 状态

**步骤**：
1. 在 `WeComChannel` 加 `send_typing` 方法
2. 在 `process_text` 开始时调用

### 5.4 优先级 4：通道插件化（1 周，可选）

**目标**：设计插件式通道注册机制

**步骤**：
1. 定义 `plugin.yaml` 格式
2. 实现通道自动发现和注册
3. 改造 `main.py` 使用插件系统

---

## 六、如果想加飞书/钉钉/Telegram

### 6.1 加飞书

```python
# mind/channel_feishu.py
class FeishuChannel(BaseChannel):
    def send_text(self, user_id: str, text: str) -> dict:
        # 飞书 API 实现
        pass
    
    def parse_message(self, raw_data: bytes) -> MessageEvent:
        # 飞书消息解析
        pass
```

### 6.2 加 Telegram

```python
# mind/channel_telegram.py
class TelegramChannel(BaseChannel):
    def send_text(self, user_id: str, text: str) -> dict:
        # Telegram Bot API 实现
        pass
    
    def parse_message(self, raw_data: bytes) -> MessageEvent:
        # Telegram 消息解析
        pass
```

### 6.3 加钉钉

```python
# mind/channel_dingtalk.py
class DingTalkChannel(BaseChannel):
    def send_text(self, user_id: str, text: str) -> dict:
        # 钉钉 API 实现
        pass
    
    def parse_message(self, raw_data: bytes) -> MessageEvent:
        # 钉钉消息解析
        pass
```

---

## 七、结论

| 维度 | 判断 |
|------|------|
| 能否直接搬代码 | ❌ 不能（平台不同、协议不同、架构不同） |
| 能否搬架构思想 | ✅ 可以（BaseChannel 抽象、MessageEvent、MessageDeduplicator） |
| 搬运价值 | 中等（解决通道扩展性问题，但不是当前生产就绪的阻塞项） |
| 建议 | 先修 P0 问题（1-2 天），再考虑通道标准化（1-2 天） |

**核心结论**：Hermes 的 IM 集成**不能直接搬**，但它的**架构思想**（BaseChannel 抽象、MessageEvent 标准化、插件化通道管理）值得借鉴。如果未来要加飞书/钉钉/Telegram，可以参考 Hermes 的设计。
