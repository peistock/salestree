# 销销 — B2B 销售智能协作空间

面向互联网广告/营销公司销售团队的 AI 助手。核心定位是**客户与商机状态的实时镜像**：围绕客户公司、联系人、商机和营销玩法建立记忆，让销售在见客户前懂更多、沟通时说得更准、跟进时做得更细。

> 本项目由 FamilyMind（家庭数字孪生） pivot 而来，当前实现、数据模型、系统提示词和技能库均以销售场景为准。

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
- [x] Web 聊天界面（`/chat`）三栏布局：协作看板 / 资讯看板
- [x] Streamlit 管理后台（`dashboard.py`）

## 项目结构

```
family-mind/
├── CLAUDE.md              # 项目上下文（给 Claude Code 读的）
├── docker-compose.yml     # PostgreSQL 容器编排
├── Dockerfile             # 应用容器构建（备用）
├── .env.example           # 配置模板
├── .gitignore
├── init.sql               # PostgreSQL 初始化
├── requirements.txt       # Python 依赖
├── main.py                # FastAPI 主服务 + Web 聊天 + 资讯工作台 API
├── cli.py                 # CLI 模式（终端直接对话）
├── dashboard.py           # Streamlit 管理后台
└── mind/
    ├── agent.py              # AgentSession 状态层（编排 + 快速路由 + checkpoint 恢复）
    ├── agent_runner.py       # Agent 执行公共逻辑（CLI 和 Web 共用）
    ├── agent_loop.py         # AgentLoop 运行时层（Tool Calling while 循环）
    ├── agent_events.py       # 标准事件定义
    ├── agent_message.py      # 应用层消息类型
    ├── services.py           # 基础设施服务层
    ├── tools.py              # 安全工具集（@tool + ToolResult + md_to_pdf）
    ├── tool_result.py        # 工具标准信封与注册表
    ├── context_compressor.py # 上下文压缩器
    ├── todo_store.py         # Todo 任务跟踪
    ├── interruption.py       # 真打断-恢复任务栈
    ├── subagent.py           # 子 Agent 并行执行器
    ├── browser.py            # 浏览器自动化（CDP Proxy）
    ├── channel.py            # 消息通道抽象层
    ├── care_scanner.py       # 三维扫描
    ├── memory.py             # 记忆系统
    ├── llm_client.py         # LLM 通用适配层
    ├── scheduler.py          # 定时任务
    ├── intent_router.py      # 意图路由
    ├── analytics.py          # DuckDB 分析层
    ├── embedder.py           # BGE 本地 Embedding
    ├── knowledge.py          # 知识库
    ├── vector_store.py       # PGVector 封装
    └── association_engine.py # 关联引擎

third_party/wechat-digest-skill/   # 资讯工作台（公众号文章 + 知识库 + 线索库）
├── kb.py
├── extract_leads_kb.py
├── assets/digest_template.html
└── output/
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
cd ~/family-mind
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

```bash
python3 -m uvicorn main:app --host 0.0.0.0 --port 8001
```

### 5. 启动管理后台

```bash
streamlit run dashboard.py
```

浏览器访问 `http://localhost:8501`。

### 6. 访问功能

- **协作看板**：`http://localhost:8001/chat` — 与销销对话，进行客户研究、写跟进文案等
- **资讯看板**：`http://localhost:8001/wechat_kb` — 查看公众号文章、知识库、线索库

## 团队部署建议

销销当前默认是「本地开发机」模式。要给同事用，推荐：**服务器跑主应用 + 数据库，SearXNG 留在本地 Mac**。

原因：SearXNG 在云端机房 IP（如京东云）容易被百度/搜狗/360 反爬虫拦截，返回空结果或验证码；家庭宽带住宅 IP 稳定得多。

配置方法：

```bash
# 服务器上的 .env
SEARXNG_URL=https://searxng.peistock.win
```

本地 Mac 继续用 Docker 跑 SearXNG，并通过 Cloudflare Tunnel 把 `searxng.peistock.win` 暴露出去。主应用调用该域名即可。

```bash
# 本地开发/测试时的 .env
SEARXNG_URL=http://127.0.0.1:8080
```

主应用、PostgreSQL、LLM（建议切云 API）全部放在服务器；同事只需浏览器访问。

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

## 从 Git 克隆后直接使用

```bash
git clone https://github.com/peistock/xiaoxiaoshu.git
cd xiaoxiaoshu
# 后续步骤与「快速启动」相同
```
