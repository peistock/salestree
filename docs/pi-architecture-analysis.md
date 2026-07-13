# salestree（Pi 架构）分析报告

> 分析时间：2026-07-10
> 对比基线：sales-mind（Python FastAPI）
> 新架构：TypeScript Fastify + Pi Agent Framework

---

## 一、架构变化总览

### 1.1 技术栈对比

| 维度 | sales-mind（旧） | salestree（新） |
|------|-----------------|----------------|
| 语言 | Python 3.11 | TypeScript (Node.js 22+) |
| Web 框架 | FastAPI | Fastify |
| Agent 框架 | 自研 `AgentLoop` while 循环 | `@earendil-works/pi-agent-core` |
| LLM 客户端 | 自研 `LLMClient` (OpenAI SDK) | `@earendil-works/pi-ai` |
| DB 驱动 | psycopg2 (每次新建连接) | pg.Pool (连接池) ✅ |
| 消息协议 | 企微 XML + Web HTML | WebSocket only |
| 进程模型 | 单进程同步 + BackgroundTasks | 单进程异步 |

### 1.2 新增模块

```
server/src/
├── agent/
│   ├── Session.ts          # 单次用户交互封装（核心）
│   └── eventMapper.ts      # Pi 事件 → 销销事件
├── llm/
│   └── provider.ts         # Pi LLM Provider 工厂
├── db/
│   └── index.ts            # pg.Pool 连接池 ✅
├── memory/
│   └── Memory.ts           # 长期记忆加载
├── association/
│   └── AssociationEngine.ts
├── skills/
│   └── SkillLoader.ts
├── knowledge/
│   ├── KnowledgeBase.ts
│   └── VectorStore.ts
├── tools/
│   ├── Toolkit.ts          # AgentTool 注册
│   ├── getTime.ts
│   ├── searchWeb.ts
│   ├── fetchWebpage.ts
│   ├── dbTools.ts          # CRM 查询工具
│   ├── todoTools.ts        # Todo/Plan 工具
│   └── pythonProxy.ts      # 调用 Python 遗留工具
└── routes/
    ├── health.ts
    └── ws.ts               # WebSocket /ws/chat
```

---

## 二、解决的旧问题

### 2.1 ✅ DB 连接池（已解决）

```typescript
// 旧：每次新建连接
def get_conn():
    return psycopg2.connect(...)

// 新：连接池
const pool = new Pool({ host, port, user, password, database });
export async function query<T>(sql, params) {
    const client = await pool.connect();
    try { return client.query(sql, params).rows; }
    finally { client.release(); }
}
```

**改进**：解决了审计报告中的第 4 个问题。

### 2.2 ✅ Agent 循环简化（已解决）

```python
# 旧：1058 行的 agent_loop.py
class AgentLoop:
    def run(self, messages, max_iterations=50, ...):
        # 复杂的 while 循环、工具执行、上下文压缩、checkpoint...
```

```typescript
// 新：使用 Pi Agent 核心，181 行的 Session.ts
export class AgentSession {
    async run(query, send) {
        await this.agent.prompt(promptMessages);
    }
}
```

**改进**：Pi 框架封装了 Tool Calling while 循环、上下文管理、流式输出。代码量从 1058 行降到 181 行。

### 2.3 ✅ LLM 客户端简化（已解决）

```python
# 旧：275 行的 llm_client.py
class LLMClient:
    def chat_with_tools(self, messages, tools, ...):
        # 手动处理 OpenAI SDK、重试、降级...
```

```typescript
// 新：使用 Pi 的 Provider 抽象
const provider = createProvider({
    id: providerId,
    baseUrl,
    auth: { apiKey: envApiKeyAuth(...) },
    models: [{ id: modelId, ... }],
    api: openAICompletionsApi(),
});
```

**改进**：Pi 封装了 Provider/Model/Api 抽象，代码更简洁。

### 2.4 ✅ 流式输出（已解决）

```typescript
// 新：Pi 的事件驱动流式
this.agent.subscribe((event) => {
    if (event.type === "message_update") {
        send({ type: "token", content: e.delta });
    }
});
```

**改进**：Pi 原生支持流式输出，不需要手动实现。

---

## 三、未解决的旧问题

### 3.1 ❌ 消息去重（未解决）

`routes/ws.ts` 中没有消息去重逻辑。WebSocket 场景下，客户端可能重发消息。

```typescript
// 当前：无去重
socket.on("message", async (raw) => {
    const data = JSON.parse(raw.toString());
    // 直接处理，无去重检查
});
```

### 3.2 ❌ 多用户并发（部分解决）

```typescript
// 当前：用户级串行（abort 旧会话）
activeSessions.get(userId)?.abort();
const session = new AgentSession(userId);
activeSessions.set(userId, session);
```

**问题**：
- 同一用户的新消息会 abort 旧会话（合理）
- 但 `activeSessions` 是内存 Map，重启丢失
- 没有跨进程锁

### 3.3 ❌ LLM 自动降级（未解决）

```typescript
// 当前：只用一个 provider
const provider = createProvider({
    baseUrl: config.llm.baseUrl,
    ...
});
```

没有 fallback 到云端 API 的逻辑。本地 LM Studio 挂了就瘫痪。

### 3.4 ❌ 关联引擎仍然是硬编码

```typescript
// AssociationEngine.ts - 和旧版完全相同的规则表
const ASSOCIATION_RULES: Record<string, string[]> = {
    客户: ["account.basic", "account.signals", "deal.stage"],
    ...
};
```

### 3.5 ❌ 企微集成丢失

新架构只有 WebSocket 接入，**没有企微适配器**。销售只能通过 Web 聊天页使用，不能通过企微使用。

### 3.6 ❌ 协作面丢失

早间简报、午间快讯、晚间复盘、紧急插话等功能在新架构中没有实现。

---

## 四、Pi 框架本身的分析

### 4.1 Pi 的核心抽象

```
@earendil-works/pi-agent-core
├── Agent              # Agent 实例
├── AgentTool          # 工具定义
├── AgentMessage       # 消息格式
└── StreamFn           # 流式函数

@earendil-works/pi-ai
├── Provider           # LLM 提供商
├── Model              # 模型定义
├── Api                # API 协议（openai-completions）
├── Models             # 模型注册表
└── Context            # 上下文
```

### 4.2 Pi 的优势

| 优势 | 说明 |
|------|------|
| 简洁 API | `agent.prompt(messages)` 一行代码启动循环 |
| 流式输出 | 原生支持 `agent.subscribe()` |
| 工具系统 | `AgentTool` 类型安全 |
| Provider 抽象 | 支持多 LLM 提供商切换 |
| 事件驱动 | `turn_end`、`tool_execution_start/end` 等事件 |

### 4.3 Pi 的局限

| 局限 | 说明 |
|------|------|
| 无上下文压缩 | 需要自己实现 |
| 无会话持久化 | 需要自己实现 |
| 无错误分类/重试 | 需要自己实现 |
| 无多轮对话管理 | 需要自己实现 |
| 社区小 | `@earendil-works` 不是主流框架 |

---

## 五、与 Hermes 的对比

| 维度 | Pi（当前） | Hermes |
|------|-----------|--------|
| 成熟度 | 小众框架 | 212k stars |
| 会话管理 | 无（自己实现） | SQLite + FTS5 |
| 上下文压缩 | 无（自己实现） | 3168 行生产级 |
| 错误处理 | 基础 | 10+ FailoverReason |
| IM 集成 | 无 | Telegram/Discord/Slack/WhatsApp/Signal |
| 插件系统 | 无 | 完整插件架构 |
| 上下文窗口 | 手动配置 | 自动检测 |

---

## 六、建议

### 6.1 短期（1-2 周）：补全基础

| # | 任务 | 工作量 |
|---|------|--------|
| 1 | 消息去重（DB 或内存 TTL） | 0.5 天 |
| 2 | LLM fallback（加云端 API） | 0.5 天 |
| 3 | 上下文压缩（搬 Hermes 逻辑） | 1 天 |
| 4 | 会话持久化（DB 存对话历史） | 1 天 |

### 6.2 中期（2-4 周）：恢复功能

| # | 任务 | 工作量 |
|---|------|--------|
| 5 | 企微适配器（TypeScript） | 3 天 |
| 6 | 协作面（简报/复盘/插话） | 3 天 |
| 7 | 跨会话搜索（FTS5） | 2 天 |

### 6.3 长期（1-2 月）：架构升级

| # | 任务 | 工作量 |
|---|------|--------|
| 8 | 评估是否迁移 Hermes 架构 | 2 周 |
| 9 | 或者继续在 Pi 上构建 | 持续 |

---

## 七、核心判断

**Pi 框架是一个轻量级的 Agent SDK，适合快速原型，但不适合生产级系统。**

它解决了旧架构的两个核心痛点：
1. DB 连接池（`pg.Pool`）
2. Agent 循环简化（`Agent` 类封装）

但它**没有解决**：
1. 会话持久化
2. 上下文压缩
3. 错误分类/重试
4. 多用户状态管理
5. IM 集成

**建议**：继续在 Pi 上构建，但要**补齐 Pi 缺失的能力**。具体来说：
- 从 Hermes 搬 `error_classifier.py`、`context_compressor.py`、`iteration_budget.py`
- 自己实现会话持久化和消息去重
- 企微适配器用 TypeScript 重写（比 Python 更适合 WebSocket）

**不要做的事**：
- 不要再换框架（Pi → Hermes 迁移成本太高）
- 不要在 mind/ 目录里继续写 Python（TypeScript 和 Python 混用会越来越混乱）
