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
│   │   └── schema.ts      # Drizzle schema
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
│   │   ├── ws.ts          # WebSocket /ws/chat
│   │   ├── wechatKb.ts    # /wechat_kb 资讯看板页面与静态资源
│   │   ├── companyLeads.ts# /api/wechat_kb/company_leads 线索聚合（公司视角）
│   │   └── policy.ts      # /api/sales_policies 销售政策看板（飞书表格同步）
│   └── utils/
│       └── logger.ts
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
