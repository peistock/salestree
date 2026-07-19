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
│   │   └── usageStore.ts  # LLM 用量与组织配额数据访问
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
- 价目表位于 `src/llm/pricing.ts`，按 model/provider 维护每 1M token 的 USD/CNY 价格；本地模型默认 0 成本。
- 组织月度 token 配额 `organizations.monthly_token_quota`：
  - WebSocket `/ws/chat` 在创建 `AgentSession` 前检查本月已用量，超配额立即返回错误。
  - `Session.ts` 在每轮 `turn_end` 记录用量后再次检查，超配额调用 `agent.abort()` 并返回已生成的部分内容。
- Admin API（`ADMIN_API_KEY` + 请求头 `X-Admin-Key`）：
  - `GET /api/admin/usage?org_id=&user_id=&start_date=&end_date=&limit=&offset=`
  - `GET /api/admin/usage/summary?org_id=&user_id=&start_date=&end_date=`
  - `GET /api/admin/orgs`
  - `PATCH /api/admin/orgs/:org_id/quota` body `{ monthly_token_quota: number }`
- 当前没有真正的用户鉴权，`user_id` 由客户端自声明；配额拦截是“尽力而为”，等后续接入真实认证后再补强。

## 资讯看板

- `/wechat_kb` 由 `src/routes/wechatKb.ts` 直接服务 `third_party/wechat-digest-skill/output/digest.html`。
- `POST /api/refresh_news_panel` 接收 JSON `{ accounts, token, cookie, since?, count? }`：
  - 把 `token`/`cookie` 写入 `third_party/wechat-digest-skill/credentials.json`；
  - 按 `accounts`（公众号名称数组）执行 `wechat_collector.py collect` → `analyze_kb.py` → `render_html.py`；
  - 立即返回“已开始采集”提示，后台 detached 执行。
- 前端 `public/chat.html` 提供账号 tag 输入、可折叠的 token/cookie 配置面板和“更新”按钮。

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
