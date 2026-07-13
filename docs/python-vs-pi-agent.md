# Python Agent vs TypeScript Pi Agent 对比分析

> 分析时间：2026-07-10
> 对比对象：mind/agent_loop.py（Python） vs server/src/agent/Session.ts（TypeScript Pi）

---

## 一、代码量对比

| 模块 | Python (sales-mind) | TypeScript (salestree) | 变化 |
|------|--------------------|-----------------------|------|
| Agent 主循环 | `agent_loop.py` 1236 行 | `Session.ts` 181 行 | **-85%** |
| LLM 客户端 | `llm_client.py` 275 行 | `provider.ts` 233 行 | -15% |
| 工具集 | `tools.py` 1398 行 | `Toolkit.ts` 23 行 + 各工具文件 | 拆分 |
| 子 Agent | `subagent.py` 304 行 | ❌ 未实现 | -100% |
| 打断恢复 | `interruption.py` 209 行 | ❌ 未实现 | -100% |
| 上下文压缩 | `context_compressor.py` 243 行 | ❌ 未实现 | -100% |
| 迭代预算 | `iteration_budget.py` 新增 | ❌ 未实现 | -100% |
| 消息清理 | `message_sanitization.py` 新增 | ❌ 未实现 | -100% |
| 意图路由 | `intent_router.py` 117 行 | ❌ 未实现 | -100% |
| 计划管理 | `plan_store.py` + `plan_tool.py` | ❌ 未实现 | -100% |

**总计**：Python Agent 约 4000+ 行核心逻辑，TypeScript Pi Agent 约 500 行。

---

## 二、功能对比

### 2.1 Pi Agent 有但 Python Agent 没有的

| 功能 | Pi Agent | 说明 |
|------|----------|------|
| 流式输出 | ✅ 原生支持 | `agent.subscribe()` 事件驱动 |
| 消息格式 | ✅ 类型安全 | `AgentMessage` 带 `timestamp` |
| 工具热插拔 | ✅ | `afterToolCall` 钩子 |

### 2.2 Python Agent 有但 Pi Agent 没有的

| 功能 | Python Agent | Pi Agent | 影响 |
|------|-------------|----------|------|
| **意图分流** | ✅ L1 关键词匹配 | ❌ 无 | 简单问题也走完整循环，浪费 token |
| **简单回复快速通道** | ✅ `_simple_reply` 1-3 秒 | ❌ 无 | 所有请求都走 agent.prompt() |
| **上下文压缩** | ✅ 每 4 轮触发 | ❌ 无 | 长对话会撑爆上下文窗口 |
| **迭代预算** | ✅ 迭代/时间/token 三维 | ❌ 只有 `maxTurns=4` | 无法控制 token 消耗 |
| **Guardrail** | ✅ 时效性关键词拦截 | ❌ 无 | LLM 可能凭记忆回答时效性问题 |
| **子 Agent** | ✅ `delegate` 工具 | ❌ 无 | 无法并行处理复杂任务 |
| **打断恢复** | ✅ 任务栈 | ❌ 无 | 长任务被打断后无法恢复 |
| **计划管理** | ✅ `plan` 工具 | ❌ 无 | 多步骤任务无法拆分执行 |
| **Todo 持久化** | ✅ JSON 文件 | ❌ 无 | 重启丢失 |
| **Checkpoint** | ✅ 状态保存/恢复 | ❌ 无 | 崩溃后无法恢复 |
| **心跳提示** | ✅ 60 秒心跳 | ❌ 无 | 用户长时间看不到反馈 |
| **事件追踪** | ✅ `AgentTraceStore` | ❌ 无 | 无法审计 Agent 行为 |
| **并行工具执行** | ✅ `PARALLEL_SAFE_TOOLS` | ❌ 无 | 多个独立工具串行执行 |
| **工具参数修复** | ✅ JSON 修复逻辑 | ❌ 无 | LLM 输出格式错误时崩溃 |
| **Orphan 消息修复** | ✅ `_sanitize_messages` | ❌ 无 | tool_call/tool_result 不匹配时崩溃 |
| **消息去重** | ✅ 企微消息去重 | ❌ 无 | WebSocket 重发导致重复处理 |
| **用户级串行** | ✅ `threading.Lock` | ⚠️ 部分（abort 旧会话） | 并发控制不完整 |
| **LLM 降级** | ✅ `_fallback_model` | ❌ 无 | 本地模型挂了就瘫痪 |
| **错误重试** | ✅ `_call_with_retry` | ❌ 无 | 临时错误直接失败 |
| **快照反思** | ✅ `_reflect_on_past` | ❌ 无 | 模糊请求无法关联历史经验 |
| **文件输出** | ✅ PDF/图片/文件推送 | ❌ 无 | 无法生成报告文件 |
| **语音处理** | ✅ ASR/TTS | ❌ 无 | 无法处理语音消息 |
| **图片处理** | ✅ Vision 分析 | ❌ 无 | 无法处理图片消息 |
| **企微集成** | ✅ 消息收发/加密/重试 | ❌ 无 | 只能通过 Web 使用 |
| **协作面** | ✅ 简报/复盘/插话 | ❌ 无 | 无主动推送能力 |

---

## 三、核心差距量化

| 维度 | Python Agent | Pi Agent | 差距 |
|------|-------------|----------|------|
| 最大迭代次数 | 50 | 4 | **-92%** |
| Token 预算控制 | ✅ 三维 | ❌ 无 | -100% |
| 上下文压缩 | ✅ | ❌ | -100% |
| 工具数量 | 20+ | 12 | -40% |
| 错误恢复 | 重试 + 降级 | try-catch | -80% |
| 状态持久化 | Checkpoint + DB | ❌ 无 | -100% |
| 并发控制 | Lock + 去重 | abort 旧会话 | -60% |

---

## 四、Pi Agent 的架构优势

尽管功能差距大，Pi Agent 在**架构设计**上有优势：

### 4.1 关注点分离

```typescript
// Pi Agent：清晰的职责分离
class AgentSession {
    memory: Memory           // 记忆
    association: Association // 关联
    skillLoader: SkillLoader // 技能
    vectorStore: VectorStore // 知识库
}
```

```python
# Python Agent：职责混杂
class FamilyAgent:
    # system prompt 构建、skill 加载、关联引擎、
    # 知识库检索、对话保存、情绪检测、文件扫描...
    # 全在一个类里
```

### 4.2 依赖注入

```typescript
// Pi：模型、工具、事件处理都是注入的
new Agent({
    initialState: { model, tools, systemPrompt },
    streamFn,
    afterToolCall,
});
```

```python
# Python：硬编码依赖
class AgentLoop:
    def __init__(self, llm, toolkit, todo_store, ...):
        # 7 个构造参数
```

### 4.3 事件驱动

```typescript
// Pi：声明式事件订阅
this.agent.subscribe((event) => {
    if (event.type === "turn_end") { ... }
    if (event.type === "tool_execution_start") { ... }
});
```

```python
# Python：命令式事件发送
def _emit(self, event):
    if self.event_sink:
        self.event_sink(event)
```

---

## 五、Pi Agent 的致命缺陷

### 5.1 上限太低

```typescript
private readonly maxTurns = 4;
```

硬编码 4 轮。Python Agent 是 50 轮。复杂任务（客户研究、报告生成）需要 10-20 轮工具调用，Pi Agent 根本跑不完。

### 5.2 无状态管理

```typescript
// 每次请求都是全新会话
async run(query, send) {
    const promptMessages = [{ role: "user", content: promptText }];
    await this.agent.prompt(promptMessages);
}
```

没有对话历史、没有 checkpoint、没有持久化。用户说"继续"，Agent 完全不知道之前在做什么。

### 5.3 无安全防护

- 没有 Guardrail（时效性拦截）
- 没有工具参数修复
- 没有 orphan 消息修复
- 没有消息去重

LLM 输出任何格式都会直接传给工具执行，格式错误就崩溃。

---

## 六、结论

### Pi Agent vs Python Agent 的本质差异

| 维度 | Python Agent | Pi Agent |
|------|-------------|----------|
| **设计哲学** | 生产级，功能完整 | 原型级，快速验证 |
| **代码质量** | 有 bug 但功能全 | 架构干净但功能缺 |
| **可维护性** | 差（1236 行大函数） | 好（181 行小类） |
| **可扩展性** | 差（硬编码多） | 好（依赖注入） |
| **生产就绪** | 70% | 30% |

### 建议

**不要在 Pi 上从零重写 Python Agent 的所有功能。** 应该：

1. **保留 Pi 的架构优势**（关注点分离、依赖注入、事件驱动）
2. **补齐 Pi 缺失的核心能力**：
   - 上下文压缩（从 Hermes 搬）
   - 迭代预算（从 Hermes 搬）
   - 意图分流（从 Python 搬）
   - Checkpoint（自己实现）
3. **砍掉不需要的功能**：
   - 心跳话术池（120 行废话）
   - `_reflect_on_past`（基本没用）
   - 公众号身份识别（已经废弃）

**最终目标**：用 Pi 的干净架构 + Python 的生产级能力，得到一个既好维护又可靠的系统。
