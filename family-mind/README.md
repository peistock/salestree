# FamilyMind — 家庭 AI 管家

基于企业微信自建应用的家庭 AI 管家系统，服务 6 人家庭（4 位老人 + 夫妻管理员）。

## 已完成功能（Phase 1-2.x）

- [x] 混合部署（本地 Python + Docker PostgreSQL + PGVector）
- [x] 企微消息接入（接收/推送/重试/去重/加密回调验证）
- [x] 固定 Cloudflare Tunnel（`wechat.peistock.win` + `searxng.peistock.win`）
- [x] 四层记忆系统（L1 核心规则 / L2 用户画像 / L3 情景记忆 / L4 技能库）
- [x] Agent 执行引擎（Tool Calling while 循环，Hermes 模式）
- [x] 本地 LLM（**LM Studio qwen3.6-35b-a3b**，端口 1234，零 API 费用；DeepSeek v4-flash / 百炼 qwen3.6-plus 为 API 备选）
- [x] 安全工具集（已删除代码执行工具）
- [x] 定时任务（早播报 / 午间关怀 / 晚间放松）
- [x] 结构化日志 + 健康检查接口
- [x] 语音消息处理（AMR → FFmpeg → Qwen3-ASR → 文本回复）
- [x] 记忆系统重构（创作空间 + 结构化画像 + 场景化技能库）
- [x] 浏览器自动化（CDP Proxy + 独立 Chrome，支持打开/点击/填表/截图/滚动）
- [x] 工具规范升级（@tool 装饰器 + ToolResult 标准信封 + care_scanner 三维扫描）
- [x] 前置参数校验（ToolRegistry 基于 schema required 字段前置检查）
- [x] 内置 md_to_pdf（fpdf2 + 系统字体，Markdown 一键转带封面 PDF）
- [x] 上下文压缩器（长任务自动压缩中间历史，防止上下文膨胀）
- [x] Todo 任务跟踪（LLM 自主维护多阶段任务清单）
- [x] 真打断-恢复任务栈（LIFO，支持老人连续插嘴多层嵌套恢复）
- [x] 子 Agent 并行（delegate 工具，ThreadPoolExecutor 并行执行多子任务）
- [x] Checkpoint 续作（异常中断后回复"继续"即可恢复）
- [x] 家庭共享记忆（夫妻可查看长辈健康摘要）
- [x] 消息通道抽象层（企微为主；公众号已接入但个人订阅号无客服消息权限，功能受限）
- [x] AgentLoop 运行时提取（独立循环层，职责单一）
- [x] AgentEvent 事件驱动（标准事件流替代旧版回调）
- [x] AgentMessage / Message 分层（应用层与 LLM 层边界转换）
- [x] Services / Session 分离（基础设施与状态解耦）
- [x] 并行工具执行（无状态工具白名单 + ThreadPoolExecutor）
- [x] **意图路由**（L1 关键词匹配，简单请求直接回复 vs 复杂请求走 Agent 循环）
- [x] **早报/晚报推送**（tophub.today 抓取 → LLM 生成老人友好简报，早 8:00 / 晚 18:00）
- [x] **DuckDB 分析层**（agent_turns / task_assets / execution_summary / agent_learnings，自省式 Agent）
- [x] ~~**本地 LLM 为主**（LM Studio qwen3.5-35b-a3b）~~ → ~~百炼 qwen3.6-plus~~ → ~~DeepSeek v4-flash（2026-05-15）~~ → **LM Studio 本地 qwen3.6-35b-a3b**（2026-05-24，当前运行配置）
- [x] **上下文压缩器提速**（代码 manifest + 本地 Gemma，3s 替代原 40-70s）
- [x] **代理绕过本地服务**（NO_PROXY + 显式 proxies=None，修复 SearXNG/CDP Proxy/peistock 500）
- [x] **SearXNG 新闻引擎**（baidu/sogou/360search + bing news/google news/sogou wechat，默认 categories=news；SearXNG 本地 Docker + Cloudflare Tunnel `searxng.peistock.win`）
- [x] **用户级串行锁**（`threading.Lock`，防止同一用户并行任务冲突覆盖 checkpoint/todo）
- [x] **hv-analysis skill 拆分**（常规版阶段1-3 + 深度版 deep-analysis skill 独立，解决 skill 过长导致 LLM 注意力分散）
- [x] **基于 todo 状态的 skill 自动匹配**（阶段3结束后自动加载 deep-analysis，无需用户精确回复触发词）
- [x] **PDF 生成修复**（`multi_cell()` 替代 `cell()` + 移除字符截断，A4 页面正确换行）

## 项目结构

```
family-mind/
├── CLAUDE.md              # 项目上下文（给 Claude Code 读的）
├── docker-compose.yml     # PostgreSQL 容器编排
├── Dockerfile             # 应用容器构建（备用）
├── .env.example           # 配置模板
├── .gitignore
├── init.sql               # PostgreSQL 初始化（含家庭成员数据）
├── requirements.txt       # Python 依赖
├── main.py                # FastAPI 主服务 + 企微回调
├── cli.py                 # CLI 模式（终端直接对话 / Kimi coding plan 集成）
└── mind/
    ├── agent.py              # AgentSession 状态层（编排 + 快速路由 + checkpoint 恢复）
    ├── agent_runner.py       # Agent 执行公共逻辑（CLI 和 Web 共用同一套入口）
    ├── agent_loop.py         # AgentLoop 运行时层（Tool Calling while 循环，事件驱动）
    ├── agent_events.py       # 标准事件定义（AgentEvent / AgentEventType）
    ├── agent_message.py      # 应用层消息类型（AgentMessage，LLM 边界 to_llm() 转换）
    ├── services.py           # 基础设施服务层（AgentServices，全局复用）
    ├── tools.py              # 安全工具集（@tool 装饰器 + ToolResult 标准信封 + md_to_pdf）
    ├── tool_result.py        # 工具标准信封与注册表（前置参数校验 + OpenAI Function Calling 格式）
    ├── context_compressor.py # 上下文压缩器（长任务防止上下文膨胀）
    ├── todo_store.py         # Todo 任务跟踪（LLM 自主维护任务清单）
    ├── interruption.py       # 真打断-恢复任务栈（LIFO，支持多层嵌套）
    ├── subagent.py           # 子 Agent 并行执行器（delegate 工具）
    ├── browser.py            # 浏览器自动化（CDP Proxy）
    ├── mp_client.py          # 微信公众号 API 封装（粉丝消息/客服消息）
    ├── channel.py            # 消息通道抽象层（企微 / 公众号）
    ├── care_scanner.py       # 隐性基线扫描（健康/安全/情绪三维）
    ├── voice.py              # 语音处理（ASR + TTS）
    ├── memory.py             # 五层记忆系统（L1-L4 + 创作空间 L5 + 家庭共享 L3）
    ├── wechat.py             # 企微 API 封装
    ├── llm_client.py         # LLM 通用适配层（OpenAI-compatible，支持 Tool Calling）
    ├── scheduler.py          # 定时任务（早安/午间/晚间/早报/晚报）
    ├── companion_routines.py # 陪伴面仪式（早安/午间/晚间 + 早报/晚报推送）
    ├── news_briefing.py      # 今日简报抓取（tophub.today 早报/晚报）
    ├── intent_router.py      # 意图路由（L1 关键词匹配 simple vs complex）
    ├── analytics.py          # DuckDB 分析层（Agent 自省）
    ├── embedder.py           # BGE 本地 Embedding
    ├── knowledge.py          # 知识库（向量检索）
    ├── vector_store.py       # PGVector 封装
    └── association_engine.py     # 关联引擎（头晕→血压+用药+天气）
```

## 快速启动

### 1. 前置依赖

```bash
# macOS
# - Docker Desktop（跑 PostgreSQL）
# - Python 3.11+（mlx-qwen3-asr 需要 3.10+）
# - FFmpeg（语音转码：brew install ffmpeg）
# - LM Studio（本地 LLM，默认端口 1234）
# - cloudflared（固定 Tunnel）

# 克隆项目后进入目录
cd ~/family-mind
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的真实配置：
# - LLM_BASE_URL=http://127.0.0.1:1234/v1（主，LM Studio 本地）
#   或 https://api.deepseek.com（备选，DeepSeek API）
#   或 https://dashscope.aliyuncs.com/compatible-mode/v1（备选，百炼）
# - LLM_API_KEY=lm-studio（LM Studio 本地）或你的 API Key
# - MODEL_DAILY=qwen/qwen3.6-35b-a3b（主模型，LM Studio）
# - MODEL_COMPLEX=qwen/qwen3.6-35b-a3b
# - MODEL_SUMMARY=qwen/qwen3.6-35b-a3b
# - REASONING_EFFORT=high（DeepSeek thinking mode，仅 DeepSeek 启用时有效）
# - WECHAT_CORPID / AGENTID / SECRET / TOKEN / AESKEY（企微后台）
#
# 注意：企微成员账号需填入数据库
#   `user_profiles` 表的 `wechat_user_id` 字段必须是企微管理后台中的
#   真实成员账号（如 ZhangSan），不能直接用内部 family_id（如 grandpa）。
```

#### 注意：Python 版本

mlx-qwen3-asr 需要 Python 3.10+。如果系统默认是 3.9：

```bash
brew install python@3.11
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 启动 PostgreSQL

```bash
docker-compose up -d db
```

#### 注意：端口冲突检查

启动前确认 5432 端口未被其他 PostgreSQL 实例占用：

```bash
lsof -i :5432
```

如果输出中有**非 Docker** 的 PostgreSQL 进程（如本地 pg0 管理的实例），需先终止，否则 FamilyMind 会连接到错误的数据库并报密码认证失败。

### 4. 启动 FamilyMind（Web 服务）

```bash
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
# 或如果使用 venv：
# venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### 4a. CLI 模式（开发调试）

```bash
# 交互模式
python3 cli.py

# 单次命令
python3 cli.py "查一下天气"
```

### 4b. 外挂大脑（Streamlit 管理界面）

```bash
# 启动管理后台（端口 8501）
streamlit run dashboard.py --server.port 8501
# 或如果使用 venv：
# venv/bin/streamlit run dashboard.py --server.port 8501
```

浏览器访问 `http://localhost:8501`。

功能：
- **今日托付**：每天早上录入今日重点关注
- **晚间简报**：查看 Agent 自动生成的家庭叙事
- **紧急插话**：发送高优先级指令，Agent 下次交互立即响应
- **记忆透明**：查看/删除老人的记忆条目
- **文档上传**：拖拽 PDF/txt/md 到知识库

可选：设置环境变量 `DASHBOARD_PASSWORD=你的密码` 开启密码保护。

### 5. 启动 Cloudflare Tunnel（另一个终端）

```bash
cloudflared tunnel run familymind
```

隧道配置（`~/.cloudflared/config.yml`）：
- `wechat.peistock.win` → `http://localhost:8001`（FamilyMind Web 服务）
- `searxng.peistock.win` → `http://localhost:8080`（SearXNG 搜索服务）

### 6. 配置企微回调

在企微后台「接收消息」中配置：
- URL：`https://wechat.peistock.win/wechat`
- Token：与 .env 中 WECHAT_TOKEN 一致
- EncodingAESKey：与 .env 中 WECHAT_AESKEY 一致

### 7. 测试

在企微家庭群里发消息，Bot 应在几秒内回复。

## 下一步（Phase 3 TTS + Phase 4-5）

- Phase 3 剩余：Edge-TTS 语音合成（刚子AI分身用语音回复老人）
- Phase 4-5 见 `CLAUDE.md` 中的「实施路线」。在本地 Claude Code 中打开本项目继续开发：

```bash
cd ~/family-mind
claude
```

## 成本

- 大模型 API：**¥0/月**（LM Studio 本地 qwen3.6-35b-a3b，零 API 费用）
- 上下文压缩：**¥0/月**（本地 Gemma 4 26B，零 API 费用）
- mlx-qwen3-asr：¥0（本地 MLX 加速）
- Edge-TTS：¥0（微软免费）
- 企微：¥0（个人团队/企业认证后免费额度）
- Cloudflare Tunnel：¥0（免费固定域名）
- PostgreSQL：¥0（Docker 本地运行）
- **总计：¥0/月**（全部本地运行，无 API 费用）

> 备选：DeepSeek v4-flash / 百炼 qwen3.6-plus 在 `.env` 中切换 BASE_URL 即可启用，按 token 计费约 ¥30-100/月。
