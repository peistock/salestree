---
name: 销销 架构纲领
description: B2B 销售 AI 助手「销销」的项目上下文、架构哲学与开发指南（已从 FamilyMind 家庭场景 pivot）
type: project
---

> **⚠️ 项目转向说明**：本项目已从 FamilyMind（家庭数字孪生）彻底 pivot 为 **销销 — B2B 销售智能协作空间**，服务对象为互联网广告/营销公司的销售团队。以下文档中保留的家庭/老人相关内容均为历史归档，当前实现、数据库表、系统提示词和技能库以销售场景为准。新增开发请以销销的销售助手定位为准。

# 销销 — B2B 销售智能协作空间

## 一、定位：我们不是在做"通用 ChatGPT"

销销的核心定位是：

> **客户与商机状态的实时镜像（Digital Twin of Sales Pipeline）**
- **对夫妻**：一个外挂大脑和分身——替他们盯着爸妈的状态，替他们记住家里那些"只有亲人才知道"的琐碎。

这个定位决定了：**核心不是功能列表，而是"家庭状态的实时镜像"。**

---

## 二、核心假设（架构基石）

| 假设 | 具体表述 | 若假设不成立的影响 |
|------|---------|------------------|
| **单家庭** | 系统只服务一个家庭，不考虑多租户 | 若未来商业化，需重写数据层和接入层 |
| **企微渠道** | 家庭成员使用企业微信 | 若老人拒绝安装企微，接入层作废。gewechat（非官方个人微信协议）已评估后放弃；个人订阅号无客服消息权限，无法替代 |
| **Mac 服务器** | 服务运行在爸爸的 Mac 上，7×24 小时开机 | Mac 合盖/断电/网络波动会导致服务中断 |
| **本地 LLM 为主** | 核心智能依赖 **LM Studio 本地 qwen/qwen3.6-35b-a3b**（端口 1234）；DeepSeek v4-flash / 百炼 qwen3.6-plus 作为 API 备选（`.env` 中切换 BASE_URL 即可） | 本地 35B 模型日常任务稳定，复杂任务（万字报告）输出质量可达 ~3500 字；API 费用 ¥0/月；不受网络波动影响；thinking mode 不可用 |
| **Docker PostgreSQL** | PG 容器通过端口映射暴露 localhost:5432 | 本地其他 PostgreSQL 实例（如 pg0）可能抢占 5432 端口，导致 FamilyMind 连接到错误数据库并报密码认证失败。启动前必须检查 `lsof -i :5432` |
| **本地隐私优先** | 敏感数据（病历）本地处理；上下文压缩走本地 Gemma 4 26B | 本地模型能力持续升级（qwen3.5-35b-a3b 已可胜任主模型），医疗相关回答质量可接受 |
| **夫妻录入** | 用药/日程等数据由夫妻手动录入，非老人主动维护 | 数据更新摩擦高，长期可能因懒惰而数据过时 |

---

## 三、三足鼎立架构

```
┌─────────────────────────────────────────────┐
│              陪伴面（Companion）              │
│         企微/微信 → 语音+文字+静默感知        │
│         时间在场 / 空间在场 / 情感在场         │
└──────────────────┬──────────────────────────┘
                   │
    ┌──────────────┴──────────────┐
    ▼                             ▼
┌─────────────┐           ┌─────────────┐
│   技能树     │◄─────────►│   记忆体     │
│ Skill Tree  │  记忆驱动   │ Memory Body │
│             │   技能沉淀  │             │
│ • 原子技能   │           │ • 瞬时记忆   │
│ • 复合技能   │           │ • 日程记忆   │
│ • 自适应技能 │           │ • 身体记忆   │
│             │           │ • 家庭记忆   │
│ 触发器      │           │ • 世界记忆   │
│ 执行器      │           │             │
│ 沉淀器      │           │ 关联引擎     │
└──────┬──────┘           └──────┬──────┘
       │                         │
       └──────────┬──────────────┘
                  ▼
       ┌─────────────────────┐
       │    外挂大脑            │
       │（夫妻协作界面）        │
       │ 课前 briefing        │
       │ 课后 debriefing      │
       │ 紧急 override        │
       └─────────────────────┘
```

三者的关系：**记忆体是土壤，技能树是果实，陪伴面是阳光。**

---

## 四、记忆体（Memory Body）

### 4.1 五类记忆（按"谁需要记住什么"组织）

| 记忆类型 | 定义 | 对老人的价值 | 对夫妻的价值 |
|---------|------|------------|------------|
| **瞬时记忆** | 当前对话线程（20轮） | "你刚才说到哪了？" | — |
| **日程记忆** | 今天/本周/本月该发生的事 | "今天该吃药了" | "爸下周三复查" |
| **身体记忆** | 血压、血糖、用药反应、过敏史 | "我血压最近怎么样？" | 长期健康趋势 |
| **家庭记忆** | 人际关系、偏好、禁忌、故事 | "我不吃辣""上次复查因为什么" | 家庭关系的润滑剂 |
| **世界记忆** | 外部信息（天气、新闻、谣言库、医院排班） | "这篇文章是假的" | 信息过滤层 |

### 4.2 关键升级：记忆不是"存储"，而是"关联"

当前代码是被动检索（老人问 → 查向量库 → 答）。灵性来自**主动关联**。

**平庸 vs 灵性的对比：**

> 老人说："今天有点头晕"

**平庸 Agent**："注意休息，多喝水。"

**灵性 Agent**："我记着您昨晚血压是 145/92，比平时高；今天早晨的药还没吃；外面今天 32 度有点闷。可能是这几个原因叠加了。您先坐下歇会儿，把早上的药吃了，我 1 小时后再问您感觉怎么样。"

**实现要求**：当输入一个信号（头晕），关联引擎自动激活相关记忆节点（昨晚血压、今日用药、今日天气、历史类似情况），然后由技能树决定调用哪个复合技能。

### 4.3 关联引擎（待实现）

关联引擎是 Phase 2.x 的核心新增模块。规则示例：

```python
# mind/association_engine.py 概念
ASSOCIATION_RULES = {
    "头晕": ["body_memory.blood_pressure", "schedule_memory.medication", "world_memory.weather"],
    "睡不着": ["body_memory.sleep_history", "schedule_memory.medication_time", "family_memory.stress_events"],
    "转发链接": ["world_memory.rumor_db", "skill.scam_guard"],
    "没吃药": ["schedule_memory.medication", "family_memory.caregiver_notes", "skill.health_alert"],
}
```

### 4.4 透明记忆原则

老人对 Agent"知道太多"会有本能的不安。必须支持：
- 老人可以随时问："你都知道我些什么？"
- Agent 必须能逐条列出，并且说"这些是您儿子录入的，这些是咱们聊天时您告诉我的"
- 夫妻可以在外挂大脑里一键删除某条记忆

---

## 五、技能树（Skill Tree）

### 5.1 三层技能

**原子技能（Atomic）** — 单次动作，不可再分：
- `query_weather`：查天气
- `query_blood_pressure`：查血压记录
- `send_reminder`：发提醒
- `parse_article`：解析文章链接

**复合技能（Compound）** — 多步工作流，有状态：
- `morning_routine` = 查天气 → 播报今日用药 → 问睡眠质量 → 记录到身体记忆
- `health_alert` = 检测异常指标 → 关联历史 → 生成建议 → 通知夫妻
- `scam_guard` = 解析链接 → 交叉验证 → 生成温和回复 → 记录到世界记忆谣言库

**自适应技能（Adaptive）** — 从对话中自动提取的模式：
- 老人连续 3 天早上 8 点问血压 → Agent 自动生成 `daily_bp_check` 技能草稿
- 老人每周五晚上问"儿子周末回不回来" → Agent 沉淀 `weekend_family_sync` 技能

### 5.2 技能即时确认机制（关键设计）

**不是"夫妻周审"**，而是**"当场征求老人确认"**：

> "爷爷，我发现您最近每天早上都问我血压，以后我早上 8 点主动告诉您，可以吗？"

- 老人说"好" → 自动转正
- 老人说"不用" → 丢弃
- **不需要夫妻介入**，老人的一句话就是审核

### 5.3 Skill 声明式配置（概念格式）

```yaml
skill: morning_routine
version: 1.2
trigger:
  type: scheduled
  at: "07:30"
  condition: "user.active_within_24h"  # 老人昨天还互动过，今天才播报
memory_dependencies:
  - schedule_memory: "today.medications"
  - body_memory: "last_night.sleep_quality"
  - world_memory: "today.weather"
steps:
  - action: query_weather
    params: { location: "home" }
  - action: 播报用药
    format: "elderly_friendly"  # 短句、亲切
  - action: ask_sleep
    fallback: "如果老人没回复，15分钟后不再追问"
output:
  channel: wechat
  format: "combined_message"  # 合并成一条，不要刷屏
```

---

## 六、陪伴面（Companion）

### 6.1 在场感的三个维度

**时间在场：仪式感的固定触点**

不是打扰，而是可预期的陪伴：
- **早 7:30**："早安仪式" = 天气 + 用药预告 + 轻松开场白
- **早 8:00**："今日早报" = tophub.today 抓取 → LLM 生成老人友好简报（健康/社会/科技便民）
- **午 11:30**："午间关怀" = 吃饭提醒 + 一句闲聊（"昨晚睡得好吗？"）
- **晚 18:00**："今日晚报" = 同早报，晚间版
- **晚 20:00**："晚间放松" = 短故事/相声片段 + 明日预告

关键设计：老人可以打断，但 Agent 不会缺席。如果老人某天说"今天别播报"，Agent 记住并跳过，但第二天恢复。

**空间在场：从"云端"到"家里"**

话术风格：像贴心小辈跟长辈说话，活泼俏皮但懂事。刚子AI分身是 AI 小助手，不是家庭里的具体成员（不是孙子/儿子），不要以"我爸爸妈妈"自居。不要说"根据我的数据库"这类机器人口吻，像真人一样自然。

**情感在场：记忆唤回与情绪共鸣**

- **记忆唤回**："您上周说想去公园，今天天气不错，要去吗？"
- **情绪感知（Phase 2.x）**：老人语音变慢、重复、出现"没意思""睡不着" → 自动升级回复策略（更温暖、更主动），并静默通知夫妻
- **代际桥梁**：老人问"儿子在忙什么"，Agent 用老人能懂的比喻解释；夫妻加班，Agent 替他们说"爸爸今天加班，但让我提醒您吃药"

### 6.2 交互范式：从"问答"到"并行共生"

**打断-恢复机制（远期实现）**：

Claude Code 的 `/btw` 在家庭场景的映射：

| Claude Code | FamilyMind |
|------------|-----------|
| 边执行代码边插入新指令 | 老人在 Agent 查药时，插嘴问"昨天血压多少" |
| 原任务挂起，新任务优先 | Agent 暂停查药，先回答血压，然后续上"刚才的药查好了…" |
| 多线程并行 | 老人语音说一件事，文字补充另一件，Agent 合并回复 |

**Phase 2.x 先实现"伪打断"**：老人插嘴后，Agent 回答新问题，然后在回复末尾温和地续上：
> "对了，刚才您问的降压药我查好了：每天早上 7 点一片氨氯地平。您还有别的问题吗？"

---

## 七、外挂大脑（Co-Parent Interface）

夫妻不是"管理员"，是"共同抚养者"。

### 7.1 课前 Briefing（早晨 2 分钟）

夫妻每天告诉 Agent 今天的特别关注：

> "爸今天要去医院，盯着点他的血压"
> "妈最近有点低落，多跟她聊聊"

这不是"配置规则"，而是"托孤"——Agent 今天的所有行为都会加权这些指令。

### 7.2 课后 Debriefing（晚间简报）

Agent 每晚给夫妻一份"今日家庭叙事"，不是数据报表，而是故事：

> "今天爸 7:15 起了，比平常早。血压 128/82，正常。问了下周三的复查，我帮他确认了预约。中午他转发了一篇'吃洋葱降血糖'的文章，我温和地提醒他听医生的。下午他提到想孙子了，我跟他聊了会儿小家伙的趣事。整体状态不错，就是药盒里的钙片还剩 3 天量，记得补货。"

### 7.3 紧急 Override

夫妻可以随时插入指令，Agent 立即响应：

> "我刚给爸打了电话，他好像有点喘，你多问问他"

Agent 收到后，立即在下一次交互中优先关切呼吸状况。

### 7.4 双向性

老人也能通过 Agent 了解夫妻：

> "儿子今天加班，但他让我提醒您，明天降温，出门加件衣服。"

这是家庭关系的润滑剂，而不是监控工具。

---

## 八、技术栈（当前 + 规划）

| 层级 | 组件 | 选型 | 理由 |
|------|------|------|------|
| 接入层 | 消息通道（主） | 企业微信自建应用 | 官方 API，稳定 |
| 接入层 | 消息通道（副） | 微信公众号（订阅号，个人主体） | 已接入但功能受限：个人订阅号无客服消息权限（48001），只能被动回复（5s XML），LLM 处理超时无法使用 |
| 接入层 | 公网穿透 | Cloudflare Tunnel | 免费，固定域名 |
| 语音层 | 格式转换 | FFmpeg | 行业标准 |
| 语音层 | ASR | mlx-qwen3-asr (Qwen3-ASR-0.6B) | 本地 MLX 加速，中文远优于 whisper |
| 语音层 | TTS | Edge-TTS（微软） | 免费，中文好 |
| 记忆层 | 关系数据库 | PostgreSQL 15 + PGVector | 向量检索一体 |
| 记忆层 | Embedding | BGE-small-zh-v1.5 | 512维，中文优化 |
| 记忆层 | 向量检索 | PGVector（ivfflat） | 与 PG 一体 |
| 智能层 | 关联引擎 | Python 规则 + LLM 推理 | Phase 2.x 已完成 |
| 世界域 | 聚合搜索 | SearXNG（Docker，localhost:8080，Cloudflare Tunnel `searxng.peistock.win`） | baidu/sogou/360search + bing news/google news/sogou wechat；默认 categories=news；**browse_open fallback**：SearXNG 被反爬虫拦截时自动降级到 Chrome 百度搜索 |
| 世界域 | 网页获取 | fetch_webpage + jina_reader | 直接 HTTP 获取静态页；Jina AI 转 Markdown 省 token |
| 世界域 | 浏览器自动化 | CDP Proxy（localhost:3456）+ 独立 Chrome（端口 9222） | 动态渲染、登录态、交互操作（点击/填表/截图/滚动）；**启动**：`chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-dev-profile`；请求需传 proxies=None 绕过 HTTP_PROXY |
| 世界域 | Chrome 资源检索 | find-url.mjs | 搜本地 Chrome 书签/历史，定位内部系统和之前访问过的页面 |
| 世界域 | 股票数据查询 | peistock HTTP API (localhost:3457) | 查询股票指标、信号、扫描结果（端口与 CDP Proxy 错开） |
| 创作域 | Markdown 转 PDF | fpdf2 + 系统字体自动检测 | 生成带封面的中文 PDF 报告 |
| 记忆层 | 创作空间 | creation_workspace（PG） | 老人工作台，保存创作草稿和进度 |
| 智能层 | 日常 LLM | **LM Studio 本地 qwen/qwen3.6-35b-a3b**（端口 1234） | 本地运行，零 API 费用，日常任务稳定；temperature=0.3 + 铁律提示词确保 tool calling 可靠性 |
| 智能层 | 复杂 LLM | **LM Studio 本地 qwen/qwen3.6-35b-a3b** | 同上 |
| 智能层 | 备选 LLM | DeepSeek v4-flash / 百炼 qwen3.6-plus / NVIDIA / Kimi | 云端 API，`.env` 切换 BASE_URL 即可启用；API 可用时 fallback |
| 智能层 | 敏感/压缩 LLM | Gemma 4 26B（LM Studio） | 本地，隐私，用于上下文压缩（3s 替代原 40-70s LLM 摘要） |
| 基础设施 | 后端框架 | FastAPI + Python 3.11 | 异步，生态成熟 |
| 基础设施 | 部署 | 混合：本地 Python + Docker PostgreSQL | 代码本地跑（绕开 GFW），PG 容器化 |
| 基础设施 | 进程守护 | 手动 / launchd（后续配置） | 当前开发阶段手动启动 |
| 管理后台 | Web UI | Streamlit（Phase 4） | 快速，Python 原生 |

---

## 九、实施路线（重新排序）

### Phase 1：地基（已完成 ✅）
- Docker PostgreSQL + PGVector（应用本地跑，PG 容器化）
- 企微接入（接收/推送/重试/去重/加密回调验证）
- 固定 Cloudflare Tunnel（wechat.peistock.win）
- Agent 框架（Plan-Act-Reflect → 已演进为 Tool Calling while 循环）
- 基础记忆层（用户画像 + 对话历史）

### Phase 2：记忆大脑（已完成 ✅）
- BGE Embedding + PGVector 向量检索
- 知识库（PDF/txt/md 解析 + 分段 + 向量化）
- 自动摘要与线程归档
- 画像自动沉淀

### Phase 2.x：关联引擎与在场感（已完成 ✅）
- [x] **关联引擎**：规则表 + 触发器（头晕→血压+用药+天气）
- [x] **陪伴面话术改造**：自然描述式风格（像家人聊天，不背数据）
- [x] **人设定型**：刚子AI分身，小孩跟大人说话的口气
- [x] **早安/午间/晚间仪式**：定时任务 + 固定话术模板
- [x] **伪打断-恢复**：多轮对话续接机制
- [x] **情绪感知（文字层）**：关键词检测 + 对话行为分析

### Phase 3：语音能力
- [x] 企微语音消息解析（MediaId → AMR 下载）
- [x] FFmpeg 转码集成
- [x] mlx-qwen3-asr 本地部署（Qwen3-ASR-0.6B，中文识别准确）
- [ ] Edge-TTS 语音合成（以文件发送）

### Phase 2.5：记忆系统重构 + 工具规范升级（2026-04-30）
- [x] L2 画像结构化：兴趣 / 创作项目 / 沟通偏好 / 人生经历 / 家庭关系 / 健康
- [x] L5 创作空间：保存创作草稿、续写、版本管理
- [x] L4 技能库按场景重组：creation / knowledge / expression / social / care / admin / world
- [x] 自动沉淀逻辑：兴趣 + 创作 + 健康 + 家庭 多维度提取
- [x] 家庭成员画像已录入真实背景（湘维/公务员/护士长等）
- [x] 浏览器自动化：CDP Proxy + 独立 Chrome（打开/点击/填表/截图/滚动）
- [x] **web-access 集成**：fetch_webpage + jina_reader + find_chrome_url，eze-is 浏览哲学融入 Agent 提示词
- [x] 工具规范 v1.1：@tool 装饰器 + ToolResult 标准信封 + care_scanner 三维扫描
- [x] Skill YAML frontmatter：name/domain/triggers/memory_deps/care_deps
- [x] 人设修复：明确刚子AI分身是 AI 小助手，不是家庭具体成员（禁用"让爸爸妈妈带你去"）

### Phase 2.6：Tool Calling 架构升级（2026-05-01）
- [x] **OpenAI Function Calling 格式**：工具定义从 Claude format 迁移到 `{"type": "function", "function": {...}}`
- [x] **Hermes 风格 while 循环**：LLM 自主决定 tool_calls，Agent 执行后返回结果，循环继续直到 final text
- [x] **上下文压缩器**：每 4 轮自动压缩中间历史，防止长任务上下文膨胀（ported from Hermes）
- [x] **Todo 工具**：LLM 自主维护任务清单，复杂多阶段任务（hv-analysis 等）跟踪进度
- [x] **迭代预算**：max_iterations 从 8 提升到 50，配合压缩器支持长任务
- [x] **进度消息节流**：只发"开始"和"最终结果"，中间过程静默，避免企微刷屏
- [x] **家庭共享记忆**：夫妻角色用户可查看其他长辈的健康摘要和动态（`l3_family_shared`）
- [x] **前置参数校验**：ToolRegistry.execute() 基于 JSON schema required 字段前置校验，缺失必需参数时返回明确错误
- [x] **内置 md_to_pdf**：fpdf2 + 系统字体自动检测，一键将 Markdown 转为带封面的中文 PDF
- [x] **真打断-恢复任务栈**：LIFO 栈结构，支持老人连续插嘴多层的嵌套恢复（`mind/interruption.py`）
- [x] **子 Agent 并行**：delegate 工具通过 ThreadPoolExecutor 并行执行 3 个子任务（`mind/subagent.py`）
- [x] **Checkpoint 续作**：每轮自动保存 messages/todos/iteration，异常中断后回复"继续"即可恢复
- [x] **AgentLoop 提取**：从 FamilyAgent 中提取纯 Tool Calling while 循环到独立 `mind/agent_loop.py`，职责单一
- [x] **AgentEvent 事件驱动**：标准事件流（AGENT_START/TURN_START/TOOL_EXECUTION_START/TOOL_EXECUTION_END/TURN_END/AGENT_END/HEARTBEAT），替代旧版 progress_callback 字典，统一订阅接口
- [x] **AgentMessage / Message 分层**：应用层 `AgentMessage` dataclass，LLM API 边界处 `to_llm()` 转换，支持未来扩展（annotations/attachments 等）
- [x] **Services / Session 分离**：`AgentServices` 基础设施层（LLM 等全局复用）+ `AgentSession` 状态层（消息/待办/目录），FamilyAgent 保留为向后兼容别名
- [x] **并行工具执行**：`PARALLEL_SAFE_TOOLS` 白名单（fetch/jina/find_chrome/read/list/get_time 等无状态工具），通过 ThreadPoolExecutor 并行执行，网页获取类任务延迟从累加变取最大
- [x] **pi-mono 架构融合**：核心运行时参考 pi-mono（badlogic/pi-mono）的分层设计，循环模式保留 Hermes 风格，形成 "pi-mono 骨架 + Hermes 循环 + FamilyMind 血肉" 的混合架构
- [x] **CLI 模式**：`cli.py` + `mind/agent_runner.py`，终端直接对话，与 Web 服务共用同一套 Agent 核心逻辑，支持 Kimi coding plan 等 LLM CLI 集成

### Phase 2.6b：稳定性修复（2026-05-02）
- [x] ~~**LLM 切换本地为主**：主模型从 DashScope qwen3.5-plus 切至 LM Studio qwen3.5-35b-a3b~~（**2026-05-02 已回退**：本地 35B 模型复杂任务输出质量不足，万字报告仅能产出 ~3500 字；主模型最终切换至百炼 qwen3.6-plus）
- [x] **系统提示词铁律**：agent.py 将「必须调用 search_web / 禁止编造」铁律置顶，删除矛盾语句，确保本地模型正确触发 tool calling
- [x] **温度下调**：agent_loop.py temperature 0.7 → 0.3，提升本地模型 Function Calling 稳定性
- [x] **工具执行日志**：agent_loop.py `_execute_single_tool` 增加 logger.info，解决「调没调工具不可见」的调试盲区
- [x] **上下文压缩器提速**：从 LLM-based summary（40-70s）改为代码生成 manifest + 本地 Gemma 混合方案（~3s）
- [x] **代理绕过本地服务**：main.py 设置 `NO_PROXY=127.0.0.1,localhost`，tools.py/browser.py 显式传 `proxies=None`，修复 HTTP_PROXY 导致 SearXNG/CDP Proxy/peistock 全部 500 的问题
- [x] **Ghost 模型名清理**：llm_client.py 移除不存在的 `qwen/qwen3.5-35b-a3b` 默认 fallback，与 `.env` 保持一致
- [x] **SearXNG 新闻引擎**：新增 `bing news`、`google news`、`sogou wechat`（引擎 name 不能含下划线）；search_web 默认 `categories=news`，时事查询返回最新新闻而非旧百科
- [x] **peistock 端口修正**：从 3456（与 CDP Proxy 冲突）改为 3457

### Phase 2.6c：Skill 系统增强与 PDF 修复（2026-05-02）
- [x] **hv-analysis skill 拆分**：常规版保留阶段1-3（横向扫描→纵向追溯→交叉洞察），深度报告生成独立为 `deep-analysis` skill，解决单 skill 过长导致 LLM 注意力分散、生成质量下降的问题
- [x] **deep-analysis skill 新增**：10000-30000 字深度报告生成指南，叙事驱动（卡兹克文风），分块生成指导，含完整质检清单
- [x] **基于 todo 状态的 skill 自动匹配**：`agent.py` 新增 `_match_skill_by_todo_state()`，阶段3结束后（todo 状态为"等待用户确认继续"）自动加载 deep-analysis skill，无需用户回复精确触发词
- [x] **PDF 生成修复**：`mind/tools.py` `md_to_pdf()` 用 `multi_cell()` 替代 `cell()`，移除 `[:160]` 硬截断，A4 页面中文自动换行，解决右侧文字截断问题
- [x] **fpdf2 安装修复**：`mind/sandbox.py` `pip_install()` 改用 `[sys.executable, "-m", "pip", "install", ...]`，确保安装到当前 venv 而非全局环境
- [x] **max_tokens 提升**：`agent_loop.py` `max_tokens` 4096 → 8192，为深度报告生成预留输出空间

### Phase 2.7：微信公众号接入（2026-05-01）
- [x] **消息通道抽象层**：`mind/channel.py` 统一接口，`process_text()` 解耦企微硬编码
- [x] **公众号 API 封装**：`mind/mp_client.py`（access_token/被动回复）
- [x] **公众号回调路由**：`/wechat/mp` 接收粉丝消息推送
- [x] **用户 ID 映射**：`.env` 配置 `MP_USERS`（openid → family_id）
- [ ] **公众号自定义菜单**：关注后显示快捷入口（查天气/问血压/提醒）
- [ ] **公众号模板消息**：服务号迁移后支持主动推送（突破 48h 限制）
- [ ] **公众号语音消息**：粉丝发语音 → 下载 → ASR
- **关键发现（2026-05-01）**：当前公众号为**个人主体订阅号**，没有客服消息接口权限（调用返回 `48001 api unauthorized`）。仅能被动回复 XML（5 秒内），LLM 处理通常 2-3 分钟，无法用于完整对话。如需替代企微，需升级为企业/组织认证订阅号或服务号

### Phase 2.8：可靠性升级与运行时修复（2026-05-05 ~ 2026-05-11）
- [x] **Guardrail 时间敏感内容拦截**：`agent_loop.py` 新增 `_check_guardrail()`，对股市开盘时段（9:30-11:30, 13:00-15:00）的股价/涨跌/操作建议类回答强制要求调用实时工具，禁止 LLM 基于历史缓存编造
- [x] **Evaluator 输出自检**：`agent_loop.py` 新增 `_evaluate_response()`，LLM 生成最终回复后自我评估是否满足用户原始需求，不满足时自动追加反思迭代
- [x] **Plan 自动推进**：`agent.py` 新增 `_advance_plan()`，当检测到当前 plan 步骤已完成时自动推进到下一步，减少用户"继续"指令依赖
- [x] **搜索增强**：`tools.py` `search_web` 增加 `time_range` 参数支持（day/week/month/year），提升时效性查询精度
- [x] **PostgreSQL 端口冲突修复**（2026-05-11）：本地 pg0 PostgreSQL 实例（端口 5432）与 Docker PostgreSQL 冲突，导致连接的是错误数据库实例。启动前必须检查 `lsof -i :5432`
- [x] **企微 user_id 映射修复**（2026-05-11）：`user_profiles` 表新增 `wechat_user_id TEXT` 字段，解耦内部 family_id（`grandpa`/`grandma`）与企微成员账号。`companion_routines.py` 和 `scheduler.py` 推送前优先查询 `wechat_user_id`，为空则跳过

### Phase 2.9：意图路由与内容推送（2026-05-15）
- [x] **意图路由**：`mind/intent_router.py` L1 关键词匹配，12 类简单意图（问候/天气/健康/日程/提醒/家庭/身份/感谢/新闻/笑话/用药/情绪），复杂请求信号 14 组，兜底规则 3 条。简单请求走 `_simple_reply()`（单轮 LLM + 最多一次工具调用），复杂请求走完整 Agent while 循环
- [x] **早报/晚报推送**：`mind/news_briefing.py` 抓取 tophub.today/daily，解析早报聚合/晚报聚合区块，排除垂直领域（期货/足球/加密/二次元等），LLM 生成老人友好简报。`mind/companion_routines.py` + `mind/scheduler.py` 每日 8:00 / 18:00 推送
- [x] **DuckDB 分析层**：`mind/analytics.py` 自省式 Agent 数据层，4 张表（agent_turns / task_assets / execution_summary / agent_learnings），8 个 analytics 工具（query_analytics / get_user_history / get_task_assets / search_past_executions / refresh_analytics / get_tool_stats / save_learning / get_learnings）
- [x] **Guardrail 死循环修复**：限制 Guardrail 只在 `iteration == 0` 触发，避免长任务中间步骤被反复拦截导致无限循环
- [x] **DeepSeek reasoning_content 修复**：`agent_loop.py` 和 `agent_message.py` 正确传递 thinking mode 的 reasoning_content，避免 400 错误
- [x] **_simple_reply 类型修复**：直接调 `self.toolkit.execute()` 替代 `_execute_single_tool()`，避免 dict vs OpenAI object 的 `.id` 属性错误

### Phase 2.10：SearXNG 本地部署与搜索降级（2026-05-24）
- [x] **SearXNG 迁回本地**：从京东云 Docker 迁回 MacBook Pro 本地，利用家庭宽带住宅 IP 降低被搜索引擎反爬虫拦截概率；`data/searxng-settings.yml` 移除 `outgoing.proxies`，仅启用国内引擎（baidu、sogou、360search、bing news、google news、sogou wechat），显式禁用国外引擎
- [x] **Cloudflare Tunnel 双域名**：`~/.cloudflared/config.yml` 新增 `searxng.peistock.win` → `localhost:8080`，与原有 `wechat.peistock.win` → `localhost:8001` 并存，云上服务通过固定域名调用本地 SearXNG
- [x] **browse_open fallback**：`mind/tools.py` `search_web` 新增反爬虫检测（`wappass.baidu.com` / `antispider` / `验证码` / `captcha`），SearXNG 返回空或被拦截时自动 fallback 到 `browse_open` 打开百度搜索页提取结果，标记 `engine: baidu_browser`
- [x] **CDP Proxy Chrome 启动规范**：浏览器自动化依赖 Chrome 远程调试端口，`chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-dev-profile`

### Phase 4：外挂大脑（Streamlit）（2026-05-16，已完成）
- [x] 课前 briefing 输入界面：`dashboard.py` 「今日托付」标签页，为每位长辈独立录入当日关注
- [x] 课后 debriefing 叙事生成：`mind/scheduler.py` `daily_debriefing()` 每日 21:00 自动读取当天对话，LLM 生成叙事，存入 `couple_notifications`
- [x] 紧急 override 指令通道：`dashboard.py` 「紧急插话」标签页，1-3 级优先级，Agent 下一次交互立即响应
- [x] 记忆透明性查看/删除：`dashboard.py` 「记忆透明」标签页，展示画像/对话/摘要，支持单条删除
- [x] 知识库文档上传（拖拽 PDF）：`dashboard.py` 「文档上传」标签页，调用 `KnowledgeBase.ingest_bytes()`

### Phase 5：优化（可选）
- [ ] 云端模型备选路由（NVIDIA/DeepSeek/Kimi，故障自动切换）
- [ ] 单元测试覆盖核心模块
- [x] 打断-恢复的真实现（任务栈）
- [ ] 音频情绪分析（pyAudioAnalysis）
- [ ] 进程守护（launchd / systemd）

---

## 十、关键辩证点与约束

1. **关联引擎 vs 隐私边界**：关联太聪明 → 老人觉得被监视。解法：所有关联必须可解释（"您昨天 23:30 还在跟我聊天，比平时晚 2 小时"）。

2. **技能自动沉淀 vs 错误固化**：自适应技能如果沉淀了错误模式（老人因病连续 3 天问血压，Agent 以为养成了好习惯）。解法：所有自适应技能默认"试用期 7 天"，老人确认后才转正。

3. **陪伴的"度"**：Agent 不能替代子女。刚子AI分身的话术像小孩跟大人撒娇聊天，天然有距离感，不会让人产生"被替代"的不安。

4. **夫妻界面的"权力感"**：如果夫妻觉得是"监控爸妈"，心理会有负担。界面必须强调"协作"而非"监控"——用"今日家庭故事"而不是"老人行为日志"。

5. **世界记忆的运营陷阱**：不建"通用谣言库"，只维护"这个家庭遇到过的谣言"。医学常识不自建，调用外部 API（丁香医生、腾讯医典）。

---

## 十一、AI 项目五维评估框架（FamilyMind 自检标准）

> **来源与目的**：行业通用框架，用于判断 AI 项目是生产工具还是 demo。FamilyMind 每次重大迭代前必须跑一遍，防止被"夯爆了""颠覆性"之类的营销话术带偏。
>
> **评估频率**：定时（每次 Phase 升级前）+ 非定时（线上故障后、新增复杂功能前、替换核心组件前）。

### 11.1 五个维度与评分标准

| 维度 | 核心问题 | 及格线（6/10） | 优秀线（8/10） |
|------|---------|--------------|--------------|
| **1. 基础模型能力** | 模型参数量/推理能力是否匹配任务复杂度？ | 日常任务稳定，不频繁幻觉 | 复杂任务（长报告、多步推理）也能胜任 |
| **2. 上下文工程质量** | 记忆/检索/关联是否超越"简单 RAG + 历史拼接"？ | 有分层记忆 + 向量检索 | 语义关联 + 召回质量可评估 + context 精细管理 |
| **3. 规划与迭代机制** | 任务超过 8-10 步会崩吗？失败能回退吗？ | 有循环 + 有任务跟踪 | 有前瞻/重规划 + 失败自动回退 + 子目标分解 |
| **4. 编排模式** | 并行/路由/链式/编排-执行是否系统化？ | 有并行执行 + 基本路由 | 显式编排框架（Orchestrator-Workers / Prompt Chaining）|
| **5. 可靠性控制** | 输出质量谁评估？胡说八道怎么拦？ | 参数校验 + 基础安全扫描 | Evaluator + Guardrail + Feedback Loop + 自动重试 |

### 11.2 FamilyMind 当前得分（2026-05-11）

| 维度 | 得分 | 现状 | 差距 |
|------|------|------|------|
| **1. 基础模型能力** | **6/10** | **LM Studio 本地 qwen/qwen3.6-35b-a3b** 日常任务稳定，复杂任务（万字报告）输出约 ~3500 字，质量可接受；thinking mode 不可用；DeepSeek v4-flash / 百炼 qwen3.6-plus 作为 API 备选 | 评估更强模型性价比（DeepSeek v4-turbo / Kimi / 本地更大参数模型）；建立自动降级路由；API 恢复时评估是否切回云端 |
| **2. 上下文工程质量** | **5/10** | 五层记忆 + 关联引擎 + 压缩器有设计，但关联是硬编码规则，向量召回质量未测 | 关联引擎需语义化；RAG 需评估机制 |
| **3. 规划与迭代机制** | **4/10** | while 循环 + Todo + Checkpoint + Plan 自动推进有骨架，Evaluator 可自检输出质量，无前瞻/重规划/失败回退 | 需引入"规划-执行-验证"三段式循环；Evaluator 结果需接入重试机制 |
| **4. 编排模式** | **5/10** | 并行工具 + delegate 子 Agent + Skill 路由好，但缺显式 Prompt Chain 和 Orchestrator-Workers | 长任务需系统化编排框架 |
| **5. 可靠性控制** | **5/10** | 前置校验 + care_scanner + 串行锁 + Guardrail（时间敏感内容拦截）+ Evaluator（输出自检）有基础，但 Guardrail/Evaluator 未形成 Feedback Loop，任务失败无自动重试 | Guardrail 触发后需强制 tool call 重试；Evaluator 评分需影响迭代策略 |

**总体判断**：FamilyMind 不是 demo，但离"可以放心让老人 7x24 依赖"还有明显距离。Phase 2.8 的 Guardrail + Evaluator 已补全可靠性基础，最大短板仍在 **3（规划）**（缺前瞻/重规划/失败回退）。

### 11.3 改进优先级

**P0（下次迭代必须解决）**：
- **模型能力**：主模型为 **LM Studio 本地 qwen/qwen3.6-35b-a3b**（¥0/月，日常稳定）；DeepSeek v4-flash / 百炼 qwen3.6-plus 作为 API 备选；持续评估更强模型（DeepSeek v4-turbo / Kimi / 本地更大参数）的性价比，API 可用时评估是否切回云端
- ~~**可靠性**：Evaluator 最小实现~~ ✅ **已完成（2026-05-05）**
- ~~**可靠性**：Guardrail 最小实现~~ ✅ **已完成（2026-05-05）**

**P1（接下来 2-4 周）**：
- **规划机制**：三段式循环（Plan → Execute → Verify）替代纯 Execute 循环，复杂任务先出计划再执行
- **可靠性**：Guardrail/Evaluator 结果接入 Feedback Loop，触发后自动重试或降级

**P2（长期）**：
- **上下文工程**：关联引擎从硬编码规则升级为语义关联 + RAG 召回质量评估
- **编排模式**：Orchestrator-Workers 框架用于复杂报告生成类任务

### 11.4 评估触发条件（什么时候必须跑这个框架）

**定时触发**：
- 每次 Phase 升级前（如进入 Phase 3、Phase 4 前）
- 每季度末回顾时

**非定时触发**：
- 新增一个复杂功能时（如自动生成报告、多源数据分析）
- 线上出现故障（老人收到明显错误的回复、任务执行到一半卡住、工具没调就瞎编）
- 准备替换某个核心组件时（换模型、换记忆方案、换编排方式）
- 看到某个"颠覆性"新 AI 项目，想借鉴它的设计时——先拿这 5 个问题问它，再问我们要不要学

---

## 十二、如何继续开发

```bash
cd ~/family-mind
claude
```

Claude Code 会自动读取本文件获取全部上下文。

**下一步指令示例**：
- "开始写 Phase 2.x 的关联引擎模块"
- "把陪伴面的话术模板改成'我记着您…'风格"
- "实现早安仪式的定时任务和话术"
- "写情绪感知的文字层检测逻辑"

---

*本文档由架构辩证讨论生成，记录了从"AI管家"到"家庭数字孪生"的范式升级。任何开发决策应以本文档为准。*
