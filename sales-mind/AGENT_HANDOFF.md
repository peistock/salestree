# SalesMind — 新机器迁移说明（给 Agent 读）

## 这是什么

SalesMind 是一个基于企业微信的家庭 AI 管家系统，运行在 macOS 上。
- 后端：FastAPI + Python 3.11
- 数据库：PostgreSQL 15 + PGVector（Docker 本地运行）
- 本地 LLM：LM Studio（默认端口 1234）
- 向量 Embedding：BGE-small-zh-v1.5
- ASR：mlx-qwen3-asr（本地 MLX）
- 管理后台：Streamlit（端口 8501）

原始项目路径：`/Users/peter/sales-mind`

---

## 压缩包里有什么

| 路径 | 说明 |
|------|------|
| `mind/` | 核心 Python 模块（Agent、记忆、工具、调度等） |
| `data/` | 业务数据，包括 DuckDB 分析库、知识库文档、SearXNG 配置 |
| `docs/` | 工具规范文档 |
| `scripts/` | 启动脚本 |
| `main.py` / `cli.py` / `dashboard.py` | Web 服务、CLI、管理后台 |
| `requirements.txt` | Python 依赖 |
| `docker-compose.yml` | PostgreSQL 容器编排 |
| `init.sql` | 数据库初始化脚本（含家庭成员数据） |
| `CLAUDE.md` | 项目架构与上下文（必须读） |
| `README.md` | 完整启动手册 |
| `AGENT_HANDOFF.md` | 本文档 |

## 压缩包里没有什么

| 路径 | 原因 |
|------|------|
| `.env` | 含 API Key、企微密钥、数据库密码，**必须单独配置** |
| `venv/` | Python 虚拟环境，可在新机器重建 |
| `logs/` | 运行日志，体积大且可重新生成 |
| `__pycache__/` / `*.pyc` | 编译缓存 |
| `*.log` / `nohup.out` / `.pid` | 运行时文件 |
| `.DS_Store` | macOS 系统文件 |

---

## 到新机器后的步骤

### 1. 解压

```bash
cd /Users/peter
tar xzvf sales-mind.tar.gz
```

### 2. 安装系统依赖

- Docker Desktop（跑 PostgreSQL）
- Python 3.11+（`mlx-qwen3-asr` 需要 3.10+）
- FFmpeg：`brew install ffmpeg`
- LM Studio（本地 LLM，默认端口 1234）
- `cloudflared`（可选，用于固定公网域名）

### 3. 创建虚拟环境并安装依赖

```bash
cd /Users/peter/sales-mind
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. 配置环境变量

**不要复制旧机器的 `.env`**。从模板创建新文件：

```bash
cp .env.example .env
```

然后编辑 `.env`，填入真实值：

| 必须配置项 | 说明 |
|-----------|------|
| `LLM_BASE_URL` / `LLM_API_KEY` | 默认 LM Studio 本地 `http://127.0.0.1:1234/v1` + `lm-studio`；也可切换 DeepSeek / 百炼 |
| `MODEL_DAILY` / `MODEL_COMPLEX` / `MODEL_SUMMARY` | 默认 `qwen/qwen3.6-35b-a3b`（LM Studio） |
| `WECHAT_CORPID` / `WECHAT_AGENTID` / `WECHAT_SECRET` / `WECHAT_TOKEN` / `WECHAT_AESKEY` | 企业微信自建应用后台获取 |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | 默认 localhost:5432，与 docker-compose.yml 一致 |
| `WORK_DIR` / `DATA_DIR` | 默认 `./data` |

> `.env` 必须独立传输或重新填写，**不能通过压缩包传播**。

### 5. 启动 PostgreSQL

```bash
docker-compose up -d db
```

启动前检查 5432 端口是否被占用：

```bash
lsof -i :5432
```

如果有非 Docker 的 PostgreSQL 进程，先停止，否则 SalesMind 会连错数据库。

### 6. 启动服务

Web 服务：

```bash
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

CLI 模式：

```bash
python3 cli.py
```

管理后台：

```bash
streamlit run dashboard.py --server.port 8501
```

### 7. 配置企微回调（如需完整功能）

- Cloudflare Tunnel：`cloudflared tunnel run salesmind`
- 企微后台 URL：`https://wechat.peistock.win/wechat`
- Token / EncodingAESKey 与 `.env` 一致

---

## 关键注意事项

1. **数据延续性**
   - `data/analytics.duckdb` 和 `data/analytics.duckdb.wal` 已随压缩包迁移，DuckDB 分析数据会保留。
   - PostgreSQL 数据在 Docker 卷里，不会随压缩包迁移。如需保留，需要单独导出/导入：
     ```bash
     # 旧机器导出
     docker exec salesmind-db-1 pg_dump -U family salesmind > salesmind.sql
     # 新机器导入
     docker exec -i salesmind-db-1 psql -U family salesmind < salesmind.sql
     ```

2. **本地 LLM 必须可用**
   - 默认依赖 LM Studio 本地模型 `qwen/qwen3.6-35b-a3b`，端口 1234。
   - 如果新机器没有 GPU/内存不足，可在 `.env` 切到 DeepSeek / 百炼 API。

3. **企业微信配置必须重新验证**
   - 新机器的公网域名 / IP 如果变化，需要在企微后台更新可信 IP 和回调 URL。

4. **不要提交 `.env`**
   - `.gitignore` 已排除 `.env`，不要手动把它加进版本控制或压缩包。

---

## 如何继续开发

```bash
cd /Users/peter/sales-mind
claude
```

Claude Code 会自动读取 `CLAUDE.md` 和 `README.md` 获取项目上下文。

---

*生成日期：2026-07-09*
