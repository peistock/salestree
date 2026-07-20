# 销销 — 新机器迁移说明（给 Agent 读）

## 这是什么

销销（SalesMind / Xiaoxiaoshu）是一个面向互联网广告/营销公司销售团队的 AI 协作助手。
- 主服务：TypeScript Fastify（`server/`，端口 8001，由 `server/.env` 中 `PORT` 控制）
- 遗留服务：Python FastAPI（`main.py`，端口 8000/8001，可选）
- 数据库：PostgreSQL 15 + PGVector（Docker 本地运行，主机端口 5433，容器内 5432）
- 本地 LLM：LM Studio（默认端口 1234）
- 管理后台：Streamlit（端口 8501）

原始项目路径：`/Users/peter/salestree`
远程仓库：`https://github.com/peistock/salestree.git`

---

## 压缩包 / Git 仓库里有什么

| 路径 | 说明 |
|------|------|
| `server/` | TypeScript 新服务端（Fastify + Pi Agent），承载 Web 聊天、对话持久化、文件上传、HTML 编辑 |
| `mind/` | 核心 Python 模块（Agent、记忆、工具、调度等） |
| `data/` | 业务数据与配置，包括技能 `data/skills/`、模板 `data/templates/`、SearXNG 配置 |
| `docs/` | 工具规范与架构文档 |
| `scripts/` | 启动脚本与同步脚本 |
| `main.py` / `cli.py` / `dashboard.py` | Python 遗留 Web 服务、CLI、管理后台 |
| `requirements.txt` | Python 依赖 |
| `docker-compose.yml` | PostgreSQL 容器编排 |
| `init.sql` | 数据库初始化脚本（含销售示例数据） |
| `CLAUDE.md` | 项目架构与上下文（必须读） |
| `README.md` | 完整启动手册 |
| `AGENT_HANDOFF.md` | 本文档 |

## 压缩包 / Git 仓库里没有什么

| 路径 | 原因 |
|------|------|
| `.env` / `server/.env` | 含 API Key、企微密钥、数据库密码，**必须单独配置** |
| `data/uploads/` | 客户上传/生成的文件（HTML 方案、聊天记录附件等），**不进版本控制** |
| `venv/` | Python 虚拟环境，可在新机器重建 |
| `server/node_modules/` | npm 依赖，可在新机器重建 |
| `server/dist/` | TypeScript 编译输出 |
| `logs/` / `*.log` / `nohup.out` / `.pid` | 运行时文件 |
| `.DS_Store` | macOS 系统文件 |

---

## 到新机器后的步骤

### 1. 克隆项目

```bash
cd /Users/peter
git clone https://github.com/peistock/salestree.git
cd salestree
```

### 2. 安装系统依赖

- Docker Desktop（跑 PostgreSQL）
- Python 3.11+
- Node.js 18+（TS 服务依赖）
- FFmpeg：`brew install ffmpeg`
- LM Studio（本地 LLM，默认端口 1234）
- `cloudflared`（可选，用于固定公网域名）

### 3. 安装项目依赖

```bash
# Python 依赖
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# TypeScript 服务依赖
cd server
npm install
```

### 4. 配置环境变量

**不要复制旧机器的 `.env`**。从模板创建新文件：

```bash
# 在项目根
 cp .env.example .env

# 在 server/ 目录
cd server
cp .env.example .env
```

然后编辑两个 `.env`，填入真实值：

| 必须配置项 | 说明 |
|-----------|------|
| `LLM_BASE_URL` / `LLM_API_KEY` | 默认 LM Studio 本地 `http://127.0.0.1:1234/v1` + `lm-studio`；也可切换 DeepSeek / 百炼 |
| `MODEL_DAILY` / `MODEL_COMPLEX` / `MODEL_SUMMARY` | 默认 `qwen/qwen3.6-35b-a3b`（LM Studio） |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | 默认 localhost:**5433**，与 `docker-compose.yml` 中 `5433:5432` 映射一致 |
| `SEARXNG_URL` | SearXNG 地址，本地 `http://127.0.0.1:8080`，服务器部署可指向 `https://searxng.peistock.win` |
| `ADMIN_API_KEY` | **新增**：访问 `/api/admin/*` 用量、配额、用户管理接口的认证密钥 |
| `REQUIRE_KNOWN_USERS` | **新增**：`true` 时只允许已知活跃用户连接 WS；默认 `false` 自动创建占位用户 |

> `.env` 必须独立传输或重新填写，**不能通过压缩包传播**。

### 5. 启动 PostgreSQL

```bash
docker-compose up -d db
```

启动前检查 5433 端口是否被占用：

```bash
lsof -i :5433
```

如果有非 Docker 的 PostgreSQL 进程，先停止，否则销销会连错数据库。

### 6. 启动服务

TypeScript 新服务（Web 聊天、文件上传、HTML 编辑器）：

```bash
cd server
npm run dev
```

访问 `http://localhost:8001/chat`。`server/.env` 中 `PORT` 可修改。

Python 遗留服务（可选，仅当需要旧 API 时启动）：

```bash
python3 -m uvicorn main:app --host 0.0.0.0 --port 8001
```

管理后台：

```bash
streamlit run dashboard.py --server.port 8501
```

浏览器访问 `http://localhost:8501`，密码为 `.env` 中的 `DASHBOARD_PASSWORD`（未配置则直接进入）。
后台新增「LLM 用量」和「用户管理」tab，可查看用量、修改配额、创建/编辑/禁用用户。

### 7. 配置企微回调（如需完整功能）

- Cloudflare Tunnel：`cloudflared tunnel run salesmind`
- 企微后台 URL：`https://wechat.peistock.win/wechat`
- Token / EncodingAESKey 与 `.env` 一致

---

## 关键注意事项

1. **数据延续性**
   - `data/uploads/`（客户文件、生成的 HTML 方案）**不进 git**，新机器需要单独迁移或重新生成。
   - PostgreSQL 数据在 Docker 卷里，不会随仓库迁移。如需保留，需要单独导出/导入：
     ```bash
     # 旧机器导出
     docker exec salesmind-db-1 pg_dump -U family salesmind > salesmind.sql
     # 新机器导入
     docker exec -i salesmind-db-1 psql -U family salesmind < salesmind.sql
     ```

2. **本地 LLM 必须可用**
   - 默认依赖 LM Studio 本地模型 `qwen/qwen3.6-35b-a3b`，端口 1234。
   - 如果新机器没有 GPU/内存不足，可在 `.env` 切到 DeepSeek / 百炼 API。

3. **数据库 schema 会演进**
   - `init.sql` 已包含商业化改造新增的 `organizations`、`llm_usage` 等表。
   - 如果已有数据库是旧 schema，需要手动执行 `init.sql` 末尾新增段，或重建容器：
     ```bash
     docker-compose down db
     docker volume rm salestree_db_data  # 会清空数据，谨慎操作
     docker-compose up -d db
     ```

4. **企业微信配置必须重新验证**
   - 新机器的公网域名 / IP 如果变化，需要在企微后台更新可信 IP 和回调 URL。

5. **不要提交 `.env`**
   - `.gitignore` 已排除 `.env` 和 `data/uploads/`，不要手动把它们加进版本控制。

---

## 如何继续开发

```bash
cd /Users/peter/salestree
claude
```

Claude Code 会自动读取 `CLAUDE.md` 和 `README.md` 获取项目上下文。

---

*更新日期：2026-07-20*
