# SalesMind 工具规范 v1.1（SalesMind 适配版）

> **文档性质**：架构契约与开发接口规范  
> **适用对象**：原子工具开发者、复合工具编排者、Skill 审核者（夫妻管理员）  
> **版本日期**：2026-05-16  
> **状态**：Phase 2.6c 基线版（Tool Calling + 前置校验 + md_to_pdf + 用户级串行锁 + Skill 拆分）  
> **与上游 v1.1 的关系**：完全吸收上游 v1.1 的架构设计（YAML Skill、声明式 tools_chain、@tool 装饰器、标准信封），仅在实施路线上分阶段落地。

---

## 一、设计哲学：双螺旋定位（与上游 v1.1 一致）

SalesMind 不是"帮老人管理健康"的工具，而是**"帮老人重新成为家庭叙事中心"的协作者**。健康看护是底线，创作表达是主线，两者共用同一套记忆体和技能树。

**Expression-First, Care-Always**：

```
        对外：创作放大器（高频、显性、骄傲）
        ┌─────────────────────────────────────┐
        │  creation │ knowledge │ expression  │
        │  social   │           │             │
        └─────────────────────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        │      家庭数字孪生记忆体      │
        │  （健康基线 + 兴趣主轴 + 关系网络 + 文风指纹） │
        └─────────────┬─────────────┘
                      │
        ┌─────────────────────────────────────┐
        │  care     │ admin     │ safety      │
        └─────────────────────────────────────┘
        对内：家庭助理（低频、隐性、安心）
```

**五条铁律**：

1. **Expression-First**：老人打开企微的第一动机是"帮我写/帮我整/帮我发"，Agent 必须优先响应创作意图。
2. **Care-Always**：健康看护不消失，而是**隐性基线**。任何创作交互都伴随照护扫描（如老人写自传提到"咳了三个月"，自动关联近期呼吸数据）。
3. **记忆驱动，双轨关联**：工具执行前必须声明 `memory_deps` 与 `care_deps`，创作轨加载兴趣/文风/项目，照护轨加载健康/安全/情绪。
4. **文风指纹是核心技术壁垒**：所有创作类输出必须经过 `writing_fingerprint` 翻译层，禁止生成千篇一律的 AI 味文案。
5. **透明可审计 + 夫妻共治**：老人可随时问"你都知道我些什么"；夫妻在后台既是观众（看创作流）也是治理者（审 Skill、修正文风、确认健康警报）。

---

## 二、三阶工具模型

| 阶次 | 名称 | 定义 | 生命周期 |
|-----|------|------|---------|
| T1 | **原子工具**（Atomic） | 单次调用、无状态、幂等、直接映射到底层能力 | 永久内置 |
| T2 | **复合工具**（Compound） | 多步编排、有状态、含分支与异常处理、封装家庭工作流 | 夫妻审核后发布 |
| T3 | **自适应工具**（Adaptive） | 从对话历史中自动提取的重复模式，经人工确认后转正 | 草稿 → 试用 7 天 → 转正/废弃 |

---

## 三、记忆体与技能树的双轨关系

```
输入信号
    │
    ├─→ 意图域路由（Domain Router）
    │       │
    │       ├─→ 创作轨：creation / knowledge / expression / social
    │       │              ↓
    │       │         加载记忆：writing_patterns, life_experiences,
    │       │                  creation_workspace, interests
    │       │              ↓
    │       │         生成个性化表达
    │       │
    │       └─→ 照护轨：care / admin
    │                      ↓
    │                 加载记忆：body_memory, schedule_memory, family_circle
    │                      ↓
    │                 生成管理/提醒/警报
    │
    └─→ 隐性基线扫描（Always-On Care Scanner）
               ↓
          任何域的对话都扫描：
          • 健康信号（血压/用药/症状词）
          • 安全信号（诈骗/极端内容/遗嘱/想死）
          • 情绪信号（低落/兴奋/孤独）
               ↓
          触发静默通知夫妻 or 升级响应策略
```

---

## 四、Skill Manifest 规范

所有工具必须以单一文件声明，路径为 `skills/{domain}/{tool_name}/skill.yaml`。

### 4.1 完整模板

```yaml
---
# ========== 元数据区 ==========
name: photo_caption_with_story
version: 1.0.0
domain: creation                    # 必填：creation / knowledge / expression / social / care / admin / world
type: compound                      # atomic | compound | adaptive
status: published                   # draft | trial | published | deprecated
author: system
created_at: "2026-04-30"
reviewed_by: "peter"

# ========== 触发器区 ==========
triggers:
  - type: manual
    command: "给这张照片配段文案"
  - type: manual
    command: "写朋友圈"
  - type: event
    event: "message.contains_image"
    condition: "sender.role == elderly and intent_domain == creation"

# ========== 记忆依赖区（双轨）==========
memory_deps:
  # 创作轨
  - domain: creation_workspace
    query: "recent_projects.type=photo"
    required: false
  - domain: user_profile
    query: "writing_patterns"
    required: true
  - domain: user_profile
    query: "life_experiences.nature_photography"
    required: false
  - domain: user_profile
    query: "interests"
    required: false
  # 照护轨（隐性基线）
  - domain: body_memory
    query: "today.steps"
    required: false

care_deps:                          # 照护扫描专用
  - domain: body_memory
    query: "recent_mood_indicators"
    required: false
  - domain: safety_db
    query: "extreme_content_flags"
    required: false

# ========== 工具链区 ==========
tools_chain:
  - step: 1
    tool: image_ocr
    alias: photo_meta
    params:
      image_path: "{{event.image_path}}"
      extract_exif: true
    output_mode: structured
  - step: 2
    tool: photo_analyze
    alias: scene
    params:
      image_path: "{{event.image_path}}"
      detect_objects: true
      detect_mood: true                # 识别照片氛围（用于文风适配）
  - step: 3
    tool: generate_elderly_text
    alias: caption
    template: "photo_story"
    writing_fingerprint: "{{memory.user_profile.writing_patterns}}"  # 文风注入
    inputs:
      photo_objects: "{{steps.scene.result.objects}}"
      photo_mood: "{{steps.scene.result.mood}}"
      exif_date: "{{steps.photo_meta.result.exif_date}}"
      related_memory: "{{memory.user_profile.life_experiences.nature_photography}}"
      steps_today: "{{memory.body_memory.today.steps}}"              # 照护基线融入
  - step: 4
    tool: send_wechat
    params:
      target: "{{event.sender_id}}"
      content: "{{steps.caption.result}}"
    on_failure: retry_exponential

# ========== 异常与回退区 ==========
fallback:
  missing_required_memory: "我先看看照片，一会儿给您写段话。"
  tool_failure: "照片我看了，但文案还没想好，您先忙别的，我 2 分钟后再发给您。"
  no_response_30min: null             # 创作类不追问，避免打扰

# ========== 安全与权限区 ==========
safety:
  auto_execute: true
  human_in_the_loop: false
  data_sensitivity: "medium"
  allowed_channels: ["wechat"]
  audit_level: "verbose"
  content_moderation: "elderly_safe"  # elderly_safe | standard | strict
  extreme_content_action: "escalate"   # 检测到极端/遗嘱内容时上报夫妻

# ========== 老人友好层 ==========
elderly_friendly:
  format: "short_paragraph"
  max_chars_per_paragraph: 30
  tone: "warm"
  tone_variant: "celebratory"         # 创作完成时带成就感
  include_emoji: false
  medical_disclaimer: false

# ========== 创作空间关联 ==========
source_workspace:
  enabled: true
  project_type: "photo_caption"
  auto_save_draft: true               # 是否自动保存到 creation_workspace
  version_on_complete: true           # 老人确认发送后存为新版本
---

## Markdown 正文区

### 用途
帮老人的照片生成带个人记忆和文风的朋友圈/家庭群文案，同时隐性融入健康正能量（如"今天走了 6000 步，精神头不错"）。

### 典型交互
老人发送公园花卉照片 → Agent 识别花名 → 关联老人过往养花经历 → 套用文风指纹 → 生成文案 → 询问"这样发可以吗？" → 老人确认后发送并归档。

### 夫妻审核备注
> 该工具已沉淀老人"爱用省略号、喜用自然比喻"的文风特征，试用期内无异常。
```

### 4.2 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | Skill 标识，不含空格，唯一 |
| `domain` | 是 | 七大域之一：`creation` `knowledge` `expression` `social` `care` `admin` `world` |
| `type` | 是 | `atomic` `compound` `adaptive` |
| `status` | 是 | `draft` `trial` `published` `deprecated` |
| `triggers` | 是 | 触发条件列表 |
| `memory_deps` | 否 | 创作轨记忆依赖 |
| `care_deps` | 否 | 照护轨记忆依赖 |
| `tools_chain` | compound 必填 | 原子工具编排顺序 |
| `fallback` | 否 | 异常回退话术 |
| `safety` | 否 | 安全与权限配置 |
| `elderly_friendly` | 否 | 老人友好输出配置 |
| `source_workspace` | 否 | 创作空间关联 |

---

## 五、原子工具（T1）接口规范

### 5.1 注册协议

```python
@tool(
    name="photo_analyze",
    domain="creation",              # 必填
    category="media",
    input_schema=PhotoInput,
    output_schema=PhotoOutput,
    timeout=8,
    fallback_on_timeout=True,
    idempotent=True,
    local_only=True,                # 本地执行，隐私
    care_scanner="enabled"          # 执行后触发照护扫描
)
def photo_analyze(image_path: str) -> dict:
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
| `fetch_webpage` | world | browser | `url` | 标题+正文 | 10s | No | low | 🆕 web-access |
| `jina_reader` | world | browser | `url` | Markdown | 10s | No | low | 🆕 web-access |
| `find_chrome_url` | world | browser | `keywords` | URL 列表 | 8s | No | low | 🆕 web-access |
| `browse_open` | world | browser | `url` | 标题+正文 | 10s | No | medium | ✅ 已有 |
| `browse_click` | world | browser | `selector` | 确认 | 5s | No | medium | ✅ 已有 |
| `browse_fill` | world | browser | `selector`, `text` | 确认 | 5s | No | medium | ✅ 已有 |
| `browse_screenshot` | world | browser | `filename` | 路径 | 8s | No | low | ✅ 已有 |
| `browse_scroll` | world | browser | `direction` | 确认 | 3s | No | low | ✅ 已有 |
| `browse_text` | world | browser | `selector` | 文本 | 5s | No | low | ✅ 已有 |
| `md_to_pdf` | creation | io | `path` | PDF 路径 | 30s | Yes | low | ✅ 已有（fpdf2 + 系统字体） |
| `photo_analyze` | creation | media | `image_path` | 物体/氛围 | 8s | Yes | low | 🆕 待建 |
| `image_ocr` | creation | media | `image_path` | 文本 | 10s | Yes | medium | 🆕 待建 |
| `voice_transcribe` | creation | media | `audio_path` | 文本+情绪 | 10s | Yes | medium | 🆕 待建 |
| `tts_generate` | creation | media | `text`, `voice` | MP3 路径 | 8s | Yes | low | 🆕 待建 |
| `workspace_read` | creation | memory | `project_name` | 草稿内容 | 1s | Yes | high | 🆕 待建 |
| `workspace_write` | creation | memory | `project_name`, `content` | 版本号 | 2s | Yes | high | 🆕 待建 |
| `style_extract` | creation | memory | `text_samples[]` | 文风指纹 JSON | 5s | Yes | high | 🆕 待建 |
| `fetch_weather` | world | info | `location` | 结构化天气 | 5s | No | low | 🆕 待建 |
| `parse_pdf` | knowledge | knowledge | `file_path` | 文本+表格 | 15s | Yes | high | 🆕 待建 |
| `local_llm` | brain | brain | `prompt`, `model` | 文本 | 30s | Yes | critical | 🆕 待建 |
| `embed_text` | brain | brain | `text` | 向量 512d | 3s | Yes | high | 🆕 待建 |
| `vector_search` | brain | brain | `query_vec` | 文档片段 | 2s | Yes | high | ✅ 已有（knowledge.py） |
| `session_search` | memory | memory | `keyword` | 对话片段 | 2s | Yes | high | 🆕 待建 |
| `query_analytics` | admin | analytics | `sql` / `params` | DuckDB 查询结果 | 3s | Yes | medium | 🆕 analytics |
| `get_user_history` | admin | analytics | `user_id`, `limit` | 用户对话历史 | 2s | Yes | medium | 🆕 analytics |
| `get_task_assets` | admin | analytics | `task_id` | 任务产物列表 | 2s | Yes | medium | 🆕 analytics |
| `search_past_executions` | admin | analytics | `filters` | 历史执行记录 | 3s | Yes | medium | 🆕 analytics |
| `refresh_analytics` | admin | analytics | — | 分析视图刷新 | 5s | Yes | low | 🆕 analytics |
| `get_tool_stats` | admin | analytics | `tool_name`, `days` | 工具调用统计 | 2s | Yes | low | 🆕 analytics |
| `save_learning` | admin | analytics | `category`, `pattern`, `action` | 学习记录 ID | 1s | Yes | low | 🆕 analytics |
| `get_learnings` | admin | analytics | `category`, `limit` | 学习记录列表 | 2s | Yes | low | 🆕 analytics |

### 5.3 标准输出信封

所有原子工具返回统一信封：

```json
{
  "status": "success",
  "tool": "voice_transcribe",
  "latency_ms": 420,
  "result": {
    "text": "今天去了公园，花开得真好...",
    "emotion_tag": "positive",
    "care_signals": [
      {"type": "mood", "level": "positive", "confidence": 0.92},
      {"type": "health_mention", "keyword": "膝盖疼", "action": "note_only"}
    ]
  },
  "audit": {
    "executed_by": "agent",
    "timestamp": "2026-04-30T12:00:00Z"
  },
  "fallback_used": false
}
```

---

## 六、复合工具（T2）编排规范

### 6.1 意图域路由（Domain Router）

```python
intent_domains = {
    "creation":   ["写", "配", "整", "做", "照片", "故事", "朋友圈", "文案", "绘本", "自传", "配文"],
    "knowledge":  ["查", "解读", "什么意思", "怎么看", "攻略", "新闻", "资料"],
    "expression": ["方案", "信", "发言", "致辞", "意见", "建议", "稿子"],
    "social":     ["祝福", "问候", "群里", "孙子", "儿子", "家庭", "亲戚"],
    "care":       ["血压", "药", "不舒服", "头晕", "挂号", "体检"],
    "admin":      ["提醒", "日程", "下周", "什么时候", "记录", "几点"],
    "world":      ["天气", "搜索", "上网", "网页", "看看", "查一下"],
}
```

**跨域联动规则**：
- 主域为 `creation` 时，自动加载 `care_deps` 中的 `body_memory.recent` 作为**正能量素材**（如步数、好心情）。
- 主域为 `care` 时，自动加载 `memory_deps` 中的 `user_profile.communication_style` 作为**话术适配素材**。
- 任何域检测到 `care_signals` 中的 `extreme_content`（遗嘱、想死、重度悲观），立即**冻结当前域执行**，转交 `safety_escalation` 复合工具。

### 6.2 双轨状态机

```
INIT → DOMAIN_ROUTE → MEMORY_LOAD_DUAL → TOOL_EXEC →
       CARE_SCAN_ALWAYS → OUTPUT_RENDER → ELDERLY_FRIENDLY →
       DELIVER → ARCHIVE_DUAL
```

- `MEMORY_LOAD_DUAL`：同时加载 `memory_deps`（创作轨）与 `care_deps`（照护轨）
- `CARE_SCAN_ALWAYS`：每步原子工具执行后，扫描 `care_signals`
- `ARCHIVE_DUAL`：创作内容写入 `creation_workspace`，照护线索写入 `body_memory` / 夫妻通知队列

---

## 七、自适应工具（T3）沉淀机制

### 7.1 触发模式

| 触发模式 | 条件 | 示例 |
|---------|------|------|
| **高频重复** | 同一用户 3 天内 ≥3 次以相似意图触发同类原子工具组合 | 连续 3 天早上让 Agent 配照片文案 |
| **长链固化** | 单次对话中 Agent 调用 ≥5 个工具完成一个任务 | 写自传 = 查经历 → 查文风 → 生成 → 润色 → 保存 |
| **夫妻显式沉淀** | 夫妻在后台标记"把这次对话存为固定流程" | 每月 1 号健康汇总 |
| **文风收敛** | Agent 检测到老人连续 5 次接受同一种文风变体 | 老人开始爱用"花开得像小时候"这类表达，自动更新指纹 |

### 7.2 草稿生命周期

夫妻审核时评估：
- 文风指纹是否被正确提取？
- 是否错误固化了一个临时创作（如老人某次开玩笑的口吻）？
- 是否涉及敏感话题（家庭矛盾、财产分配）？

---

## 八、安全与沙箱策略

### 8.1 权限矩阵

| 操作 | Agent 自动执行 | 需老人确认 | 需夫妻确认 | 禁止 |
|------|--------------|-----------|-----------|------|
| 查天气、播报日程 | ✅ | — | — | — |
| **生成朋友圈文案** | ✅ | — | — | — |
| **生成自传/绘本内容** | ✅ | — | — | — |
| **发送朋友圈/家庭群** | — | ✅ | — | — |
| 读取健康记录 | ✅ | — | — | — |
| 写入健康记录 | — | ✅ | — | — |
| 修改用药剂量 | — | — | ✅ | — |
| **检测到极端内容（遗嘱/想死）** | — | — | — | **立即静默上报夫妻** |
| **创作内容涉及家庭矛盾/财产** | — | — | ✅ | — |
| 网页抓取（通用） | ✅ | — | — | — |
| 网页抓取（医疗/金融） | — | — | ✅ | — |
| 生成语音 | ✅ | — | — | — |
| **模仿老人文风生成内容** | ✅ | — | — | — |

### 8.2 创作域安全

**情绪预警（Emotional Sentinel）**：
- 老人在创作中提及"遗嘱""想死""没意思""活够了" → 立即触发 `safety_escalation`
- Agent 不直接质问老人，而是**温和续接创作**，同时**静默通知夫妻**：
  > "这段写得很有感触，咱们先存着，您慢慢整理。我先不打扰您了。"

**极端内容拦截**：
- 老人要求 Agent 写"控诉子女"的内容 → Agent 不拒绝，但**改写为"建议信"口吻**，并通知夫妻：
  > "我帮您理了理想法，写成了一封建议信，您看看语气合适吗？"

**文风保护**：
- 禁止 Agent 在创作中引入网络梗、英文、流行语，除非 `writing_patterns` 中明确允许。
- 禁止自动修改老人已确认的定稿（`status: done` 的 `creation_workspace` 记录为只读）。

---

## 九、老人友好输出层

### 9.1 文风指纹注入规则

创作类工具在 `OUTPUT_RENDER` 阶段，必须执行 `WritingFingerprintInjector`：

```yaml
injection_rules:
  - 加载 `user_profile.writing_patterns`
  - 若老人本次对话有新增表达（如用了新比喻），先更新指纹草稿（待夫妻审核）
  - 禁止覆盖项：
      - 网络梗
      - 英文缩写
      - 感叹号（除非指纹允许）
      - 超过 30 字的长句（自动拆分）
  - 强制适配项：
      - 称谓代入（"您" / 老人自称习惯）
      - 时间感知（"早上" / "昨儿" / 指纹偏好）
      - 情感浓度（指纹中的 "含蓄/热烈/幽默"）
```

### 9.2 语气梯度

| 场景 | 语气 | 示例 |
|------|------|------|
| 日常播报 | warm | "早上好呀，今天 26 度" |
| 健康异常 | calm | "血压稍微高了一点，先坐会儿" |
| 诈骗拦截 | alert | "这个链接看着不太对，您先别点" |
| **创作完成** | **celebratory** | **"这段写得好，我帮您存着了，随时接着写！"** |
| **老人表达困惑** | **patient** | **"没事，咱们慢慢来，想到哪儿说到哪儿"** |
| 深夜/凌晨 | calm + short | "还没睡？需要我帮您叫儿子吗？" |

---

## 十、L5 创作空间接口规范

### 10.1 表结构

```sql
CREATE TABLE creation_workspace (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    project_name TEXT NOT NULL,           -- "人生自传"
    project_type TEXT NOT NULL,           -- autobiography | photo_caption | story
                                          -- | social_post | proposal | speech
    draft_content TEXT,                   -- Markdown 格式
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'drafting',       -- drafting | reviewing | done | archived
    style_preset JSONB,                   -- 创作时的文风指纹快照
    source_material JSONB,                -- 素材清单：照片路径、语音转写、文档链接
    care_context JSONB,                   -- 创作时的隐性照护上下文
                                          -- 如 {"steps": 6000, "mood": "positive"}
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    last_accessed_by_agent TIMESTAMP,
    completed_at TIMESTAMP,
    reviewed_by TEXT                      -- 夫妻管理员 ID
);
```

### 10.2 核心操作接口

| 操作 | 工具 | 说明 |
|------|------|------|
| **接着写** | `workspace_read` | 按 `project_name` 取最新 `status='drafting'` 记录 |
| **存草稿** | `workspace_write` | 覆盖当前版本，不递增版本号 |
| **定稿** | `workspace_write` | `status='reviewing'`，通知夫妻审核，版本号 +1 |
| **发布** | 夫妻后台操作 | `status='done'`，锁定只读 |
| **素材追加** | `workspace_write` | 更新 `source_material` |

---

## 十一、完整示例

### 示例 1：复合工具 —— `photo_caption_with_story`

见 4.1 完整模板。

### 示例 2：复合工具 —— `autobiography_continue`

```yaml
---
name: autobiography_continue
version: 1.1.0
domain: creation
type: compound
status: published
author: system
reviewed_by: "peter"

triggers:
  - type: manual
    command: "接着写自传"
  - type: manual
    command: "继续上次写的"

memory_deps:
  - domain: creation_workspace
    query: "project_name='人生自传' AND status='drafting'"
    required: true
  - domain: user_profile
    query: "writing_patterns"
    required: true
  - domain: user_profile
    query: "life_experiences"
    required: false
  - domain: body_memory
    query: "today.energy_level"
    required: false

care_deps:
  - domain: body_memory
    query: "recent_mood_indicators"
    required: false
  - domain: safety_db
    query: "extreme_content_flags"
    required: false

tools_chain:
  - step: 1
    tool: workspace_read
    alias: draft
    params:
      project_name: "人生自传"
  - step: 2
    tool: local_llm
    alias: continuation
    params:
      model: "gemma-4-26b-a4b-it-ud"
      prompt: "根据以下人生经历和文风指纹，续写自传。上次写到：{{steps.draft.result.content}}"
      context: "{{memory.user_profile.life_experiences}}"
      style: "{{memory.user_profile.writing_patterns}}"
    timeout: 30
  - step: 3
    tool: generate_elderly_text
    alias: preview
    template: "autobiography_preview"
    inputs:
      new_content: "{{steps.continuation.result}}"
      last_paragraph: "{{steps.draft.result.last_paragraph}}"
  - step: 4
    tool: send_wechat
    params:
      target: "{{user.id}}"
      content: "{{steps.preview.result}}\n\n您看这样接着写行吗？说'好'我就存上，说'改改'您告诉我哪儿不对。"
    on_failure: retry_exponential

fallback:
  missing_required_memory: "自传草稿我找找...找到了，上次写到 1985 年。咱们继续？"
  tool_failure: "今天脑子有点慢，您先歇会儿，我整理好再发给您。"

safety:
  auto_execute: true
  human_in_the_loop: false
  data_sensitivity: "high"
  content_moderation: "elderly_safe"
  extreme_content_action: "escalate"

elderly_friendly:
  format: "story"
  max_chars_per_paragraph: 40
  tone: "warm"
  tone_variant: "celebratory"

source_workspace:
  enabled: true
  project_type: "autobiography"
  auto_save_draft: true
  resume_on_trigger: true
  version_on_complete: true
---

## 用途
恢复老人上次未完成的自传项目，按文风指纹续写，并请求确认后存为新版本。

## 双轨沉淀
- 创作轨：续写内容写入 `creation_workspace`，版本 +1
- 照护轨：若老人在续写中提及健康问题（如"那年咳了三个月"），扫描 `care_signals` 并静默通知夫妻
```

### 示例 3：复合工具 —— `health_alert_with_context`

```yaml
---
name: health_alert_with_context
version: 1.2.0
domain: care
type: compound
status: published
author: system

triggers:
  - type: event
    event: "body_memory.anomaly_detected"

memory_deps:
  - domain: body_memory
    query: "last_7_days.blood_pressure"
    required: true
  - domain: user_profile
    query: "communication_style"
    required: true
  - domain: user_profile
    query: "family_circle"
    required: false

care_deps:
  - domain: body_memory
    query: "emergency_contacts"
    required: true

tools_chain:
  - step: 1
    tool: read_record
    alias: bp_trend
    params:
      metric: "blood_pressure"
      date_range: "last_7_days"
  - step: 2
    tool: local_llm
    alias: analysis
    params:
      model: "gemma-4-26b-a4b-it-ud"
      prompt: "分析血压趋势，判断是否需要立即干预"
      context: "{{steps.bp_trend.result}}"
  - step: 3
    tool: generate_elderly_text
    alias: msg_elderly
    template: "health_alert_gentle"
    inputs:
      trend: "{{steps.analysis.result.trend}}"
      suggestion: "{{steps.analysis.result.suggestion}}"
      style: "{{memory.user_profile.communication_style}}"
  - step: 4
    tool: send_wechat
    params:
      target: "{{user.id}}"
      content: "{{steps.msg_elderly.result}}"
  - step: 5
    tool: generate_elderly_text
    alias: msg_couple
    template: "couple_brief"
    inputs:
      alert_type: "血压异常"
      trend: "{{steps.analysis.result.trend}}"
      elderly_mood: "待观察"
    when: "{{steps.analysis.result.severity}} != 'low'"
  - step: 6
    tool: send_wechat
    when: "{{steps.analysis.result.severity}} != 'low'"
    params:
      target: "couple"
      content: "{{steps.msg_couple.result}}"

fallback:
  tool_failure: "血压数据有点乱，您现在方便再量一次吗？我等着。"

safety:
  auto_execute: true
  human_in_the_loop: false
  data_sensitivity: "critical"
  allowed_channels: ["wechat"]
  audit_level: "verbose"

elderly_friendly:
  format: "short_paragraph"
  max_chars_per_paragraph: 25
  tone: "calm"
  medical_disclaimer: true
---

## 用途
检测到血压异常时，用老人熟悉的口吻温和提醒，同时按严重程度通知夫妻。

## 创作式安抚设计
即使走 care 域，输出也经过 `communication_style` 适配：
- 故事型老人："这几天血压像过山车，咱们稳一稳，先坐下歇会儿"
- 干货型老人："血压 145/92，偏高，建议：① 坐下休息 ② 30 分钟后复测 ③ 若持续升高告知儿子"
```

---

## 十二、版本与演进规则

| 版本段 | 含义 | 升级条件 |
|-------|------|---------|
| `x.0.0` | 重大架构变更 | 新增记忆域（如 L6）、双轨引擎重构 |
| `x.y.0` | 功能升级 | 新增原子工具、复合工具新增域、L5 创作空间升级 |
| `x.y.z` | 补丁修复 | 文风模板调整、话术优化、安全规则微调 |

---

## 十三、与现有代码的衔接

| 现有模块 | 需要改什么 | 工作量 | 优先级 |
|---------|-----------|--------|--------|
| `user_profiles` 表 | 新增 `writing_patterns` JSONB；新增 `wechat_user_id TEXT`（企微成员账号，解耦内部 family_id） | 1 小时 | P0 |
| `creation_workspace` 表 | 补 `style_preset`, `source_material`, `care_context` 等字段 | 1 小时 | P0 |
| `mind/tools.py` | 引入 `@tool` 装饰器，所有工具标注 domain/category，输出标准信封；新增 `md_to_pdf` | 已完成 |
| `mind/tool_result.py` | 工具注册表前置参数校验（required 字段检查） | 已完成 |
| `mind/agent.py` | AgentSession 状态层（编排 + 快速路由 + checkpoint 恢复） | 已完成 |
| `mind/agent_loop.py` | AgentLoop 运行时层（Tool Calling while 循环 + 事件驱动 + 并行执行） | 已完成 |
| `mind/agent_events.py` | 标准事件定义（AgentEvent / AgentEventType） | 已完成 |
| `mind/agent_message.py` | 应用层消息类型（AgentMessage，LLM 边界 to_llm() 转换） | 已完成 |
| `mind/services.py` | 基础设施服务层（AgentServices，全局复用） | 已完成 |
| `mind/agent_runner.py` | Agent 执行公共逻辑（CLI 和 Web 共用同一套入口） | 已完成 |
| `cli.py` | CLI 模式入口（终端直接对话 / Kimi coding plan 集成） | 已完成 |
| `mind/interruption.py` | 真打断-恢复任务栈（LIFO，支持嵌套） | 已完成 |
| `mind/subagent.py` | 子 Agent 并行执行器（delegate 工具） | 已完成 |
| `mind/memory.py` | 新增 `creation_workspace` 查询接口；`care_scanner` 钩子 | 1 天 | P0 |
| `skills/` 目录 | 新建 `creation/` `knowledge/` `expression/` `social/` 子目录；迁移现有 Skill 为 YAML + Markdown 混合格式 | 2 天 | P1 |
| `mind/knowledge.py` | 新增人生经历库、文风指纹库 | 1 天 | P1 |
| `mind/scheduler.py` | 早安仪式从纯 care 升级为 creation + care 双轨 | 半天 | P1 |
| `mind/emotion_sensor.py` | 扩展为 `care_scanner.py`，增加健康/安全维度，输出 `care_signals` | 1 天 | P0 |

---

## 十四、实施路线

### Phase 2.5.x —— 基础设施（已完成）
- [x] 升级 `user_profiles` 表，新增 `writing_patterns` JSONB
- [x] 补全 `creation_workspace` 表字段
- [x] 改造 `mind/tools.py`：引入 `@tool` 装饰器，标注 domain，输出标准信封
- [x] 新增 `md_to_pdf` 原子工具（fpdf2 + 系统字体自动检测）
- [x] ToolRegistry 前置参数校验（required 字段检查）
- [ ] 改造 `mind/emotion_sensor.py` → `care_scanner.py`
- [x] 实现 Domain Router（关键词匹配）

### Phase 2.5.y —— 高频复合工具 PoC
- [ ] `photo_caption_with_story`（创作域）
- [ ] `autobiography_continue`（创作域 + L5）
- [ ] `health_alert_with_context`（照护域，但带文风适配）
- [ ] 迁移现有 Markdown Skill 为 YAML + Markdown 格式

### Phase 2.5.z —— 完善
- [ ] `style_extract` 工具（自动提炼文风指纹）
- [ ] 夫妻通过企微直接修改文风指纹
- [ ] 早安仪式双轨升级
- [ ] Phase 4 Streamlit 后台：创作流面板、素材投喂、文风指纹编辑器

---

*本文档作为 SalesMind 双螺旋架构的工具体系契约。记忆做宽，技能做全，Agent 做灵。*
