---
name: 销销 架构纲领
description: B2B 销售 AI 助手「销销」的项目上下文、架构哲学与开发指南
type: project
---

# 销销 — B2B 销售智能协作空间

## 一、定位

销销的核心定位是：

> **客户与商机状态的实时镜像（Digital Twin of Sales Pipeline）**
> 围绕客户公司（account）、联系人（contact）、商机（deal）、互动记录（activity）和营销知识库建立记忆，让销售在见客户前懂更多、沟通时说得更准、跟进时做得更细。

核心不是功能列表，而是**_pipeline 状态的实时镜像_**。

---

## 二、核心假设（架构基石）

| 假设 | 具体表述 | 若假设不成立的影响 |
|------|---------|------------------|
| **单销售团队** | 系统先服务一个销售团队/小代理商，暂不做完整多租户隔离 | 已落地最小组织模型（`organizations` + `user_profiles.org_id`）与 LLM 用量计量，但完整 RBAC/数据隔离仍待后续 |
| **企微/工作渠道** | 销售人员主要在企业微信或 Web 聊天页使用 | 若团队不用企微，需强化 Web/邮件等替代通道 |
| **Mac/混合部署** | 主服务可运行在本地 Mac 或云服务器；SearXNG 默认保留在本地网络 | 本地断网时搜索降级为浏览器百度搜索 fallback |
| **混合部署** | 服务器运行销销主应用（FastAPI + PostgreSQL + 云/本地 LLM）；SearXNG 可保留本地 Mac，通过 Cloudflare Tunnel `searxng.peistock.win` 暴露 | 云端机房 IP 易被搜索引擎反爬虫拦截；本地网络断网时搜索降级为 Chrome 百度搜索 fallback。`.env` 中 `SEARXNG_URL` 控制指向本地还是远程域名 |
| **本地 LLM 为主** | 核心智能依赖 **LM Studio 本地 qwen/qwen3.6-35b-a3b**（端口 1234）；DeepSeek v4-flash / 百炼 qwen3.6-plus 作为 API 备选，通过 `LLM_FALLBACK_*` 环境变量配置后自动故障转移 | 本地 35B 模型日常任务稳定，复杂任务（万字报告）输出质量可达 ~3500 字；API 费用 ¥0/月；thinking mode 不可用 |
| **Docker PostgreSQL** | PG 容器通过端口映射暴露 localhost:5432 | 本地其他 PostgreSQL 实例（如 pg0）可能抢占 5432 端口，导致销销连接到错误数据库并报密码认证失败。启动前必须检查 `lsof -i :5432` |
| **本地隐私优先** | 客户沟通记录、报价等敏感数据本地处理；上下文压缩走本地 Gemma 4 26B | 本地模型能力持续升级，处理速度和质量可接受 |
| **销售主动录入** | 客户/联系人/商机由销售手动维护，非自动 CRM 同步 | 数据更新摩擦高，长期可能因懒惰而数据过时 |

---

## 三、三足鼎立架构

```
┌─────────────────────────────────────────────┐
│              协作面（Companion）              │
│         企微/微信 → 文字+语音+静默感知        │
│         早间简报 / 午间快讯 / 晚间复盘         │
└──────────────────┬──────────────────────────┘
                   │
    ┌──────────────┴──────────────┐
    ▼                             ▼
┌─────────────┐           ┌─────────────┐
│   技能树     │◄─────────►│   记忆体     │
│ Skill Tree  │  记忆驱动   │ Memory Body │
│             │   技能沉淀  │             │
│ • 原子技能   │           │ • 瞬时记忆   │
│ • 复合技能   │           │ • 客户记忆   │
│ • 自适应技能 │           │ • 商机记忆   │
│             │           │ • 知识记忆   │
│ 触发器      │           │ • 世界记忆   │
│ 执行器      │           │             │
│ 沉淀器      │           │ 关联引擎     │
└──────┬──────┘           └──────┬──────┘
       │                         │
       └──────────┬──────────────┘
                  ▼
       ┌─────────────────────┐
       │    管理驾驶舱          │
       │（销售主管协作界面）      │
       │ 客户/联系人/商机 CRUD   │
       │ 今日关注               │
       │ 紧急插话               │
       │ 记忆透明               │
       │ LLM 用量看板           │
       │ 用户与组织管理          │
       └─────────────────────┘
```

三者的关系：**记忆体是土壤，技能树是果实，协作面是阳光。**

---

## 四、记忆体（Memory Body）

### 4.1 核心实体

| 实体 | 说明 | 示例 |
|------|------|------|
| **account** | 客户公司 | 快手、三只松鼠、某新能源汽车品牌 |
| **contact** | 联系人 | 市场部总监、品牌经理、采购负责人 |
| **deal** | 商机 | 年度框架、KOL 投放、短视频制作项目 |
| **activity** | 互动记录 | 电话、微信、会议、邮件、报价 |
| **knowledge** | 知识库文档 | 行业报告、案例、公众号文章、竞品资料 |
| **lead** | 销售线索 | 从公众号文章/新闻中自动提取的客户机会 |

### 4.2 记忆分层

| 记忆类型 | 定义 | 对销售的价值 |
|---------|------|------------|
| **瞬时记忆** | 当前对话线程（20 轮） | 保持上下文连续 |
| **客户记忆** | account + contact 基础与动态 | 见客户前快速了解背景 |
| **商机记忆** | deal 阶段、金额、下一步 | 跟进节奏不遗漏 |
| **知识记忆** | 向量知识库中的行业/案例 | 说方案时有据可依 |
| **世界记忆** | 外部信息（新闻、融资、人事、口碑） | 谈资和 trigger 实时更新 |

### 4.3 关键升级：记忆不是"存储"，而是"关联"

当前代码是被动检索（销售问 → 查向量库 → 答）。灵性来自**主动关联**。

**平庸 vs 灵性的对比：**

> 销售说："快手那边最近怎么没动静"

**平庸 Agent**："我再帮您查一下。"

**灵性 Agent**："我记着快手上周刚发布了 Q2 财报，广告收入增速 15%；您这边负责的市场总监上周三在微信里问过你们 KOL 投放的报价，目前商机还在方案阶段，预计金额 80 万。您要不要我基于这两条动态，拟一封跟进微信？"

**实现要求**：当输入一个信号（"客户""竞品""报价"），关联引擎自动激活相关记忆节点（account.basic、account.signals、deal.stage、competitor.pricing），然后由技能树决定调用哪个复合技能。

### 4.4 关联引擎

核心规则见 `mind/association_engine.py`：

```python
ASSOCIATION_RULES = {
    "客户": ["account.basic", "account.signals", "deal.stage"],
    "跟进": ["contact.last_touch", "activity.recent", "deal.next_step"],
    "报价": ["deal.expected_value", "competitor.pricing"],
    "竞品": ["competitor.pricing", "competitor.reviews", "account.signals"],
    "融资": ["account.signals"],
    "高管": ["account.signals"],
}
```

### 4.5 透明记忆原则

销售人员对 Agent"知道太多"会有本能的不安。必须支持：
- 用户可以随时问："你都知道我些什么？"
- Agent 必须能逐条列出，并且说明来源（"这是您录入的""这是咱们聊天时您告诉我的"）
- 销售主管可以在管理驾驶舱里一键删除某条记忆

---

## 五、技能树（Skill Tree）

### 5.1 技能分层

**原子技能（Atomic）** — 单次动作，不可再分：
- `search_web`：网络搜索
- `fetch_webpage`：网页获取
- `read_knowledge`：读取知识库
- `create_reminder`：创建提醒

**复合技能（Compound）** — 多步工作流，有状态：
- `account_research` = 搜索官网/新闻/融资/高管/招聘/竞品/口碑 → 输出 battlecard
- `outreach_drafter` = 加载 account + contact + 最近动态 → 生成微信/邮件文案
- `churn_risk_alert` = 检测负面信号 → 关联历史 → 生成建议 → 通知主管

**自适应技能（Adaptive）** — 从对话中自动提取的模式：
- 销售连续 3 天早上问某客户进展 → Agent 自动生成 `daily_deal_check` 技能草稿
- 销售每周五晚上问"下周拜访安排" → Agent 沉淀 `weekly_visit_brief` 技能

### 5.2 技能即时确认机制

**不是"主管周审"**，而是**"当场征求销售确认"**：

> "我发现您每天早上都会问我快手这个商机的进展，以后我早上 9 点主动告诉您，可以吗？"

- 销售说"好" → 自动转正
- 销售说"不用" → 丢弃
- **不需要主管介入**，销售的一句话就是审核

### 5.3 Skill 声明式配置

技能文件位于 `data/skills/<domain>/<skill-name>.md`，frontmatter 示例：

```yaml
---
skill: account-research
domain: research
triggers: ["客户研究", "查这个公司", "account research", "客户背景"]
memory_dependencies: ["account.basic", "account.signals", "world_memory.news"]
risk_deps: ["account.churn", "competitor.pricing"]
---
```

技能域当前包括：`research` / `outreach` / `content` / `analytics` / `risk` / `admin` / `world`。

---

## 六、协作面（Companion）

### 6.1 在场感的三个维度

**时间在场：仪式感的固定触点**

不是打扰，而是可预期的陪伴：
- **早 7:30**："早间销售简报" = 今日待跟进商机 + 今日待联系客户 + 一句行业快讯
- **午 11:30**："午间行业快讯" = tophub 抓取 → LLM 生成销售友好简报（行业/品牌/营销玩法）
- **晚 20:00**："晚间复盘" = 汇总今日互动、更新商机阶段、提醒明日 next_step

关键设计：销售可以打断，但 Agent 不会缺席。

**空间在场：从"云端"到"工作流"**

话术风格：像靠谱的销售搭档，专业但不官僚。销销是 AI 小助手，不是具体某个人，不要以某个具体人物身份自居。不要说"根据我的数据库"这类机器人口吻，像真人销售一样自然。

**情感在场：客户情绪与风险感知**

- **情绪感知**：客户消息中出现"太贵""考虑一下""担心"等信号 → 自动调整回复策略并通知主管
- **风险感知**：客户提到竞品、降价、领导变动 → 自动关联 account.signals 和 deal.stage，生成风险提示

### 6.2 交互范式：从"问答"到"并行协作"

**打断-恢复机制**：

销售在 Agent 查资料时，插嘴问"昨天快手那边报价多少"，Agent 暂停查资料，先回答报价，然后续上"刚才的客户研究查好了…"。

实现见 `mind/interruption.py`。

---

## 七、管理驾驶舱（Co-Pilot Interface）

销售主管不是"管理员"，是"共同协作者"。

### 7.1 今日关注（Briefing）

销售主管/销售每天告诉 Agent 今日特别关注：

> "今天重点盯一下快手的方案反馈"
> "帮我提醒某品牌经理下午 3 点的会"

这不是"配置规则"，而是"托付"——Agent 今天的所有行为都会加权这些指令。

### 7.2 晚间复盘（Debriefing）

Agent 每晚给销售主管一份"今日销售叙事"，不是数据报表，而是故事：

> "今天您跟快手市场总监通了 15 分钟电话，主要确认了 KOL 投放预算在 80-100 万之间，对方希望周五前看到方案。三只松鼠的报价已发出，对方回复'有点贵，内部评估一下'，我标记了价格敏感信号。明天上午 10 点建议您先跟进某新能源品牌的合同签署。"

### 7.3 紧急插话（Override）

主管可以随时插入指令，Agent 立即响应：

> "我刚跟客户通过电话，他对方案有疑虑，你下次回复时多给两个案例"

Agent 收到后，立即在下一次交互中优先关切该客户疑虑。

### 7.4 双向性

销售也能通过 Agent 了解团队动态：

> "今天团队有什么需要我关注的商机风险？"

这是协作润滑剂，而不是监控工具。

---

## 八、技术栈

| 层级 | 组件 | 选型 | 理由 |
|------|------|------|------|
| 接入层 | 消息通道（主） | 企业微信自建应用 | 官方 API，稳定 |
| 接入层 | 消息通道（副） | 微信公众号（订阅号，个人主体） | 已接入但功能受限：个人订阅号无客服消息权限（48001），只能被动回复（5s XML），LLM 处理超时无法使用 |
| 接入层 | Web 聊天页 + 资讯看板 + 政策看板 | **TypeScript Fastify** (`server/`)，WebSocket `/ws/chat` + `public/chat.html`；`/wechat_kb`、公司线索、销售政策看板均已由 TS 服务直接承载 | 销售人员主要工作界面；资讯/政策看板不再依赖 Python 遗留服务 |
| 接入层 | 遗留 API（可选） | Python FastAPI (`main.py`) | 仅保留作本地开发/调试入口；生产环境只需 TS 服务（端口 8001，`server/.env` 中 `PORT` 可修改）|
| 接入层 | 公网穿透 | Cloudflare Tunnel | 免费，固定域名 |
| 语音层 | 格式转换 | FFmpeg | 行业标准 |
| 语音层 | ASR | mlx-qwen3-asr (Qwen3-ASR-0.6B) | 本地 MLX 加速，中文远优于 whisper |
| 语音层 | TTS | Edge-TTS（微软） | 免费，中文好 |
| 记忆层 | 关系数据库 | PostgreSQL 15 + PGVector | 向量检索一体 |
| 记忆层 | Embedding | BGE-small-zh-v1.5 | 512维，中文优化 |
| 记忆层 | 向量检索 | PGVector（ivfflat） | 与 PG 一体 |
| 智能层 | 关联引擎 | Python 规则 + LLM 推理 | 已完成 |
| 世界域 | 聚合搜索 | SearXNG（Docker，默认 `localhost:8080`，可通过 `.env` 的 `SEARXNG_URL` 改为远程地址如 `https://searxng.peistock.win`） | baidu/sogou/360search + bing news/google news/sogou wechat；默认 categories=news；**browse_open fallback**：SearXNG 被反爬虫拦截时自动降级到 Chrome 百度搜索 |
| 世界域 | 网页获取 | fetch_webpage + jina_reader | 直接 HTTP 获取静态页；Jina AI 转 Markdown 省 token |
| 世界域 | 浏览器自动化 | CDP Proxy（localhost:3456）+ 独立 Chrome（端口 9222） | 动态渲染、登录态、交互操作（点击/填表/截图/滚动）；**启动**：`chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-dev-profile`；请求需传 proxies=None 绕过 HTTP_PROXY |
| 世界域 | Chrome 资源检索 | find-url.mjs | 搜本地 Chrome 书签/历史，定位内部系统和之前访问过的页面 |
| 世界域 | 股票数据查询 | peistock HTTP API (localhost:3457) | 查询股票指标、信号、扫描结果（端口与 CDP Proxy 错开） |
| 创作域 | HTML 方案在线编辑 | HTML-Editor（嵌入 `server/public/html-editor/`，MIT 协议） | Agent 生成的 HTML 方案可在浏览器直接编辑文字/样式并保存回 `data/uploads/`，无需反复回 chat 提需求 |
| 创作域 | Markdown 转 PDF | fpdf2 + 系统字体自动检测 | 生成带封面的中文 PDF 报告 |
| 智能层 | 日常 LLM | **LM Studio 本地 qwen/qwen3.6-35b-a3b**（端口 1234） | 本地运行，零 API 费用，日常任务稳定；temperature=0.3 + 铁律提示词确保 tool calling 可靠性 |
| 智能层 | 复杂 LLM | **LM Studio 本地 qwen/qwen3.6-35b-a3b** | 同上 |
| 智能层 | 备选 LLM | DeepSeek v4-flash / 百炼 qwen3.6-plus / NVIDIA / Kimi | 云端 API，通过 `LLM_FALLBACK_URLS/KEYS` 等环境变量配置后自动故障转移；错误分类按超时/限流/连接/认证分别处理 |
| 智能层 | 敏感/压缩 LLM | Gemma 4 26B（LM Studio） | 本地，隐私，用于上下文压缩 |
| 基础设施 | 后端框架 | **TypeScript Fastify**（`server/`，Web 聊天、对话持久化、资讯看板、项目看板）| TS 层已接管主要 Web 能力；复杂 Agent 逻辑在需要时仍可走 Python 遗留工具代理 |
| 基础设施 | 部署 | 混合：本地 Python + Docker PostgreSQL | 代码本地跑，PG 容器化 |
| 基础设施 | 进程守护 | 手动 / launchd（后续配置） | 当前开发阶段手动启动 |
| 管理后台 | Web UI | Streamlit | 快速，Python 原生 |

---

## 九、实施路线

历史已完成阶段见 git log。当前仍开放：

- Edge-TTS 语音合成
- 核心模块单元测试
- 音频情绪分析
- 进程守护（launchd / systemd）
- Coordinator 任务恢复：发送"继续"时应回到原 `x-*` 协调任务（当前待修复）

---

## 十、关键辩证点与约束

1. **关联引擎 vs 隐私边界**：关联太聪明 → 销售觉得被监视。解法：所有关联必须可解释（"这是您上周录入的""这是今天的新闻"）。

2. **技能自动沉淀 vs 错误固化**：自适应技能如果沉淀了错误模式（销售连续 3 天问同一个丢单客户）。解法：所有自适应技能默认"试用期 7 天"，销售确认后才转正。

3. **协作的"度"**：Agent 不能替代销售。销销的话术像靠谱搭档，专业但不官僚，不会让人产生"被替代"的不安。

4. **管理驾驶舱的"权力感"**：如果主管觉得是"监控销售"，心理会有负担。界面必须强调"协作"而非"监控"——用"今日销售叙事"而不是"销售行为日志"。

5. **世界记忆的运营陷阱**：不建"通用行业库"，只维护"这个团队遇到过的客户/竞品/机会"。通用行业知识调用外部搜索和知识库。

---

## 十一、AI 项目五维评估框架

销销用五维框架自评项目成熟度。详见 [docs/ai-project-evaluation-framework.md](docs/ai-project-evaluation-framework.md)。

---

## 十二、如何继续开发

```bash
cd /Users/cpp/salestree
claude
```

Claude Code 会自动读取本文件与 `server/CLAUDE.md` 获取上下文。

**下一步指令示例**：
- "实现客户研究 skill 的多源并行搜索"
- "把早间销售简报的话术改成更直接的风格"
- "为管理驾驶舱增加商机漏斗视图"
- "改进关联引擎，支持语义匹配"
