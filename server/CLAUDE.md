# CLAUDE.md — 销销 TS 服务端

## 定位

销销后端的 TypeScript 重写层，基于 `@earendil-works/pi-agent-core` 与 `@earendil-works/pi-ai`，逐步接管原 Python FastAPI 服务的功能。

## 目录约定

```
server/
├── CLAUDE.md              # 本文件
├── package.json           # 依赖与脚本
├── tsconfig.json          # TypeScript 配置
├── .env.example           # 环境变量模板（密钥不进代码）
├── src/
│   ├── index.ts           # Fastify 启动入口
│   ├── config.ts          # 环境变量与配置
│   ├── db/
│   │   ├── index.ts       # PostgreSQL 连接
│   │   ├── schema.ts      # Drizzle schema
│   │   ├── usageStore.ts  # LLM 用量与组织配额数据访问
│   │   └── userStore.ts   # 用户数据访问
│   ├── llm/
│   │   ├── provider.ts    # LLM provider 与 failover 封装
│   │   └── pricing.ts     # 内部价目表（按 model/provider）
│   ├── agent/
│   │   ├── createAgent.ts # Pi Agent 工厂
│   │   ├── Session.ts     # 单次用户交互封装
│   │   └── eventMapper.ts # Pi 事件 → 销销 AgentEvent
│   ├── memory/
│   │   ├── Memory.ts      # 长期记忆加载
│   │   └── ConversationStore.ts # 对话线程与消息持久化
│   ├── association/
│   │   └── AssociationEngine.ts
│   ├── skills/
│   │   └── SkillLoader.ts
│   ├── knowledge/
│   │   ├── KnowledgeBase.ts
│   │   └── VectorStore.ts
│   ├── tools/
│   │   ├── Toolkit.ts     # AgentTool 注册
│   │   ├── getTime.ts     # 原子工具示例
│   │   └── pythonProxy.ts # 调用 Python 遗留工具
│   ├── routes/
│   │   ├── health.ts
│   │   ├── admin.ts       # Admin API：用量查询、组织配额管理
│   │   ├── ws.ts          # WebSocket /ws/chat
│   │   ├── upload.ts      # POST /api/upload 文件上传
│   │   ├── editorSave.ts  # POST /api/editor/save HTML 编辑器保存
│   │   ├── wechatKb.ts    # /wechat_kb 资讯看板页面与静态资源
│   │   ├── companyLeads.ts# /api/wechat_kb/company_leads 线索聚合（公司视角）
│   │   └── policy.ts      # /api/sales_policies 销售政策看板（飞书表格同步）
│   └── utils/
│       ├── logger.ts
│       └── fileStorage.ts # 上传文件保存、URL 映射、文件名安全化
├── public/
│   ├── chat.html          # Web 聊天主页面
│   └── html-editor/       # HTML-Editor 静态文件（MIT 协议）
├── scripts/
│   └── spike.ts           # 本地验证脚本
└── dist/                  # tsc 编译输出（gitignore）
```

## 工程纪律

- 默认中文沟通，代码/变量/文件名用英文。
- 密钥只进 `.env`，不进代码、不进 commit、不进日志。
- 每个工具/模块先写最小可用版本，再扩展。
- 优先复用原 Python 逻辑：复杂工具先走 `pythonProxy`，再决定是否内迁。
- 新增依赖需说明理由，避免为了“未来可能”引入抽象。

## 销售政策看板

- `scripts/feishu/sync_sales_policy.py` 只同步飞书表格中名为 `SALES_POLICY_SHEET_NAME`（默认「汇总」）的单个 sheet 到 `data/sales_policies.json`。
- 当前配置：表格 `P79Hs1Gn8hKAnLtuEJ3cACeUnbh`，sheet `汇总`。
- `src/routes/policy.ts` 暴露 `GET /api/sales_policies`（`?q=` 搜索）和 `POST /api/refresh_sales_policies`。
- 前端按原表渲染为 HTML 表格，**保留原表格术语**（返货/返现/返点等不改写）。

## 会话线程

- `src/memory/ConversationStore.ts` 管理 `conversation_threads` 与 `episodic_memory`。
- 列表排序规则：`is_archived ASC, updated_at DESC`。
- `activateThread` 只切换 `is_archived` 状态，**不更新 `updated_at`**；`updated_at` 只应由真实消息内容更新驱动，否则会导致侧边栏任务清单时间戳全部相同。

## 共享频道

一个项目/主题一个共享频道，全员可读全量历史、实时同步；AI 只在被 `@销销`/`@xiaoxiao` 时回复，且**仅用项目上下文**（不读任何成员的个人 Memory/Association）。

- **数据模型**：`conversation_threads.project_name` 非空即频道（部分唯一索引保证一项目一频道）；`linked_project` 决定自定义频道挂哪个项目的 AI 上下文；`channel_members(thread_id, user_id)` 为空 = 全员开放，非空 = 仅创建者+成员。消息多作者（`episodic_memory.user_id`），读历史 JOIN `user_profiles` 取姓名。所有用户维度查询必须带 `project_name IS NULL` 护栏（`getOrCreateActiveThread`/`listThreads`/`activateThread`/`archiveAllThreads`）。
- **WS 行为**（`src/routes/ws.ts`）：hello 支持 `project_name`（在管项目自动建频道，其他名字必须走创建 API，防绕过成员制）或 `thread_id`；人-人消息只落库+广播；@销销 消息进入**按 thread 串行队列**（`channelQueues`），AI 回复经 `channelViewers` 广播给频道内所有在线查看者（发送者也是 viewer，只广播不直发，天然无重复）。任何成员可带 `thread_id` 停止频道会话。
- **AI 上下文**（`Session.ts` 构造第八参 `{ projectName, contextProject, skipUserPersist }`）：频道模式跳过个人记忆加载，注入频道最近 20 条消息（含作者名）+ 关联项目 `_analysis.json` 摘要（≤800 字）；保留技能匹配与知识库向量检索（团队共享资产）。
- **HTTP API**：`GET /api/project_channels?user_id=`（在管项目 ∪ 可见自定义频道）、`GET /api/channel_detail?thread_id=`（私有线程 403）、`POST /api/create_channel`、`POST /api/channel_members`（仅创建者）、`POST /api/channel_transfer`（仅创建者，接收方自动补进成员）、`POST /api/delete_channel`（仅创建者；在管项目频道不可删）。`delete_task`/`rename_task`/`switch_thread` 对频道一律拒绝。
- **前端**（`public/chat.html`）：侧边栏「频道」区 + ＋创建对话框；自建频道行尾 ⚙ 管理对话框（成员增删/转让/删除）；输入 `@` 自动补全（销销置顶 + 频道成员，↑↓/Enter/Tab/Esc）；项目看板「问销销/追问背景」跳主聊天区频道并预填（不自动发送）。
- **聊天 Markdown**：AI 消息统一走 `renderMarkdown`（marked.js，`gfm:true, breaks:true`），表格/列表正常渲染；`.msg.agent.markdown-body` 需 `white-space:normal` 覆盖气泡的 `pre-wrap`。

## 文件上传与多模态消息

- `POST /api/upload?user_id=&thread_id=` 接收 multipart 文件，保存到 `data/uploads/<userId>/<threadId>/<uuid>-<safeName>`。
- 允许类型：图片（png/jpg/gif/webp）、PDF、Word（.docx）、Excel（.xls/.xlsx）、PPT（.pptx）、HTML；单文件 10MB 上限。
- 上传后返回 `{ name, url, mimeType, size }`，`url` 为 `/data/uploads/...`，由 `/data` 静态资源挂载直接服务。
- WebSocket `/ws/chat` 消息支持 `attachments` 字段；图片作为 `image_url` 进入 LLM prompt，文档以文件名+链接进入文本 prompt。
- Agent 通过 `read_file`（文本/HTML/JSON/CSV 等）和 `read_document`（Word/Excel/PPT/PDF）工具读取文档内容；系统 prompt 要求用户上传文档后必须调用对应工具，不得以"无法读取本地文件"为由拒绝。

## HTML 在线编辑

- 在 `public/html-editor/` 下嵌入 [HTML-Editor](https://github.com/yuzycheng/HTML-Editor)（MIT 协议），用于编辑 Agent 生成的 HTML 方案。
- 访问路径：`/html-editor/room.html?file=/data/uploads/...&embed=1`
- `src/room.js` 从 `?file=` 参数加载 HTML，`src/collab.js` 在 embed 模式下走本地 Yjs，不连接 PartyKit。
- `POST /api/editor/save` 接收 `{ url, html }`，校验路径必须在 `/data/uploads/` 下，覆盖写入原文件。
- `public/chat.html` 对 HTML 文件显示 ✏️ 编辑按钮，点击在新标签页打开编辑器。

## 商业化计量与配额

- 新增 `organizations` 表与 `user_profiles.org_id`，每个用户归属一个组织。
- 新增 `llm_usage` 表，每次 LLM assistant turn 在 `Session.ts` 的 `turn_end` 事件中落表，字段包括 `org_id/user_id/thread_id/model/provider/input_tokens/output_tokens/total_tokens/cost_usd/cost_cny`。
- 价目表位于 `src/llm/pricing.ts`，按 model/provider 维护每 1M token 的 USD/CNY 价格；当前收录 Kimi k2.6、Agnes `agnes-2.0-flash`、DeepSeek `deepseek-v4-flash/pro` 等云端模型，未收录的模型按 0 成本记录。
- 用户自定义 LLM：`user_profiles.llm_config` 支持每个用户配置自己的 provider/baseUrl/apiKey/model（通过 dashboard 或 Admin API）。`server/.env` 必须设置 `USER_LLM_ENCRYPTION_KEY` 且与项目根 `.env` 一致，否则 dashboard 加密存储的 key 在 TS 服务中无法解密，会导致自定义 LLM 认证失败。
- Agent 迭代预算：`AGENT_MAX_TOTAL_TOKENS` 控制单次任务总 token 上限，复杂 HTML PPT 等长输出任务建议 `64000+`。
- 组织月度 token 配额 `organizations.monthly_token_quota`：
  - WebSocket `/ws/chat` 在创建 `AgentSession` 前检查本月已用量，超配额立即返回错误。
  - `Session.ts` 在每轮 `turn_end` 记录用量后再次检查，超配额调用 `agent.abort()` 并返回已生成的部分内容。
- 用户管理：
  - `src/db/userStore.ts` 提供用户 CRUD，`src/routes/admin.ts` 暴露 `GET/POST/PATCH /api/admin/users` 与 `POST /api/admin/users/:user_id/deactivate`。
  - `ws.ts` 收到消息时默认调用 `userStore.ensureUserExists()` 自动创建占位用户（归属 `org_default`）。
  - 设置 `REQUIRE_KNOWN_USERS=true` 可关闭自动创建，未知或禁用用户将被拒绝。
- Admin API（`ADMIN_API_KEY` + 请求头 `X-Admin-Key`）：
  - `GET /api/admin/usage?org_id=&user_id=&start_date=&end_date=&limit=&offset=`
  - `GET /api/admin/usage/summary?org_id=&user_id=&start_date=&end_date=`
  - `GET /api/admin/orgs`
  - `PATCH /api/admin/orgs/:org_id/quota` body `{ monthly_token_quota: number }`
  - `GET /api/admin/users`
  - `POST /api/admin/users`
  - `PATCH /api/admin/users/:user_id`
  - `POST /api/admin/users/:user_id/deactivate`
- 前端用户列表：`GET /api/users` 返回 `user_profiles` 中的活跃用户，供 `public/chat.html` 左上角用户切换菜单使用。
- 当前没有真正的用户鉴权，`user_id` 由客户端自声明；配额拦截是“尽力而为”，等后续接入真实认证后再补强。

## 资讯看板

- `/wechat_kb` 由 `src/routes/wechatKb.ts` 直接服务 `third_party/wechat-digest-skill/output/digest.html`。
- `POST /api/refresh_news_panel` 接收 JSON `{ accounts, token, cookie, since?, count? }`：
  - 把 `token`/`cookie` 写入 `third_party/wechat-digest-skill/credentials.json`；
  - 按 `accounts`（公众号名称数组）执行 `wechat_collector.py collect` → `analyze_kb.py` → `render_html.py`；
  - 立即返回“已开始采集”提示，后台 detached 执行。
- 前端 `public/chat.html` 提供账号 tag 输入、可折叠的 token/cookie 配置面板和“更新”按钮。

## 关键环境变量

| 变量 | 说明 |
|------|------|
| `LLM_BASE_URL` / `LLM_API_KEY` / `MODEL_DAILY/COMPLEX/SUMMARY` | 主 LLM provider，当前默认 Kimi k2.6 |
| `LLM_FALLBACK_URLS/KEYS/NAMES/MODELS` | failover provider 列表，必须一一对应；当前 Agnes fallback 模型为 `agnes-2.0-flash` |
| `USER_LLM_ENCRYPTION_KEY` | 用户自定义 LLM apiKey 加密密钥，**必须与项目根 `.env` 一致** |
| `AGENT_MAX_ITERATIONS/DURATION_SECONDS/TOTAL_TOKENS` | Agent 单次任务预算，复杂 HTML PPT 建议 `TOTAL_TOKENS=100000` |
| `SEARXNG_URL` | SearXNG 地址，本地开发默认 `http://127.0.0.1:8080` |
| `ADMIN_API_KEY` | Admin API 认证头 `X-Admin-Key` |

## 常用命令

```bash
# 开发
npm install
npm run dev          # tsx src/index.ts
npm run typecheck    # tsc --noEmit
npm run build        # tsc

# 验证
npx tsx scripts/spike.ts
```
