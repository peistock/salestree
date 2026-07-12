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
│   │   └── Memory.ts      # 长期记忆加载
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
│   │   └── ws.ts          # WebSocket /ws/chat
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
