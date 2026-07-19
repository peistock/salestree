# 销销 — B2B 销售智能协作空间

面向互联网广告/营销公司销售团队的 AI 助手。核心定位是**客户与商机状态的实时镜像**：围绕客户公司、联系人、商机和营销玩法建立记忆，让销售在见客户前懂更多、沟通时说得更准、跟进时做得更细。

## 已完成功能

- [x] 混合部署（本地 Python + Docker PostgreSQL + PGVector）
- [x] Agent 执行引擎（Tool Calling while 循环，Hermes 模式）
- [x] 本地 LLM 为主（LM Studio qwen3.6-35b-a3b，端口 1234，零 API 费用；DeepSeek / 百炼为 API 备选）
- [x] 意图路由（L1 关键词匹配，简单请求直接回复 vs 复杂请求走 Agent 循环）
- [x] 客户研究技能（account-research）：输入公司名自动产出带信息来源的 battlecard
- [x] 跟进文案技能（outreach-drafter）：基于客户动态和联系人角色生成个性化微信/邮件文案
- [x] 资讯工作台（`/wechat_kb`）：公众号文章聚合、知识库、**线索库**（媒体视角 + 公司视角）
- [x] 线索自动提取：从知识库文章中用 LLM 提取客户公司、竞品、可复制行业、服务机会
- [x] 向量知识库（PDF/txt/md 上传 + PGVector 检索）
- [x] 浏览器自动化（CDP Proxy + 独立 Chrome，支持打开/点击/填表/截图/滚动）
- [x] SearXNG 新闻引擎（本地 Docker + Cloudflare Tunnel `searxng.peistock.win`）
- [x] 上下文压缩器（长任务自动压缩中间历史，防止上下文膨胀）
- [x] Todo 任务跟踪 + Checkpoint 续作 + 真打断-恢复任务栈
- [x] 子 Agent 并行（delegate 工具）
- [x] DuckDB 分析层（agent_turns / task_assets / execution_summary / agent_learnings）
- [x] Web 聊天界面（`/chat`）四栏布局：协作看板 / 资讯看板 / 项目看板 / 销售政策看板
- [x] 销售政策看板：对接飞书表格，实时同步媒体端口返点政策与折扣控制线
- [x] TypeScript 新服务端（`server/`）：Fastify + Pi Agent，承载 `/chat`、对话持久化、任务历史、文件上传与 HTML 在线编辑
- [x] HTML 在线编辑器：基于 HTML-Editor，生成方案后可直接在浏览器编辑并保存回 `data/uploads/`
- [x] LLM 用量计量：每次 assistant turn 记录 `org_id/user_id/thread_id/model/provider/tokens/cost` 到 `llm_usage`
- [x] 组织月度 token 配额：WebSocket 运行前 + 每轮运行中超额拦截
- [x] Admin API：`/api/admin/usage`、`/api/admin/usage/summary`、`/api/admin/orgs`、配额修改、用户 CRUD
- [x] Streamlit 管理后台（`dashboard.py`）：含 LLM 用量看板 + 用户管理
- [x] WebSocket 幽灵用户处理：默认自动创建占位用户，可选 `REQUIRE_KNOWN_USERS=true` 严格校验

## 项目结构

```
salestree/
├── CLAUDE.md              # 项目上下文（给 Claude Code 读的）
├── docker-compose.yml     # PostgreSQL 容器编排
├── Dockerfile             # 应用容器构建（备用）
├── .env.example           # 配置模板
├── .gitignore
├── init.sql               # PostgreSQL 初始化
├── requirements.txt       # Python 依赖
├── main.py                # FastAPI 遗留主服务 + 资讯工作台 API
├── cli.py                 # CLI 模式（终端直接对话）
├── dashboard.py           # Streamlit 管理后台
├── mind/                  # Python Agent 核心模块
│   ├── agent.py              # AgentSession 状态层（编排 + 快速路由 + checkpoint 恢复）
│   ├── agent_runner.py       # Agent 执行公共逻辑（CLI 和 Web 共用）
│   ├── agent_loop.py         # AgentLoop 运行时层（Tool Calling while 循环）
│   ├── agent_events.py       # 标准事件定义
│   ├── agent_message.py      # 应用层消息类型
│   ├── services.py           # 基础设施服务层
│   ├── tools.py              # 安全工具集（@tool + ToolResult + md_to_pdf）
│   ├── tool_result.py        # 工具标准信封与注册表
│   ├── context_compressor.py # 上下文压缩器
│   ├── todo_store.py         # Todo 任务跟踪
│   ├── interruption.py       # 真打断-恢复任务栈
│   ├── subagent.py           # 子 Agent 并行执行器
│   ├── browser.py            # 浏览器自动化（CDP Proxy）
│   ├── channel.py            # 消息通道抽象层
│   ├── care_scanner.py       # 三维扫描
│   ├── memory.py             # 记忆系统
│   ├── llm_client.py         # LLM 通用适配层
│   ├── scheduler.py          # 定时任务
│   ├── intent_router.py      # 意图路由
│   ├── analytics.py          # DuckDB 分析层
│   ├── embedder.py           # BGE 本地 Embedding
│   ├── knowledge.py          # 知识库
│   ├── vector_store.py       # PGVector 封装
│   └── association_engine.py # 关联引擎
├── server/                # TypeScript 新服务端（逐步接管 Web 聊天）
│   ├── CLAUDE.md             # TS 服务端上下文
│   ├── package.json
│   ├── tsconfig.json
│   └── src/
│       ├── index.ts          # Fastify 启动入口
│       ├── routes/ws.ts      # WebSocket /ws/chat
│       ├── routes/chat.ts    # /chat 页面路由
│       ├── routes/upload.ts  # 文件上传
│       ├── routes/editorSave.ts  # HTML 编辑器保存
│       └── memory/           # 对话持久化
│   └── public/html-editor/   # HTML-Editor 静态文件
├── data/                  # 业务数据与配置
│   ├── skills/               # Agent Skill 定义（markdown）
│   ├── templates/            # 方案生成模板（如投流方法论 HTML 片段）
│   ├── assets/               # 静态资源
│   └── uploads/              # 上传/生成的客户文件（**不进 git**）
├── third_party/wechat-digest-skill/   # 资讯工作台（公众号文章 + 知识库 + 线索库）
│   ├── kb.py
│   ├── extract_leads_kb.py
│   ├── assets/digest_template.html
│   └── output/
```

## 快速启动

### 1. 前置依赖

```bash
# macOS
# - Docker Desktop（跑 PostgreSQL）
# - Python 3.11+
# - FFmpeg（brew install ffmpeg）
# - LM Studio（本地 LLM，默认端口 1234）
# - cloudflared（固定 Tunnel，可选）

# 克隆项目后进入目录
cd ~/salestree
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的真实配置：
# - LLM_BASE_URL=http://127.0.0.1:1234/v1（主，LM Studio 本地）
#   或 https://api.deepseek.com（备选）
#   或 https://dashscope.aliyuncs.com/compatible-mode/v1（备选，百炼）
# - LLM_API_KEY=lm-studio（LM Studio 本地）或你的 API Key
# - MODEL_DAILY=qwen/qwen3.6-35b-a3b
# - MODEL_COMPLEX=qwen/qwen3.6-35b-a3b
# - MODEL_SUMMARY=qwen/qwen3.6-35b-a3b
```

### 3. 启动 PostgreSQL

```bash
docker-compose up -d db
```

启动前确认 5432 端口未被其他 PostgreSQL 实例占用：

```bash
lsof -i :5432
```

### 4. 启动销销（Web 服务）

当前只需暴露 TypeScript 新服务（端口 8002）。`/wechat_kb` 资讯看板页面、`/api/wechat_kb/company_leads` 线索接口均已由 TS 服务直接提供，**无需再启动 Python 遗留服务（端口 8001）**。

```bash
cd server
npm install
npm run dev
```

如果只使用聊天、项目看板、资讯看板，只启动 TS 服务即可。

### 5. 启动管理后台

```bash
streamlit run dashboard.py
```

浏览器访问 `http://localhost:8501`。

### 6. 访问功能

- **协作看板 / 资讯看板 / 项目看板 / 销售政策看板**：`http://localhost:8002/chat` — 与销销对话、查看飞书项目情报、公众号文章与线索库、销售折扣控制线

## 团队部署建议

销销当前默认是「本地开发机」模式。要给同事或家里电脑用，推荐：**只通过 Cloudflare Tunnel 暴露 TS 服务（端口 8002）**。

```yaml
# ~/.cloudflared/config.yml（示例）
ingress:
  - hostname: xiaoxiao.peistock.win
    service: http://localhost:8002
  - hostname: searxng.peistock.win
    service: http://localhost:8080
  - service: http_status:404
```

家里/公司任意电脑访问：

- `https://xiaoxiao.peistock.win/chat` — 聊天、项目看板、资讯看板、销售政策看板

SearXNG 仍建议留在本地 Mac（或家里网络），通过同一隧道暴露 `searxng.peistock.win`。原因：云端机房 IP 容易被百度/搜狗/360 反爬虫拦截，住宅 IP 稳定得多。

```bash
# 服务器/本机 .env
SEARXNG_URL=https://searxng.peistock.win
```

## 使用示例

在 Web 聊天输入：

- 「研究一下快手公司」→ 触发 `account-research`，返回带来源标注的 battlecard
- 「帮我写一封跟进邮件给 [联系人姓名]」→ 触发 `outreach-drafter`，返回个性化文案
- 「最近新能源汽车行业有什么营销玩法」→ 触发搜索 + 知识库检索，输出行业动态

## 资讯工作台与线索库

`third_party/wechat-digest-skill/` 是公众号文章分析工作流：

1. 抓取/导入公众号文章
2. `kb.py analyze` — LLM 分析文章并写入知识库
3. `kb.py extract-leads` / `extract_leads_kb.py` — 从文章中自动提取销售线索
4. 在 `/wechat_kb` 的「线索库」中查看：
   - **媒体视角**：某篇文章露出的客户公司 → 竞品公司（纵向拓客）→ 可复制该玩法的类似行业（横向复制）
   - **公司视角**：目标公司 → 竞品公司 → 可扩展的服务机会

## 成本

- 大模型 API：**¥0/月**（LM Studio 本地 qwen3.6-35b-a3b）
- Embedding：**¥0/月**（BGE 本地）
- 上下文压缩：**¥0/月**（本地 Gemma 4 26B）
- PostgreSQL：**¥0/月**（Docker 本地运行）
- **总计：¥0/月**（全部本地运行，无 API 费用）

> 备选：DeepSeek v4-flash / 百炼 qwen3.6-plus 在 `.env` 中切换 `BASE_URL` 即可启用。

## Admin API（用量、配额与用户）

需要 `server/.env` 中配置 `ADMIN_API_KEY`，请求时带 header `X-Admin-Key`。

```bash
# 查看组织列表
curl -H "X-Admin-Key: $ADMIN_API_KEY" http://localhost:8001/api/admin/orgs

# 查看用量汇总
curl -H "X-Admin-Key: $ADMIN_API_KEY" \
  "http://localhost:8001/api/admin/usage/summary?org_id=org_default"

# 修改组织月度 token 配额
curl -X PATCH -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"monthly_token_quota": 10000000}' \
  http://localhost:8001/api/admin/orgs/org_default/quota

# 查看用户列表
curl -H "X-Admin-Key: $ADMIN_API_KEY" http://localhost:8001/api/admin/users

# 创建用户
curl -X POST -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"userId":"sales_003","name":"张三","role":"销售代表","entityType":"sales","orgId":"org_default"}' \
  http://localhost:8001/api/admin/users

# 禁用用户
curl -X POST -H "X-Admin-Key: $ADMIN_API_KEY" \
  http://localhost:8001/api/admin/users/sales_003/deactivate
```

> 当前 `user_id` 仍由客户端自声明，但 WS 会默认自动创建占位用户；设置 `REQUIRE_KNOWN_USERS=true` 可让未知/禁用用户被拒绝。

---

## 从 Git 克隆后直接使用

```bash
git clone https://github.com/peistock/salestree.git
cd salestree
# 后续步骤与「快速启动」相同
```
