# 销销 工具规范 v1.1（B2B 销售适配版）

> **文档性质**：架构契约与开发接口规范  
> **适用对象**：原子工具开发者、复合工具编排者、Skill 审核者（销售负责人/管理员）  
> **版本日期**：2026-07-10  
> **状态**：销销销售场景基线版（Tool Calling + 前置校验 + md_to_pdf + 销售风险扫描 + Skill 拆分）  

---

## 一、设计哲学：客户洞察 + 商机驱动

销销不是"通用 ChatGPT"，而是**帮销售团队更快懂客户、更准做沟通、更细管商机**的协作者。客户研究是主线，商机跟进是目标，销售风险扫描是底线，三者共用同一套记忆体和技能树。

**Research-First, Deal-Always**：

```
        对外：销售输出（高频、显性、赢单导向）
        ┌─────────────────────────────────────┐
        │  research │ outreach  │ content     │
        │  analytics│           │             │
        └─────────────────────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        │      客户与商机状态镜像      │
        │  （公司画像 + 联系人画像 + 商机阶段 + 互动历史） │
        └─────────────┬─────────────┘
                      │
        ┌─────────────────────────────────────┐
        │  risk     │ admin     │ world       │
        └─────────────────────────────────────┘
        对内：销售辅助（低频、隐性、风控）
```

**五条铁律**：

1. **Research-First**：销售打开销销的第一动机是"帮我查这个客户/行业/竞品"，Agent 必须优先响应研究意图。
2. **Deal-Always**：商机跟进不消失，而是**隐性基线**。任何研究/内容交互都伴随销售风险扫描（如客户提到"预算不够""竞品已签"）。
3. **记忆驱动，双轨关联**：工具执行前必须声明 `memory_deps` 与 `risk_deps`，销售轨加载 account/contact/deal/activity，风险轨加载 pricing/churn/competitor 信号。
4. **来源可信是核心技术壁垒**：所有研究类输出必须标注信息来源，禁止生成无来源的"据说"。
5. **透明可审计 + 销售共治**：用户可随时问"你都知道我些什么"；销售负责人在后台既是观众（看研究流）也是治理者（审 Skill、修正输出标准、确认风险警报）。

---

## 二、三阶工具模型

| 阶次 | 名称 | 定义 | 生命周期 |
|-----|------|------|---------|
| T1 | **原子工具**（Atomic） | 单次调用、无状态、幂等、直接映射到底层能力 | 永久内置 |
| T2 | **复合工具**（Compound） | 多步编排、有状态、含分支与异常处理、封装销售工作流 | 负责人审核后发布 |
| T3 | **自适应工具**（Adaptive） | 从对话历史中自动提取的重复模式，经人工确认后转正 | 草稿 → 试用 7 天 → 转正/废弃 |

---

## 三、记忆体与技能树的双轨关系

```
输入信号
    │
    ├─→ 意图域路由（Domain Router）
    │       │
    │       ├─→ 销售轨：research / outreach / content / analytics
    │       │              ↓
    │       │         加载记忆：accounts, contacts, deals, activities,
    │       │                  recent_research, industry_signals
    │       │              ↓
    │       │         生成个性化销售输出
    │       │
    │       └─→ 风控轨：risk / admin
    │                      ↓
    │                 加载记忆：deal_stage, competitor_mentions,
    │                          pricing_pressure, churn_signals
    │                      ↓
    │                 生成提醒/预警/管理建议
    │
    └─→ 隐性基线扫描（Always-On Sales Risk Scanner）
               ↓
          任何域的对话都扫描：
          • 竞品信号（竞品/别家/替换/对比）
          • 价格压力（贵/预算/折扣/降价）
          • 时间紧迫（急/尽快/截止）
          • 负面反馈（不满意/投诉/不靠谱）
          • 流失风险（不合作/终止/换供应商）
               ↓
          触发静默通知销售负责人 or 升级响应策略
```

---

## 四、Skill Manifest 规范

所有工具必须以单一文件声明，路径为 `skills/{domain}/{tool_name}/skill.yaml`。

### 4.1 完整模板

```yaml
---
# ========== 元数据区 ==========
name: account_research_battlecard
version: 1.0.0
domain: research                       # 必填：research / outreach / content / analytics / risk / admin / world
type: compound                         # atomic | compound | adaptive
status: published                      # draft | trial | published | deprecated
author: system
created_at: "2026-07-10"
reviewed_by: "sales_lead"

# ========== 触发器区 ==========
triggers:
  - type: manual
    command: "研究一下这家公司"
  - type: manual
    command: "客户背景"
  - type: event
    event: "message.contains_company_name"
    condition: "intent_domain == research"

# ========== 记忆依赖区（双轨）==========
memory_deps:
  # 销售轨
  - domain: accounts
    query: "name ILIKE %company%"
    required: false
  - domain: contacts
    query: "account_id = %account_id%"
    required: false
  - domain: deals
    query: "account_id = %account_id% ORDER BY updated_at DESC"
    required: false
  - domain: activities
    query: "entity_id = %account_id% LIMIT 5"
    required: false
  # 风控轨（隐性基线）
  - domain: industry_signals
    query: "recent_news.company=%company%"
    required: false

risk_deps:                             # 销售风险扫描专用
  - domain: competitor_mentions
    query: "recent"
    required: false
  - domain: pricing_pressure
    query: "account=%account_id%"
    required: false

# ========== 工具链区 ==========
tools_chain:
  - step: 1
    tool: search_web
    alias: company_news
    params:
      query: "{{account.name}} 2026 财报 融资 高管"
      time_range: "month"
    output_mode: structured
  - step: 2
    tool: search_web
    alias: competitor_intel
    params:
      query: "{{account.industry}} 竞品 广告投放 案例"
      max_results: 5
  - step: 3
    tool: local_llm
    alias: battlecard
    template: "account_battlecard"
    inputs:
      account: "{{memory.accounts}}"
      news: "{{steps.company_news.result}}"
      competitors: "{{steps.competitor_intel.result}}"
      style: "{{memory.user_profile.communication_style}}"
  - step: 4
    tool: send_wechat
    params:
      target: "{{user.id}}"
      content: "{{steps.battlecard.result}}"
    on_failure: retry_exponential

# ========== 异常与回退区 ==========
fallback:
  missing_required_memory: "我先查一下这家公司的公开信息，稍后给您整理。"
  tool_failure: "公开信息检索遇到一点问题，我先把我已知的信息发给您，缺少的部分您再补充。"
  no_response_30min: null

# ========== 安全与权限区 ==========
safety:
  auto_execute: true
  human_in_the_loop: false
  data_sensitivity: "medium"
  allowed_channels: ["wechat", "web"]
  audit_level: "verbose"
  content_moderation: "sales_ready"
  extreme_risk_action: "escalate"       # 检测到流失风险/严重负面反馈时上报负责人

# ========== 用户友好层 ==========
user_friendly:
  format: "short_paragraph"
  max_chars_per_paragraph: 80
  tone: "professional"
  include_emoji: false
  source_citation: true

# ========== 销售工作区关联 ==========
source_workspace:
  enabled: true
  project_type: "account_research"
  auto_save_draft: true
  version_on_complete: true
---

## Markdown 正文区

### 用途
为销售生成一页带信息来源的客户 battlecard，包含公司定位、财务/融资、高管动态、招聘信号、竞品对比、行业口碑。

### 典型交互
销售输入"研究一下快手" → Agent 识别 account → 并行搜索公司新闻/融资/竞品/招聘 → 生成 Markdown battlecard → 询问"需要我针对张总写一封跟进邮件吗？" → 确认后调用 outreach-drafter。

### 负责人审核备注
> 该工具已沉淀销售团队"先查来源、再出结论"的标准，试用期内无异常。
```

### 4.2 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | Skill 标识，不含空格，唯一 |
| `domain` | 是 | 七大域之一：`research` `outreach` `content` `analytics` `risk` `admin` `world` |
| `type` | 是 | `atomic` `compound` `adaptive` |
| `status` | 是 | `draft` `trial` `published` `deprecated` |
| `triggers` | 是 | 触发条件列表 |
| `memory_deps` | 否 | 销售轨记忆依赖 |
| `risk_deps` | 否 | 销售风险扫描记忆依赖 |
| `tools_chain` | compound 必填 | 原子工具编排顺序 |
| `fallback` | 否 | 异常回退话术 |
| `safety` | 否 | 安全与权限配置 |
| `user_friendly` | 否 | 用户友好输出配置 |
| `source_workspace` | 否 | 销售工作区关联 |

---

## 五、原子工具（T1）接口规范

### 5.1 注册协议

```python
@tool(
    name="search_web",
    domain="world",
    category="browser",
    input_schema=SearchInput,
    output_schema=SearchOutput,
    timeout=15,
    fallback_on_timeout=True,
    idempotent=True,
    local_only=False,
    risk_scanner="enabled"          # 执行后触发销售风险扫描
)
def search_web(query: str) -> dict:
    ...
```

### 5.2 原子工具全景表（当前 + 规划）

| 工具名 | 域 | 类别 | 输入 | 输出 | 超时 | 本地-only | 安全等级 | 状态 |
|-------|---|------|------|------|------|----------|---------|------|
| `read_file` | knowledge | io | `path` | 文本 | 2s | Yes | low | ✅ 已有 |
| `write_file` | knowledge | io | `path`, `content` | 确认 | 2s | Yes | medium | ✅ 已有 |
| `list_dir` | admin | io | `path` | 文件列表 | 2s | Yes | low | ✅ 已有 |
| `search_knowledge` | knowledge | retrieval | `query` | 语义结果 | 5s | Yes | medium | ✅ 已有 |
| `get_time` | admin | info | — | 时间字符串 | 1s | Yes | low | ✅ 已有 |
| `search_web` | world | browser | `query` | 摘要列表 | 15s | No | low | SearXNG（baidu/sogou/360search + bing news/google news/sogou wechat）；SearXNG 被封时自动 fallback 到 `browse_open` 提取百度搜索结果 |
| `fetch_webpage` | world | browser | `url` | 标题+正文 | 10s | No | low | ✅ 已有 |
| `jina_reader` | world | browser | `url` | Markdown | 10s | No | low | ✅ 已有 |
| `find_chrome_url` | world | browser | `keywords` | URL 列表 | 8s | No | low | ✅ 已有 |
| `browse_open` | world | browser | `url` | 标题+正文 | 10s | No | medium | ✅ 已有 |
| `browse_click` | world | browser | `selector` | 确认 | 5s | No | medium | ✅ 已有 |
| `browse_fill` | world | browser | `selector`, `text` | 确认 | 5s | No | medium | ✅ 已有 |
| `browse_screenshot` | world | browser | `filename` | 路径 | 8s | No | low | ✅ 已有 |
| `browse_scroll` | world | browser | `direction` | 确认 | 3s | No | low | ✅ 已有 |
| `browse_text` | world | browser | `selector` | 文本 | 5s | No | low | ✅ 已有 |
| `md_to_pdf` | content | io | `path` | PDF 路径 | 30s | Yes | low | ✅ 已有（fpdf2 + 系统字体） |
| `vector_search` | knowledge | retrieval | `query` | 文档片段 | 2s | Yes | high | ✅ 已有 |
| `session_search` | memory | memory | `keyword` | 对话片段 | 2s | Yes | high | 🆕 待建 |
| `query_analytics` | admin | analytics | `sql` / `params` | DuckDB 查询结果 | 3s | Yes | medium | ✅ 已有 |
| `get_user_history` | admin | analytics | `user_id`, `limit` | 用户对话历史 | 2s | Yes | medium | ✅ 已有 |
| `get_task_assets` | admin | analytics | `task_id` | 任务产物列表 | 2s | Yes | medium | ✅ 已有 |
| `search_past_executions` | admin | analytics | `filters` | 历史执行记录 | 3s | Yes | medium | ✅ 已有 |
| `refresh_analytics` | admin | analytics | — | 分析视图刷新 | 5s | Yes | low | ✅ 已有 |
| `get_tool_stats` | admin | analytics | `tool_name`, `days` | 工具调用统计 | 2s | Yes | low | ✅ 已有 |
| `save_learning` | admin | analytics | `category`, `pattern`, `action` | 学习记录 ID | 1s | Yes | low | ✅ 已有 |
| `get_learnings` | admin | analytics | `category`, `limit` | 学习记录列表 | 2s | Yes | low | ✅ 已有 |

### 5.3 标准输出信封

所有原子工具返回统一信封：

```json
{
  "status": "success",
  "tool": "search_web",
  "latency_ms": 420,
  "result": {
    "items": [...],
    "risk_signals": [
      {"type": "competitor_mention", "keyword": "竞品", "action": "note_only"},
      {"type": "pricing_pressure", "keyword": "预算不够", "action": "escalate"}
    ]
  },
  "audit": {
    "executed_by": "agent",
    "timestamp": "2026-07-10T12:00:00Z"
  },
  "fallback_used": false
}
```

---

## 六、复合工具（T2）编排规范

### 6.1 意图域路由（Domain Router）

```python
intent_domains = {
    "research":   ["研究", "查", "背景", "battlecard", "竞品", "行业", "公司", "客户"],
    "outreach":   ["写邮件", "跟进", "触达", "话术", "cold call", "微信", "邮件"],
    "content":    ["方案", "报告", "PPT", "文案", "文章", "总结", "整理"],
    "analytics":  ["分析", "统计", "数据", "复盘", "绩效", "转化率"],
    "risk":       ["风险", "预警", "流失", "投诉", "竞品签了"],
    "admin":      ["提醒", "日程", "记录", "几点", "安排"],
    "world":      ["天气", "搜索", "上网", "网页", "看看", "查一下"],
}
```

**跨域联动规则**：
- 主域为 `research` 时，自动加载 `risk_deps` 中的 `competitor_mentions` / `pricing_pressure` 作为**风险提示素材**。
- 主域为 `outreach` 时，自动加载 `memory_deps` 中的 `account.research_summary` 和 `contact.role_in_deal` 作为**个性化素材**。
- 任何域检测到 `risk_signals` 中的 `churn_risk` 或 `negative_feedback` 严重信号，立即**冻结当前域执行**，转交 `risk_escalation` 复合工具。

### 6.2 双轨状态机

```
INIT → DOMAIN_ROUTE → MEMORY_LOAD_DUAL → TOOL_EXEC →
       RISK_SCAN_ALWAYS → OUTPUT_RENDER → USER_FRIENDLY →
       DELIVER → ARCHIVE_DUAL
```

- `MEMORY_LOAD_DUAL`：同时加载 `memory_deps`（销售轨）与 `risk_deps`（风控轨）
- `RISK_SCAN_ALWAYS`：每步原子工具执行后，扫描 `risk_signals`
- `ARCHIVE_DUAL`：销售内容写入 `creation_workspace`，风险线索写入 `risk_logs` / 负责人通知队列

---

## 七、自适应工具（T3）沉淀机制

### 7.1 触发模式

| 触发模式 | 条件 | 示例 |
|---------|------|------|
| **高频重复** | 同一销售 3 天内 ≥3 次以相似意图触发同类原子工具组合 | 连续 3 天早上让 Agent 查行业新闻 |
| **长链固化** | 单次对话中 Agent 调用 ≥5 个工具完成一个任务 | 客户研究 = 查公司 → 查竞品 → 查高管 → 生成 battlecard → 保存 |
| **负责人显式沉淀** | 负责人在后台标记"把这次对话存为固定流程" | 每周一输出上周线索跟进汇总 |
| **风格收敛** | Agent 检测到销售连续 5 次接受同一种输出风格 | 销售开始习惯"三段式 battlecard"，自动更新模板 |

### 7.2 草稿生命周期

负责人审核时评估：
- 信息来源是否被正确引用？
- 是否错误固化了一个临时输出（如某次特殊折扣口径）？
- 是否涉及敏感客户数据或内部未公开信息？

---

## 八、安全与沙箱策略

### 8.1 权限矩阵

| 操作 | Agent 自动执行 | 需用户确认 | 需负责人确认 | 禁止 |
|------|--------------|-----------|-------------|------|
| 查天气、播报日程 | ✅ | — | — | — |
| **生成客户 battlecard** | ✅ | — | — | — |
| **生成跟进文案** | ✅ | — | — | — |
| **发送邮件/微信给客户** | — | ✅ | — | — |
| 读取客户/商机记录 | ✅ | — | — | — |
| 写入客户/商机记录 | — | ✅ | — | — |
| 删除客户/商机记录 | — | — | ✅ | — |
| **检测到严重流失风险/投诉** | — | — | — | **立即静默上报负责人** |
| **内容涉及客户未公开敏感信息** | — | — | ✅ | — |
| 网页抓取（通用） | ✅ | — | — | — |
| 网页抓取（客户内部系统） | — | — | ✅ | — |
| 生成 PDF 报告 | ✅ | — | — | — |

### 8.2 风控域安全

**风险预警（Risk Sentinel）**：
- 对话中出现"终止合作""换供应商""非常不满""要投诉" → 立即触发 `risk_escalation`
- Agent 不直接质问客户，而是**温和续接对话**，同时**静默通知负责人**：
  > "我记下了，这部分我们内部再对齐一下，稍后给您更准确的回复。"

**来源保护**：
- 禁止 Agent 在研究中编造来源，无来源时必须标注"未找到公开信息"。
- 禁止自动修改销售已确认的定稿（`status: done` 的 `creation_workspace` 记录为只读）。

---

## 九、用户友好输出层

### 9.1 来源引用注入规则

研究类工具在 `OUTPUT_RENDER` 阶段，必须执行 `SourceCitationInjector`：

```yaml
injection_rules:
  - 每条事实后标注来源（[来源：...]）
  - 若信息来自多个来源，列出主要 1-3 个
  - 禁止覆盖项：
      - 无来源的断言
      - 推测性表述不加"可能"
      - 超过 80 字的长句（自动拆分）
  - 强制适配项：
      - 销售场景术语（"客户"/"商机"/"next_step"）
      - 数据精确到可验证（财报年份、融资金额、时间）
      - 行动导向（结论后给出"下一步建议"）
```

### 9.2 语气梯度

| 场景 | 语气 | 示例 |
|------|------|------|
| 日常播报 | professional | "早上好，今天北京 26 度，记得带伞" |
| 客户风险 | calm | "快手这边提到预算紧张，建议下周重点讲 ROI" |
| 竞品动态 | alert | "小红书最近加大了 KOL 投放，快手可能也会关注" |
| **研究完成** | **confident** | **"这家公司的 battlecard 已整理好，来源都在文末"** |
| **用户表达困惑** | **patient** | **"没事，咱们把需求再拆细一点"** |
| 深夜/凌晨 | calm + short | "还没休息？需要我把明天要发的邮件先备好" |

---

## 十、L5 销售工作区接口规范

### 10.1 表结构

```sql
CREATE TABLE creation_workspace (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_name TEXT NOT NULL,           -- "快手 battlecard"
    project_type TEXT NOT NULL,           -- account_research | outreach_draft |
                                          -- content | proposal | report
    draft_content TEXT,                   -- Markdown 格式
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'drafting',       -- drafting | reviewing | done | archived
    style_preset JSONB,                   -- 输出风格快照
    source_material JSONB,                -- 素材清单：搜索关键词、来源 URL、客户数据
    risk_context JSONB,                   -- 创作时的隐性风险上下文
                                          -- 如 {"churn_risk": true, "competitor": "xxx"}
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    last_accessed_by_agent TIMESTAMP,
    completed_at TIMESTAMP,
    reviewed_by TEXT                      -- 销售负责人 ID
);
```

### 10.2 核心操作接口

| 操作 | 工具 | 说明 |
|------|------|------|
| **接着写** | `workspace_read` | 按 `project_name` 取最新 `status='drafting'` 记录 |
| **存草稿** | `workspace_write` | 覆盖当前版本，不递增版本号 |
| **定稿** | `workspace_write` | `status='reviewing'`，通知负责人审核，版本号 +1 |
| **发布** | 负责人后台操作 | `status='done'`，锁定只读 |
| **素材追加** | `workspace_write` | 更新 `source_material` |

---

## 十一、完整示例

### 示例 1：复合工具 —— `account_research_battlecard`

见 4.1 完整模板。

### 示例 2：复合工具 —— `outreach_drafter`

```yaml
---
name: outreach_drafter
version: 1.1.0
domain: outreach
type: compound
status: published
author: system
reviewed_by: "sales_lead"

triggers:
  - type: manual
    command: "写一封跟进邮件"
  - type: manual
    command: "微信跟进"

memory_deps:
  - domain: accounts
    query: "name ILIKE %company%"
    required: true
  - domain: contacts
    query: "name ILIKE %contact%"
    required: true
  - domain: activities
    query: "entity_id=%contact_id% LIMIT 3"
    required: false
  - domain: deals
    query: "account_id=%account_id%"
    required: false

risk_deps:
  - domain: pricing_pressure
    query: "account=%account_id%"
    required: false
  - domain: competitor_mentions
    query: "recent"
    required: false

tools_chain:
  - step: 1
    tool: search_web
    alias: recent_news
    params:
      query: "{{account.name}} {{contact.title}} 2026 动态"
      time_range: "week"
  - step: 2
    tool: local_llm
    alias: draft
    params:
      model: "qwen/qwen3.6-35b-a3b"
      prompt: "基于客户画像、联系人和最新动态，写一封个性化跟进邮件"
      account: "{{memory.accounts}}"
      contact: "{{memory.contacts}}"
      news: "{{steps.recent_news.result}}"
      style: "{{memory.user_profile.writing_patterns}}"
    timeout: 30
  - step: 3
    tool: send_wechat
    params:
      target: "{{user.id}}"
      content: "{{steps.draft.result}}\n\n您看这样发可以吗？说'好'我就存上，说'改改'您告诉我哪儿不对。"
    on_failure: retry_exponential

fallback:
  missing_required_memory: "我先确认一下这个联系人和客户信息，稍后给您写。"
  tool_failure: "今天检索有点慢，我先基于已有信息写一版，您看看。"

safety:
  auto_execute: true
  human_in_the_loop: false
  data_sensitivity: "medium"
  content_moderation: "sales_ready"
  extreme_risk_action: "escalate"

user_friendly:
  format: "email"
  max_chars_per_paragraph: 80
  tone: "professional"

source_workspace:
  enabled: true
  project_type: "outreach_draft"
  auto_save_draft: true
  resume_on_trigger: true
  version_on_complete: true
---

## 用途
根据客户最新动态和联系人角色，生成个性化跟进邮件/微信文案，并请求确认后存为新版本。

## 双轨沉淀
- 销售轨：文案内容写入 `creation_workspace`，版本 +1
- 风控轨：若客户近期提到预算或竞品，扫描 `risk_signals` 并静默通知负责人
```

### 示例 3：复合工具 —— `churn_risk_alert`

```yaml
---
name: churn_risk_alert
version: 1.0.0
domain: risk
type: compound
status: published
author: system

triggers:
  - type: event
    event: "risk_scanner.churn_risk_detected"

memory_deps:
  - domain: accounts
    query: "account_id=%account_id%"
    required: true
  - domain: deals
    query: "account_id=%account_id%"
    required: true

risk_deps:
  - domain: competitor_mentions
    query: "account=%account_id%"
    required: false

tools_chain:
  - step: 1
    tool: search_web
    alias: context
    params:
      query: "{{account.name}} 竞品 2026"
      time_range: "month"
  - step: 2
    tool: local_llm
    alias: analysis
    params:
      model: "qwen/qwen3.6-35b-a3b"
      prompt: "分析客户流失风险，判断是否需要立即干预"
      context: "{{steps.context.result}}"
  - step: 3
    tool: local_llm
    alias: msg_sales
    template: "churn_risk_gentle"
    inputs:
      risk: "{{steps.analysis.result.risk}}"
      suggestion: "{{steps.analysis.result.suggestion}}"
  - step: 4
    tool: send_wechat
    params:
      target: "{{user.id}}"
      content: "{{steps.msg_sales.result}}"
  - step: 5
    tool: local_llm
    alias: msg_manager
    template: "manager_brief"
    inputs:
      risk_type: "流失风险"
      risk: "{{steps.analysis.result.risk}}"
      deal_stage: "{{memory.deals.stage}}"
    when: "{{steps.analysis.result.severity}} != 'low'"
  - step: 6
    tool: send_wechat
    when: "{{steps.analysis.result.severity}} != 'low'"
    params:
      target: "manager"
      content: "{{steps.msg_manager.result}}"

fallback:
  tool_failure: "客户风险信号已收到，我会继续跟踪，稍后给您汇总。"

safety:
  auto_execute: true
  human_in_the_loop: false
  data_sensitivity: "high"
  allowed_channels: ["wechat", "web"]
  audit_level: "verbose"

user_friendly:
  format: "short_paragraph"
  max_chars_per_paragraph: 80
  tone: "calm"
---

## 用途
检测到客户流失风险时，生成应对建议并通知销售本人及负责人。
```

---

## 十二、版本与演进规则

| 版本段 | 含义 | 升级条件 |
|-------|------|---------|
| `x.0.0` | 重大架构变更 | 新增记忆域、双轨引擎重构 |
| `x.y.0` | 功能升级 | 新增原子工具、复合工具新增域、L5 工作区升级 |
| `x.y.z` | 补丁修复 | 话术模板调整、输出标准优化、安全规则微调 |

---

## 十三、与现有代码的衔接

| 现有模块 | 需要改什么 | 工作量 | 优先级 |
|---------|-----------|--------|--------|
| `user_profiles` 表 | 新增 `writing_patterns` JSONB；新增 `wechat_user_id TEXT` | 1 小时 | P0 |
| `creation_workspace` 表 | 补 `style_preset`, `source_material`, `risk_context` 等字段 | 1 小时 | P0 |
| `mind/tools.py` | 引入 `@tool` 装饰器，所有工具标注 domain/category，输出标准信封 | 已完成 |
| `mind/tool_result.py` | 工具注册表前置参数校验 | 已完成 |
| `mind/agent.py` | AgentSession 状态层 | 已完成 |
| `mind/agent_loop.py` | AgentLoop 运行时层 | 已完成 |
| `mind/agent_events.py` | 标准事件定义 | 已完成 |
| `mind/agent_message.py` | 应用层消息类型 | 已完成 |
| `mind/services.py` | 基础设施服务层 | 已完成 |
| `mind/agent_runner.py` | Agent 执行公共逻辑 | 已完成 |
| `cli.py` | CLI 模式入口 | 已完成 |
| `mind/interruption.py` | 真打断-恢复任务栈 | 已完成 |
| `mind/subagent.py` | 子 Agent 并行执行器 | 已完成 |
| `mind/memory.py` | 新增 `creation_workspace` 查询接口；`sales_risk_scanner` 钩子 | 已完成 |
| `skills/` 目录 | 销售域 skill 为主：research/outreach/content/analytics | 进行中 |
| `mind/knowledge.py` | 知识库支持行业/客户资料 | 已完成 |
| `mind/scheduler.py` | 销售简报/复盘仪式 | 已完成 |
| `mind/emotion_sensor.py` | 扩展为销售沟通情绪与风险扫描 | 已完成 |

---

## 十四、实施路线

### Phase 2.5.x —— 基础设施（已完成）
- [x] 升级 `user_profiles` 表，新增 `writing_patterns` JSONB
- [x] 补全 `creation_workspace` 表字段
- [x] 改造 `mind/tools.py`：引入 `@tool` 装饰器，标注 domain
- [x] 新增 `md_to_pdf` 原子工具
- [x] ToolRegistry 前置参数校验
- [x] 改造 `mind/emotion_sensor.py` → 销售风险扫描
- [x] 实现 Domain Router（关键词匹配）

### Phase 2.5.y —— 高频复合工具 PoC
- [x] `account_research_battlecard`（research 域）
- [x] `outreach_drafter`（outreach 域 + L5）
- [ ] `churn_risk_alert`（risk 域）
- [ ] 迁移现有 Markdown Skill 为 YAML + Markdown 格式

### Phase 2.5.z —— 完善
- [ ] `style_extract` 工具（自动提炼销售输出风格）
- [ ] 负责人通过 Web 直接修改输出风格模板
- [ ] 早间销售简报双轨升级
- [ ] Streamlit 后台：研究流面板、素材投喂、风格模板编辑器

---

*本文档作为 销销 B2B 销售工具体系契约。记忆做宽，技能做全，Agent 做灵。*
