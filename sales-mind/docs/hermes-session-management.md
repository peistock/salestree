# Hermes 历史任务管理机制分析

> 分析时间：2026-07-10
> 目标：理解 Hermes Agent 如何管理和保存历史会话/任务，评估是否值得借鉴

---

## 一、Hermes 的存储架构

### 1.1 双层存储

```
┌─────────────────────────────────────────────────────────┐
│                    存储层                                 │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  SQLite: state.db                                │   │
│  │  - 会话元数据（session_id, platform, timestamps）│   │
│  │  - 消息内容（user/assistant/tool messages）      │   │
│  │  - FTS5 全文索引（跨会话搜索）                   │   │
│  │  - 压缩状态（compressed/expanded）               │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  JSONL 文件：会话转录                             │   │
│  │  - 完整对话历史（原始格式）                       │   │
│  │  - 工具调用详情                                   │   │
│  │  - 导出/备份用                                   │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  文件系统：技能/记忆                              │   │
│  │  - ~/.hermes/skills/（技能文件）                 │   │
│  │  - ~/.hermes/memory/（记忆文件）                 │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 1.2 SQLite 表结构（推断）

根据代码分析，`state.db` 包含以下核心表：

```sql
-- 会话表
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    platform TEXT,           -- telegram/discord/slack/weixin/signal/...
    chat_id TEXT,            -- 平台聊天 ID
    user_id TEXT,            -- 用户 ID
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    compressed BOOLEAN,      -- 是否已压缩
    message_count INTEGER,
    title TEXT,              -- 会话标题（自动生成或用户指定）
    summary TEXT             -- 会话摘要
);

-- 消息表
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    role TEXT,               -- user/assistant/tool/system
    content TEXT,
    tool_calls TEXT,         -- JSON 格式的工具调用
    tool_call_id TEXT,
    timestamp TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

-- FTS5 全文索引
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=id
);
```

---

## 二、会话生命周期

### 2.1 创建会话

```
用户发消息 → 平台适配器接收 → Gateway 路由 → 创建/复用会话
```

**会话键（Session Key）格式**：
```
agent:main:<platform>:<chat_id>
```

例如：
- `agent:main:telegram:123456789`
- `agent:main:discord:987654321`
- `agent:main:weixin:user123`

### 2.2 会话复用 vs 新建

Hermes 的 `get_or_create_session()` 逻辑：

```python
def get_or_create_session(session_key: str) -> Session:
    # 1. 检查是否有活跃会话
    existing = db.get_active_session(session_key)
    if existing and not is_expired(existing):
        return existing
    
    # 2. 检查是否有待恢复会话（重启后）
    pending = db.get_pending_session(session_key)
    if pending and is_within_freshness_window(pending):
        return pending
    
    # 3. 创建新会话
    return db.create_session(session_key)
```

**自动恢复窗口**：
- 默认 1 小时（`_AUTO_CONTINUE_FRESHNESS_SECS_DEFAULT = 3600`）
- 重启后 1 小时内的会话可以自动恢复
- 可通过 `HERMES_AUTO_CONTINUE_FRESHNESS` 环境变量配置

### 2.3 会话重置策略

Hermes 支持多种重置策略：

| 策略 | 触发条件 | 说明 |
|------|---------|------|
| `manual` | 用户手动 `/new` | 只有手动才重置 |
| `idle_timeout` | 空闲超时 | 默认 30 分钟 |
| `daily` | 每天零点 | 自动新建当天会话 |
| `message_count` | 消息数超限 | 防止会话过长 |

### 2.4 会话压缩

当会话过长时，Hermes 会压缩中间轮次：

```
原始会话（100 轮） → 压缩 → 头部（10 轮）+ 摘要 + 尾部（20 轮）
```

**压缩触发条件**：
- Token 数超过上下文窗口的 75%
- 消息数超过阈值

**压缩后**：
- 原始消息保留在 JSONL 文件
- SQLite 中标记为 `compressed=True`
- 摘要注入到会话头部

---

## 三、历史任务管理

### 3.1 任务持久化

Hermes 的任务（Todo）存储在内存中，但会定期持久化：

```python
class TodoStore:
    def __init__(self):
        self._todos: List[Todo] = []
    
    def save_to_disk(self):
        """持久化到 state.db"""
        db.save_todos(self.session_id, self._todos)
    
    def load_from_disk(self):
        """从 state.db 加载"""
        self._todos = db.load_todos(self.session_id)
```

**持久化时机**：
- 每次 todo 状态变更后
- 会话结束时
- 上下文压缩时（重新注入）

### 3.2 技能管理（Curator）

Hermes 的 `curator.py` 管理技能的生命周期：

```
创建 → 使用 → 评估 → 优化 → 归档/删除
```

**技能存储**：
```
~/.hermes/skills/
├── skill_1/
│   ├── skill.yaml      # 元数据
│   ├── prompt.md       # 提示词
│   └── examples/       # 示例
├── skill_2/
│   └── ...
```

**技能评估指标**：
- 使用次数
- 成功率
- 用户反馈
- 最后使用时间

**自动归档**：
- 超过 30 天未使用 → 标记为 inactive
- 超过 90 天未使用 → 归档
- cron 引用的技能永不归档

### 3.3 跨会话搜索

Hermes 支持跨会话搜索历史任务：

```python
def session_search(query: str, limit: int = 10) -> List[Session]:
    """FTS5 全文搜索"""
    # 1. 搜索消息内容
    results = db.search_messages(query, limit)
    
    # 2. 搜索会话标题
    title_results = db.search_session_titles(query, limit)
    
    # 3. 合并去重
    return merge_and_dedup(results, title_results)
```

**搜索范围**：
- 消息内容（user/assistant/tool）
- 会话标题
- 会话摘要
- 技能名称

### 3.4 会话导出

Hermes 支持多种导出格式：

```bash
# JSONL 格式（完整转录）
hermes sessions export <session_id> --format jsonl

# Markdown 格式（可读）
hermes sessions export <session_id> --format markdown

# 仅用户提示
hermes sessions export <session_id> --only user-prompts
```

---

## 四、与销销的对比

### 4.1 会话存储

| 维度 | Hermes | 销销 |
|------|--------|------|
| 存储引擎 | SQLite（state.db） | PostgreSQL |
| 消息格式 | JSONL + SQLite 表 | PostgreSQL 表（episodic_memory） |
| 全文搜索 | FTS5 | 无（episodic_memory 未建 FTS/GIN 索引） |
| 语义搜索 | 无 | PGVector，但仅用于知识库（knowledge_embeddings），不用于会话消息 |
| 压缩 | 支持（75% token 阈值，保头尾） | 支持（默认 75% token 阈值，保头尾） |
| 跨会话搜索 | ✅ 有 | ❌ 无 |

### 4.2 任务管理

| 维度 | Hermes | 销销 |
|------|--------|------|
| Todo 存储 | 内存 + 持久化到 SQLite | JSON 文件 |
| Todo 恢复 | 自动（重启后从 DB 加载） | 手动（用户说"继续"） |
| 技能管理 | 自动评估/归档（curator） | 手动安装/删除 |

### 4.3 历史检索

| 维度 | Hermes | 销销 |
|------|--------|------|
| 搜索方式 | FTS5 关键词搜索 | 无跨会话搜索；仅有 `get_episodes(user_id, limit)` 按时间倒序取最近记录 |
| 搜索范围 | 全部会话 | 当前线程 / 最近 episodes |
| 摘要生成 | LLM 自动生成 | 手动 + 上下文压缩时自动生成 |
| 跨会话回忆 | ✅ 有（session_search） | ❌ 无 |

---

## 五、值得借鉴的设计

### 5.1 会话自动恢复

**Hermes 的做法**：
- 重启后自动扫描未完成的会话
- 1 小时内的会话自动恢复
- 提示用户"之前的任务还没做完"

**销销的现状**：
- 有 checkpoint 机制，但需要用户手动说"继续"
- `_notify_unfinished_checkpoints()` 只通知，不自动恢复

**借鉴方案**：
```python
# 改进 process_text
def process_text(user_id, content):
    # 检查是否有待恢复的会话
    pending = get_pending_session(user_id)
    if pending and is_within_freshness_window(pending):
        # 自动恢复，而不是等用户说"继续"
        return resume_session(user_id, pending)
```

### 5.2 FTS5 全文搜索

**Hermes 的做法**：
- SQLite FTS5 索引所有消息
- 支持跨会话搜索
- 返回相关会话列表

**销销的现状**：
- 只有向量语义搜索
- 不支持跨会话搜索
- 不支持关键词精确匹配

**借鉴方案**：
```python
# 在 PostgreSQL 中加全文搜索
CREATE INDEX idx_messages_content_gin 
ON messages USING gin(to_tsvector('chinese', content));

-- 搜索
SELECT * FROM messages 
WHERE to_tsvector('chinese', content) @@ to_tsquery('chinese', '快手 融资');
```

### 5.3 技能自动评估

**Hermes 的做法**：
- 跟踪技能使用次数、成功率
- 自动归档低使用率技能
- cron 引用的技能永不归档

**销销的现状**：
- 技能手动安装/删除
- 没有使用统计
- 没有自动归档

**借鉴方案**：
```python
# 在 skills 表加使用统计
ALTER TABLE skills ADD COLUMN usage_count INTEGER DEFAULT 0;
ALTER TABLE skills ADD COLUMN success_rate FLOAT DEFAULT 1.0;
ALTER TABLE skills ADD COLUMN last_used_at TIMESTAMP;
ALTER TABLE skills ADD COLUMN status TEXT DEFAULT 'active';

-- 定期归档
UPDATE skills SET status = 'archived' 
WHERE last_used_at < NOW() - INTERVAL '90 days'
AND id NOT IN (SELECT skill_id FROM cron_jobs WHERE status = 'active');
```

### 5.4 会话压缩优化

**Hermes 的做法**：
- 75% token 阈值触发
- 保护头尾，压缩中间
- 压缩后重新注入 todo

**销销的现状**：
- `context_compressor.py` 已实现 75% token 阈值触发（`DEFAULT_TRIGGER_RATIO = 0.75`），与 Hermes 相同
- 保护头部 system + 前 2 轮，保护尾部最近 4 条消息
- 压缩后通过 `todo_store.format_for_injection()` 重新注入 todo（见 `context_compressor.py` 顶部说明）

**借鉴方案**：
压缩机制本身已与 Hermes 对齐，无需大改。可补充的是：
1. 在 `agent_loop.py` 压缩后检查 `todo_store.format_for_injection()` 是否真的被注入到 messages 中。
2. 将压缩阈值、保护轮数暴露为环境变量，便于不同模型上下文窗口调整。

---

## 六、搬运优先级

### P0 — 立刻搬（1-2 天）

| # | 功能 | 来源 | 目标 | 价值 |
|---|------|------|------|------|
| 1 | 会话自动恢复 | Hermes session.py | main.py | 高（用户体验） |
| 2 | Todo 持久化 | Hermes todo_tool.py | todo_store.py | 高（任务不丢失） |

### P1 — 上线前搬（3-5 天）

| # | 功能 | 来源 | 目标 | 价值 |
|---|------|------|------|------|
| 3 | FTS5/GIN 全文搜索 | Hermes session_search | memory.py | 中（跨会话检索） |
| 4 | 技能使用统计 | Hermes curator.py | skill_installer.py | 中（技能管理） |
| 5 | 压缩阈值可配置 | Hermes context_compressor.py | context_compressor.py | 低（运维灵活性） |

### P2 — 上线后迭代（1-2 周）

| # | 功能 | 来源 | 目标 | 价值 |
|---|------|------|------|------|
| 6 | 会话导出 | Hermes sessions export | 新功能 | 低（锦上添花） |
| 7 | 会话标题自动生成 | Hermes session.py | memory.py | 低（锦上添花） |

---

## 七、结论

Hermes 的历史任务管理机制**成熟且值得借鉴**，核心设计：

1. **双层存储**：SQLite（快速查询）+ JSONL（完整转录）
2. **自动恢复**：重启后 1 小时内自动恢复未完成会话
3. **FTS5 搜索**：跨会话全文搜索
4. **技能评估**：自动统计使用率，归档低使用率技能
5. **压缩优化**：75% token 阈值，保护头尾（销销已实现同类机制）

**建议**：先搬 P0 的 2 个功能（1-2 天），解决任务丢失和用户体验问题。再搬 P1 的 3 个功能（3-5 天），加强跨会话检索、技能管理和运维灵活性。
