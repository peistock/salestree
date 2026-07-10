# 销销生产就绪性审计报告

> 审计时间：2026-07-10
> 审计范围：核心代码（agent_loop / memory / coordinator / wechat / llm_client / tools / subagent / interruption）
> 目标：评估团队投入使用的阻塞项

---

## 一、阻塞团队使用的硬伤

### 1. 多用户并发是假的

**文件**：`main.py:69-79`

消息去重用内存 `set()`，串行锁用内存 `dict`。进程重启全部丢失。企微可能瞬间推 10 条消息过来，重启后全部重复处理。

```python
_processed_msgs = set()           # 内存级，重启清空
_user_locks: dict[str, threading.Lock] = {}  # 内存级
```

`main.py:246-247`：去重缓存到 10000 条直接 `clear()`，之后的重复消息全部放行。这是数据竞争，不是去重。

**修复方案**：去重改 DB 表（已有的 `notifications` 表改造即可），锁改 Redis 分布式锁。

---

### 2. 企微 5 秒超时没兜住

**文件**：`main.py:249-256`

先回"收到，我想一下..."，然后 `BackgroundTasks.add_task`。但企微被动回复要求 **5 秒内**返回 XML。如果 DB 慢、LLM 预热，`process_text` 里的 3 分钟超时计时器才兜底——企微早就断了连接。

而且 `process_text` 里的 3 分钟超时只发了一条安慰消息，没有终止执行。Agent 继续跑完后还会再推一条结果，用户看到两条消息（先说"还在忙"，后面突然出来完整结果），体验混乱。

**修复方案**：弃用被动回复 XML，改用企微主动推送 API（`/cgi-bin/message/send`），不受 5 秒限制。

---

### 3. 单机 LLM 是单点故障

**文件**：`llm_client.py:57`

```python
self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=300)
```

本地 LM Studio 挂了，整个系统瘫痪。`_fallback_model` 只在三个本地模型之间切换——全都不可用时没有任何降级。

CLAUDE.md 写了 DeepSeek/百炼作为备选，但代码里没有自动切换逻辑。需要手动改 `.env` 重启。

**修复方案**：`_fallback_model` 加云端 API URL，配好即可自动 fallback。

---

### 4. 数据库连接无连接池

**文件**：`memory.py:37-41`

```python
def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, dbname=DB_NAME
    )
```

每次 `get_conn()` 都新建连接，用完 `conn.close()`。10 个并发用户 = 10 个 DB 连接，无复用、无池化。PG 连接有上限，默认 100，多个用户同时触发搜索/写入就爆。

**修复方案**：用 `psycopg2.pool.ThreadedConnectionPool` 或 `asyncpg`。

---

### 5. 关联引擎是硬编码规则表

**文件**：`association_engine.py:20-59`

17 个关键词映射到记忆类型，纯字符串匹配。"快手"、"三只松鼠"这类具体客户名完全匹配不到。CLAUDE.md 写的"灵性关联"在代码里不存在——不是语义匹配，就是 `keyword in query` 判断。

更致命的是 SQL 查询（`association_engine.py:62-123`）全用 `ORDER BY created_at DESC LIMIT 3/5`，没有相关性排序。用户问"快手最近怎么了"，返回的是最近创建的 3 条 account，不一定是快手。

**修复方案**：用 embedding 做相似度匹配替代关键词硬匹配。

---

### 6. 上下文压缩会注入脏数据

**文件**：`context_compressor.py:229`

```python
compressed.append({"role": "user", "content": f"[上下文摘要] {summary}"})
```

用 **user 角色**注入系统摘要，会污染 LLM 对用户意图的理解。而且压缩触发条件是每 4 轮迭代（`agent_loop.py:468`），不是按 token 数触发，和压缩器内部的 `DEFAULT_TARGET_TOKENS=8000` 逻辑冲突。

---

### 7. 子 Agent 没有超时控制

**文件**：`subagent.py:65`

子 Agent 的 `MAX_ITERATIONS=15`，每次 LLM 调用可能 10-30 秒（本地 35B 模型）。一个子 Agent 可能跑 **15 × 30s = 7.5 分钟**。3 个并行子 Agent 同时跑 = 最坏情况 7.5 分钟。没有 timeout，主 Agent 被阻塞。

**文件**：`coordinator.py:270-274`

```python
# Python ThreadPoolExecutor 不支持强制终止线程
pass
```

取消任务的注释直接写了不支持，pass 了。任务取消是假的。

**修复方案**：子 Agent 每轮检查 `threading.Event`，检测到取消信号后退出循环；`future.result(timeout=60)` 而不是无限等。

---

### 8. 意图路由过于粗糙

**文件**：`intent_router.py:96-111`

```python
if len(text) > 40:
    return ("complex", 0.6)
if len(text) < 20:
    return ("simple", 0.5)
```

- "帮我查一下快手最近有没有融资消息"（20 字）→ 判为 simple → 只跑 1 轮工具调用 → 搜不到完整信息就出结果
- "帮我查下茅台最新股价是多少"（14 字）→ `has_action` 规则判为 simple → 需要搜索 + 时效性验证但被简化

长度启发式和动作词规则冲突，误判率高。

---

## 二、生产就绪性缺口总览

| 维度 | 现状 | 差距 |
|------|------|------|
| **多用户隔离** | 内存锁 + 内存去重 | 需要 DB/Redis 级别的状态管理 |
| **LLM 故障转移** | 手动改 .env | 需要自动 fallback 到云端 API |
| **DB 连接管理** | 每次新建连接 | 需要连接池 |
| **任务取消** | 假的（pass） | 需要 threading.Event 或进程级中断 |
| **企微超时** | 被动回复 XML（5s） | 需要改主动推送 |
| **监控告警** | 只有日志 | 无 metrics、无健康检查深度、无告警 |
| **部署方式** | 本地 Python 进程 | 无验证过的 Dockerfile，无 systemd |

---

## 三、修复优先级

### P0 — 立刻修（1-2 天，不修不能上线）

| # | 问题 | 修复方案 | 工作量 |
|---|------|---------|--------|
| 1 | 消息去重用内存 set | 改 DB 表 + UNIQUE 约束 | 0.5 天 |
| 2 | 串行锁用内存 dict | 改 Redis 分布式锁 | 0.5 天 |
| 3 | LLM 无自动 fallback | `_fallback_model` 加云端 API | 0.5 天 |
| 4 | DB 无连接池 | `ThreadedConnectionPool(minconn=2, maxconn=10)` | 0.5 天 |
| 5 | 子 Agent 无 timeout | `future.result(timeout=60)` | 0.5 天 |

### P1 — 上线前必须做（1-2 周）

| # | 问题 | 修复方案 | 工作量 |
|---|------|---------|--------|
| 6 | 企微 5 秒超时 | 弃用被动回复，改主动推送 API | 2 天 |
| 7 | 任务取消假的 | 子 Agent 每轮检查 `threading.Event` | 1 天 |
| 8 | 上下文压缩注入 user 角色 | 改为 `{"role": "system", ...}` 或自定义角色 | 0.5 天 |
| 9 | 意图路由误判 | 去掉长度启发式，改用 LLM 分类或更精细规则 | 1 天 |

### P2 — 上线后迭代（2-4 周）

| # | 问题 | 修复方案 | 工作量 |
|---|------|---------|--------|
| 10 | 关联引擎硬编码 | 用 embedding 相似度替代关键词匹配 | 3 天 |
| 11 | 监控告警 | 加 Prometheus metrics + 告警规则 | 2 天 |
| 12 | 部署方式 | Dockerfile 验证 + systemd 服务化 | 1 天 |

---

## 四、架构层面的判断

### 做对了的

1. **Tool Calling while 循环**（`agent_loop.py`）：主循环逻辑清晰，有并行执行、checkpoint 保存、orphan message 修复，这是好的基础
2. **并行工具执行**（`agent_loop.py:437-463`）：`PARALLEL_SAFE_TOOLS` 白名单控制，避免无状态工具串行浪费时间
3. **Guardrail 时效性拦截**（`agent_loop.py:487-495`）：检测到时效性关键词但未搜索时强制重搜，防止 LLM 凭记忆胡说
4. **用户级串行锁**（`main.py:460-476`）：同一用户任务排队，避免并行冲突（虽然实现有问题，但设计方向对）
5. **TaskCoordinator 编排层**（`coordinator.py`）：Task 生命周期管理、依赖调度、子任务隔离，架构上是对的

### 设计过度了的

1. **心跳话术池**（`agent_loop.py:166-234`）：120 行话术池随机轮换，这是产品层面的事，不应该在核心循环里硬编码
2. **`_reflect_on_past` 自我观察**（`agent_loop.py:868-932`）：模糊请求触发历史查询，但查询逻辑是 `last_user_msg[:10]` 做 BM25，基本匹配不到任何有用的东西
3. **公众号身份识别**（`main.py:273-363`）：个人订阅号无客服消息权限（CLAUDE.md 已承认），但这套代码还在跑，白耗维护成本

### 根本性矛盾

CLAUDE.md 声明"单销售团队，暂不做多租户隔离"，但代码里 `owner_id` 到处都在用。这不是"暂不做"，是**做了一半**——DB schema 有多租户字段，但连接管理、锁、去重全是单用户级别。要么彻底简化为单用户，要么补齐多租户，现在卡在中间状态最危险。

---

## 五、结论

销销的架构设计有前瞻性（三足鼎立、关联引擎、自适应技能），但**实现质量跟不上设计野心**。核心问题是：

1. **状态管理全部在内存**：多用户并发、重启丢失、去重失效
2. **LLM 和 DB 没有容错**：单点故障，无 fallback，无连接池
3. **异步任务不可控**：取消是假的，超时只安慰不终止
4. **关联引擎名不副实**：设计是语义关联，实现是关键词 in 判断

**建议**：先修 P0 的 5 个问题（1-2 天），再做 P1 的企微超时修复（2 天），然后才能让团队试用。否则上线即踩坑。
