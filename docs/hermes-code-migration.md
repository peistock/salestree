# Hermes Agent 代码搬运分析

> 分析时间：2026-07-10
> 目标仓库：`NousResearch/hermes-agent`（212k stars，MIT 协议）
> 目的：评估哪些代码值得搬运到销销，哪些不该搬

---

## 一、Hermes Agent 概况

| 项目 | 信息 |
|------|------|
| 仓库 | `NousResearch/hermes-agent` |
| License | MIT（可商用、可修改、可再分发） |
| Stars | 212k |
| 语言 | Python |
| 定位 | Self-improving AI agent，支持 TUI/Telegram/Discord/Slack/WhatsApp |
| 核心特性 | Tool Calling while 循环、子 Agent 委托、上下文压缩、自动技能创建、跨会话记忆 |

### 销销已有的借鉴

销销代码中已标注从 Hermes 借鉴的部分：

| 文件 | 借鉴内容 | 注释 |
|------|---------|------|
| `mind/agent_loop.py:79` | Tool Calling while 循环 | "Hermes 模式适配版" |
| `mind/tool_result.py:142` | 参数类型强制转换 | "来自 Hermes" |
| `mind/todo_store.py:2` | Todo 工具核心逻辑 | "照搬 Hermes todo_tool.py" |
| `mind/subagent.py:3` | 子 Agent 并行执行 | "借鉴 Hermes delegate_tool.py" |
| `mind/interruption.py:2` | 打断-恢复机制 | "借鉴 Hermes" |

---

## 二、Hermes 核心模块对比

### 2.1 上下文压缩器

| 维度 | 销销 `context_compressor.py`（243 行） | Hermes `context_compressor.py`（3168 行） |
|------|---------------------------------------|------------------------------------------|
| 压缩触发 | 每 4 轮迭代硬触发 | 按 token 预算动态触发（75% 上下文窗口阈值） |
| 摘要注入角色 | `role: "user"` | `role: "system"` + `[CONTEXT COMPACTION]` 前缀 |
| 工具输出裁剪 | 无 | 压缩前先裁剪工具输出（廉价预处理） |
| 迭代摘要 | 无 | 跨多次压缩保留迭代摘要（`HISTORICAL_TASK_HEADING`） |
| 尾部保护 | 固定 4 条消息 | token 预算动态计算 |
| 头部保护 | 固定 3 条消息 | token 预算动态计算 |
| 压缩 thrash 防护 | 无 | 75% trigger floor（避免反复压缩） |
| 摘要模型 | 本地 Gemma 4 26B | 辅助模型（auxiliary client） |
| Token 估算 | `len(text) * 0.6` | 精确 token 计数 |

**关键差异**：Hermes 的压缩器解决了你审计报告中发现的第 6 个问题——`role: "user"` 注入脏数据。

### 2.2 错误分类器

| 维度 | 销销 `llm_client.py:90-98` | Hermes `error_classifier.py` |
|------|---------------------------|------------------------------|
| 重试判断 | 3 种异常类型 | 10+ 种 FailoverReason 枚举 |
| 覆盖场景 | 超时、限流、404/408/429/500-504 | 超时、限流、模型过载、上下文溢出、认证失败、路由错误、SSL 错误、provider 降级 |
| 重试策略 | 固定 1.5s 等待 | 按错误类型差异化：立即重试 / 指数退避 / 切模型 / 放弃 |
| 降级逻辑 | `_fallback_model` 在 3 个本地模型间切换 | 自动切 provider（OpenAI → Anthropic → 本地） |

**关键差异**：Hermes 的错误分类器解决了审计报告中第 3 个问题——LLM 单点故障无自动降级。

### 2.3 迭代预算管理

| 维度 | 销销 | Hermes `iteration_budget.py` |
|------|------|------------------------------|
| 循环控制 | `max_iterations=50` 硬编码 | 时间预算 + token 预算 + 迭代预算三维控制 |
| 超时处理 | 无 | 迭代预算耗尽时优雅退出 + 状态保存 |
| Token 控制 | 无 | 追踪每轮 token 消耗，接近上限时触发压缩 |

### 2.4 消息清理

| 维度 | 销销 `_sanitize_messages` | Hermes `message_sanitization.py` |
|------|--------------------------|----------------------------------|
| Orphan 修复 | ✅ 有 | ✅ 有 |
| 非 ASCII 清理 | ❌ 无 | ✅ `_sanitize_messages_non_ascii` |
| Surrogate 清理 | ❌ 无 | ✅ `_sanitize_messages_surrogates` |
| 图片剥离 | ❌ 无 | ✅ `_strip_images_from_messages` |
| Tool call 参数修复 | ❌ 无 | ✅ `_repair_tool_call_arguments` |

### 2.5 子 Agent 系统

| 维度 | 销销 `subagent.py`（258 行） | Hermes `delegate_tool.py`（3491 行） |
|------|----------------------------|-------------------------------------|
| 隔离级别 | 独立 Toolkit + work_dir | 独立 terminal session + file ops cache |
| 并行支持 | ThreadPoolExecutor | ThreadPoolExecutor + batch 模式 |
| 超时控制 | ❌ 无 | ✅ FuturesTimeoutError |
| 结果验证 | 无 | 成功/失败分类（但有漏洞 #8037） |
| 工具限制 | 硬编码禁止列表 | 可配置 + 永久黑名单 |
| Credential 隔离 | 无 | 有（但传递了整个 pool，有安全风险） |

**关键差异**：Hermes 有超时控制（`FuturesTimeoutError`），你的没有。这解决了审计报告中第 7 个问题。

### 2.6 Todo 工具

| 维度 | 销销 `todo_store.py` | Hermes `todo_tool.py`（330 行） |
|------|---------------------|--------------------------------|
| 核心逻辑 | 读写 JSON | 读写 JSON |
| Hydration | 有（`format_for_injection`） | 有（`hydrate_todo_state`） |
| 状态追踪 | `status: pending/in_progress/completed` | `status: pending/in_progress/completed` |
| 差异 | 基本相同 | 增加了 paired assistant todo call 限制 |

---

## 三、搬运优先级

### P0 — 立刻搬（修复审计报告硬伤）

| # | 源文件 | 目标文件 | 解决的问题 | 工作量 |
|---|--------|---------|-----------|--------|
| 1 | `agent/error_classifier.py` | `mind/llm_client.py` | LLM 单点故障无自动降级 | 0.5 天 | ✅ 已完成：新增 `FailoverDecision` 分类 + `LLM_FALLBACK_*` 多 provider 自动切换 |
| 2 | `agent/context_compressor.py`（核心逻辑） | `mind/context_compressor.py` | 压缩注入脏数据 + 触发时机错误 | 1 天 | ✅ 已完成：system 角色注入 + token 阈值触发 + 工具输出裁剪 |
| 3 | `agent/iteration_budget.py` | `mind/agent_loop.py` | 迭代无时间/token 控制 | 0.5 天 | ✅ 已完成：新增 `mind/iteration_budget.py`，按时间/token/迭代三维预算控制，耗尽时保存 checkpoint 并优雅退出 |

### P1 — 上线前搬（加强健壮性）

| # | 源文件 | 目标文件 | 解决的问题 | 工作量 |
|---|--------|---------|-----------|--------|
| 4 | `agent/message_sanitization.py`（核心函数） | `mind/agent_loop.py` | 消息清理不完整 | 0.5 天 | ✅ 已完成：新增 `mind/message_sanitization.py`，清理 surrogate/控制字符/图片/工具参数 |
| 5 | `tools/delegate_tool.py`（超时逻辑） | `mind/subagent.py` | 子 Agent 无超时 | 0.5 天 | ✅ 已完成：`future.result(timeout=60)` + `wait()` + `cancel_event`；Coordinator 取消设置 event |

### P2 — 上线后迭代（架构升级参考）

| # | 源文件 | 参考价值 | 说明 |
|---|--------|---------|------|
| 6 | `agent/conversation_loop.py`（5333 行） | 结构参考 | 不照搬，参考其 turn_context、retry_state 设计 |
| 7 | `agent/context_engine.py` | 上下文管理 | 参考其 context window 管理策略 |
| 8 | `agent/memory_manager.py` | 记忆管理 | 参考其 memory context block 构建 |
| 9 | `agent/curator.py` | 技能管理 | 参考其自动技能创建和淘汰机制 |

---

## 四、不该搬的

### 4.1 `conversation_loop.py` 全文（5333 行）

原因：
- 和你的 `agent_loop.py` 逻辑重叠但架构完全不同
- Hermes 跑在自己的终端（TUI），你的跑在企微/Web
- 包含 Codex adapter、Anthropic adapter、Gemini adapter 等多 provider 路由
- 包含 streaming 输出、终端 spinner、slash command
- 直接搬会引入大量不需要的依赖

### 4.2 `delegate_tool.py` 全文（3491 行）

原因：
- 你的 `subagent.py` 258 行已经够用
- Hermes 的子 Agent 有 terminal session 隔离，你不需要
- Hermes 的 credential pool 隔离有安全漏洞（#8037）
- 真正需要的只是超时控制逻辑

### 4.3 任何 TUI/终端相关代码

原因：
- 销销的接入层是企微/Web，不是终端
- TUI 代码（spinner、slash command、terminal UI）完全不适用

---

## 五、搬运操作指南

### 5.1 克隆仓库

```bash
git clone --depth 1 https://github.com/NousResearch/hermes-agent.git /tmp/hermes-agent
```

### 5.2 需要搬运的文件清单

```
/tmp/hermes-agent/agent/error_classifier.py      → 提取分类逻辑
/tmp/hermes-agent/agent/iteration_budget.py       → 提取预算管理
/tmp/hermes-agent/agent/message_sanitization.py   → 提取清理函数
/tmp/hermes-agent/agent/context_compressor.py     → 提取核心压缩逻辑
/tmp/hermes-agent/tools/delegate_tool.py          → 提取超时控制
```

### 5.3 搬运原则

1. **不要全文复制**：每个文件只提取解决具体问题的核心函数
2. **适配销销的架构**：Hermes 用 `AIAgent` 实例，销销用 `Toolkit` + `AgentLoop`
3. **保留销销的接口**：`agent_loop.py` 的 `run()` 方法签名不变
4. **测试覆盖**：搬运后必须跑现有的测试（如果有的话）

### 5.4 具体搬运步骤

**步骤 1：错误分类器**

```bash
# 从 Hermes 提取
grep -A 50 "class FailoverReason" /tmp/hermes-agent/agent/error_classifier.py > /tmp/hermes_failover.py
grep -A 30 "def classify_api_error" /tmp/hermes-agent/agent/error_classifier.py > /tmp/hermes_classify.py
```

集成到 `mind/llm_client.py`：
- 替换 `_should_retry` 为 Hermes 的 `classify_api_error`
- 增加 provider 降级逻辑

**步骤 2：上下文压缩器**

```bash
# 从 Hermes 提取核心逻辑
grep -A 100 "def compress_conversation" /tmp/hermes-agent/agent/context_compressor.py > /tmp/hermes_compress.py
```

集成到 `mind/context_compressor.py`：
- 改压缩触发为 token 阈值
- 改摘要注入角色为 `system`
- 加工具输出裁剪预处理
- 加压缩 thrash 防护

**步骤 3：迭代预算**

```bash
# 从 Hermes 提取
grep -A 80 "class IterationBudget" /tmp/hermes-agent/agent/iteration_budget.py > /tmp/hermes_budget.py
```

集成到 `mind/agent_loop.py`：
- 在循环条件里加时间和 token 预算检查

---

## 六、风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| Hermes 代码依赖复杂 | 中 | 只提取纯函数，不引入 Hermes 的 import 链 |
| 接口不兼容 | 低 | 销销的 `AgentLoop` 和 Hermes 的 `AIAgent` 接口不同，需要适配层 |
| 版本更新冲突 | 低 | 销销 fork 后独立维护，不持续同步 |
| License 合规 | 无 | MIT 协议，保留版权声明即可 |

---

## 七、结论

Hermes Agent 的代码**值得搬**，但要**精准搬运**，不是照搬。

**核心价值**：
1. `error_classifier.py` → 解决 LLM 单点故障
2. `context_compressor.py` → 解决压缩脏数据 + 触发时机
3. `iteration_budget.py` → 解决迭代无预算控制
4. `message_sanitization.py` → 加强消息健壮性

**不要搬**：
- `conversation_loop.py` 全文（架构差异太大）
- `delegate_tool.py` 全文（你的够用）
- TUI/终端相关代码

**建议**：先搬 P0 的 3 个文件（2 天），验证效果后再搬 P1 的 2 个文件（1 天）。
