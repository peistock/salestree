"""
Agent 执行循环（Tool Calling while 循环）

从 FamilyAgent 中提取的纯循环逻辑，不处理：
- system prompt 构建
- skill / association / knowledge 前置注入
- 对话保存、情绪检测、文件扫描
- 打断恢复的外层逻辑

职责：接收初始 AgentMessage 列表，运行 Tool Calling while 循环，返回最终结果。
"""
import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable, List, Tuple

from mind.tool_result import ToolResult
from mind.todo_store import TodoStore, todo_tool
from mind.plan_store import PlanStore
from mind.plan_tool import plan_tool
from mind.context_compressor import compress_messages, should_compress
from mind.iteration_budget import build_budget, IterationBudget
from mind.message_sanitization import sanitize_messages
from mind.agent_events import AgentEvent, AgentEventType
from mind.agent_message import AgentMessage
from mind.agent_trace import AgentTraceStore

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 60  # 秒

# 触发 Plan 模式的关键词（用户查询包含这些词时，建议先制定计划）
_PLANNING_KEYWORDS = {
    "分析", "研究", "调研", "报告", "深度", "万字", "完整版",
    "详细分析", "全面分析", "深入研究", "深入分析",
}

# 触发 Plan 模式的 Skill（加载这些 skill 时自动启用 Plan 模式）
_PLANNING_SKILLS = {"hv-analysis", "deep-analysis", "khazix-writer", "ppt-master"}

# Guardrail：涉及时效性内容的关键词（用户查询包含这些词时，必须调用工具获取实时信息）
_TIME_SENSITIVE_KEYWORDS = {
    "新闻", "热点", "政策", "法规", "最新", "今天", "最近", "现今", "目前", "当前",
    "股价", "行情", "涨跌", "涨幅", "跌幅", "市值", "市盈率", "市净率", "股息率", "估值",
    "天气", "气温", "温度", "下雨", "下雪", "台风", "雾霾",
    "财报", "年报", "季报", "半年报", "业绩", "净利润", "营收", "收入", "利润",
    "汇率", "利率", "油价", "金价", "房价", "物价",
    "票房", "销量", "销售额", "市场份额", "排名",
    "确诊", "病例", "疫情", "疫苗", "死亡", "感染",
    "比赛", "赛事", "比分", "进球", "夺冠", "奥运会", "世界杯",
    "选举", "投票", "民调", "就任", "卸任",
    "发布", "推出", "上市", "开售", "预售",
    "地震", "洪水", "火灾", "事故", "灾害",
}

# Guardrail：信息获取类工具（调用过这些工具才算"已获取实时信息"）
_INFO_TOOLS = {
    "search_web", "fetch_webpage", "jina_reader",
    "browse_open", "browse_text", "find_chrome_url", "search_knowledge",
}

# 允许并行执行的工具白名单（无状态、无相互依赖）
PARALLEL_SAFE_TOOLS = {
    "search_knowledge",
    "search_web",
    "read_file",
    "list_dir",
    "get_time",
    "fetch_webpage",
    "jina_reader",
    "find_chrome_url",
    "browse_open",
    "browse_text",
}


class AgentLoop:
    """Tool Calling while 循环（Hermes 模式适配版）"""

    def __init__(
        self,
        llm,
        toolkit,
        todo_store: TodoStore,
        work_dir,
        event_sink: Optional[Callable[[AgentEvent], None]] = None,
        trace_store: Optional[AgentTraceStore] = None,
        plan_store: Optional[PlanStore] = None,
        coordinator=None,
        current_task_id: Optional[str] = None,
    ):
        self.llm = llm
        self.toolkit = toolkit
        self.todo_store = todo_store
        self.work_dir = work_dir
        self.event_sink = event_sink
        self.trace_store = trace_store
        self.plan_store = plan_store
        self.coordinator = coordinator
        self.current_task_id = current_task_id

    def _emit(self, event: AgentEvent) -> None:
        if self.event_sink:
            try:
                self.event_sink(event)
            except Exception as e:
                logger.warning(f"事件发送失败: {e}")

    def _stream_reply(self, text: str) -> str:
        """把最终回复以 token 事件形式流式推送，返回原文本"""
        if not text or not self.event_sink:
            return text
        # 按字符流式发送，每 2-4 个字符一组，兼顾视觉效果与性能
        chunk_size = 3
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i + chunk_size]
            self._emit(AgentEvent(type=AgentEventType.TOKEN, message=chunk))
            # 小延迟让前端有逐字出现的感觉；短文本加速
            if len(text) > 200:
                time.sleep(0.015)
            else:
                time.sleep(0.005)
        return text

    def run(
        self,
        messages: List[AgentMessage],
        max_iterations: int = 50,
        start_iteration: int = 0,
        checkpoint_path=None,
        cancel_event: Optional[threading.Event] = None,
    ) -> dict:
        """
        执行 Tool Calling while 循环。

        参数:
            messages: 包含 system prompt 的初始 AgentMessage 列表
            max_iterations: 最大迭代次数
            start_iteration: 起始迭代次数（checkpoint 恢复用）
            checkpoint_path: checkpoint 文件路径，None 则不保存
            cancel_event: 外部取消信号，设置后循环会尽快退出

        返回:
            {
                "reply": str,
                "final_messages": List[AgentMessage],
                "care_signals": list,
                "iteration": int,
            }
        """
        # ====== L1 意图分流 ======
        intent, confidence = self._classify_intent(messages)
        if intent == "simple" and start_iteration == 0:
            logger.info(f"[IntentRouter] 简单意图分流 (conf={confidence:.0%})，走快速通道")
            return self._simple_reply(messages)
        elif intent == "complex":
            logger.info(f"[IntentRouter] 复杂意图 (conf={confidence:.0%})，走 Agent 循环")

        tools = self.toolkit.schema()
        care_signals = []
        final_reply = ""
        last_heartbeat = time.time()

        # 三维预算：迭代 / 时间 / token
        budget = build_budget(max_iterations=max_iterations)
        logger.info(f"[AgentLoop] 预算: {budget.summary()}")

        # Evaluator 反馈循环控制
        eval_feedback_count = 0
        MAX_EVAL_FEEDBACK = 2

        # Verify 阶段反馈循环控制
        verify_feedback_count = 0
        MAX_VERIFY_FEEDBACK = 1

        # 进度话术池（随机轮换，避免重复）
        _HB_IN_PROGRESS = [
            "销销还在忙～正在做：{task}...（已完成 {completed}/{total}）",
            "进度更新：销销正在弄 {task}...，已完成 {completed}/{total} 项",
            "还在加油干～销销在做 {task}...，进度 {completed}/{total}",
            "销销没偷懒哈～{task}... 进行中，已完成 {completed}/{total}",
            "再等等哦～销销正在 {task}...，已完成 {completed}/{total} 项",
            "销销这边正在 {task}...，进度 {completed}/{total}，快了快了～",
            "销销在忙 {task}...，进度 {completed}/{total}，还在努力～",
            "正在进行 {task}...，已完成 {completed}/{total}，销销没停～",
            "销销正在全力处理 {task}...，进度 {completed}/{total}～",
            "还在做 {task}...，已完成 {completed}/{total}，销销加油中～",
            "销销正在攻克 {task}...，进度 {completed}/{total}，请稍候～",
            "忙着呢～{task}... 进行中，已完成 {completed}/{total} 项",
            "销销正在认真处理 {task}...，进度 {completed}/{total}，快了～",
            "还在弄 {task}...，已完成 {completed}/{total}，销销不偷懒～",
            "销销正在执行 {task}...，进度 {completed}/{total}，持续推进中～",
            "进行中～{task}...，已完成 {completed}/{total}，销销在努力～",
            "销销正在埋头苦干 {task}...，进度 {completed}/{total}，请稍等～",
            "还在处理 {task}...，已完成 {completed}/{total}，销销加油～",
            "销销正在专心做 {task}...，进度 {completed}/{total}，马上推进下一步～",
            "销销正在赶 {task}...，进度 {completed}/{total}，没闲着～",
            "销销没停～{task}... 进度 {completed}/{total}，持续推进中～",
        ]
        _HB_PENDING = [
            "销销还在忙～准备做：{task}...（已完成 {completed}/{total}）",
            "前面的做完了，接下来准备 {task}...，已完成 {completed}/{total}",
            "进度 {completed}/{total}，马上开始 {task}...",
            "销销整理完手头这点，就去 {task}...，已完成 {completed}/{total}",
            "前面的搞定了，接下来轮到 {task}...，已完成 {completed}/{total}",
            "销销正准备开始 {task}...，已完成 {completed}/{total} 项",
            "马上要做 {task}...，已完成 {completed}/{total}，销销在准备～",
            "前面的收工了，接下来 {task}...，已完成 {completed}/{total}",
            "销销准备启动 {task}...，已完成 {completed}/{total} 项",
            "接下来是 {task}...，已完成 {completed}/{total}，马上开始～",
            "销销正准备 {task}...，已完成 {completed}/{total}，衔接中～",
            "前面的完成了，销销要去 {task}...，已完成 {completed}/{total}",
            "准备开始 {task}...，已完成 {completed}/{total}，销销在热身～",
            "销销接下来要忙 {task}...，已完成 {completed}/{total} 项",
            "前面告一段落，接下来 {task}...，已完成 {completed}/{total}",
            "销销正准备进入 {task}...，已完成 {completed}/{total}，衔接顺畅～",
            "马上轮到 {task}...，已完成 {completed}/{total}，销销准备开工～",
            "前面的结束了，接下来 {task}...，已完成 {completed}/{total}",
            "销销准备做 {task}...，已完成 {completed}/{total} 项，马上开始～",
            "销销要去处理 {task}...，已完成 {completed}/{total}，衔接中～",
            "销销收拾完手头的，就去 {task}...，已完成 {completed}/{total}～",
        ]
        _HB_WRAP_UP = [
            "销销还在整理，马上就好～",
            "快好了快好了，销销再整理一下～",
            "收尾阶段啦，销销马上给您结果～",
            "最后几步了，稍等片刻～",
            "销销在最后检查一遍，马上出结果～",
            "马上出炉，销销在最后润色～",
            "销销在打包整理，一会儿就给您～",
            "快啦快啦，销销在最后收尾～",
            "销销在最后核对一下，马上呈上～",
            "最后整理中，销销保证质量～",
            "马上搞定，销销在最后检查～",
            "销销在收尾了，结果马上出来～",
            "快好了，销销在最后汇总一下～",
            "销销在最后完善，马上给您完整的～",
            "最后冲刺啦，销销马上交作业～",
            "销销在最后排版整理，一会儿就好～",
            "马上好了，销销在最后确认一遍～",
            "销销在做最后的收尾工作，请稍等～",
            "快出结果了，销销在最后检查细节～",
            "销销快完成了，最后整理一下给您～",
            "销销在扫尾，马上给您最终结果～",
        ]

        def _send_heartbeat():
            # 从最近一轮的工具执行中提取有价值的信息
            tool_summary = ""
            last_tool_name = ""
            last_tool_result = ""
            # 倒序遍历 messages，找最近一轮的 tool 结果
            for msg in reversed(messages):
                if msg.role == "tool" and msg.content:
                    content = msg.content[:200]
                    # 找到对应的 tool_call（往前找 assistant tool_calls）
                    for prev_msg in reversed(messages[:messages.index(msg)]):
                        if prev_msg.role == "assistant" and prev_msg.tool_calls:
                            for tc in prev_msg.tool_calls:
                                if tc.get("id") == msg.tool_call_id:
                                    last_tool_name = tc.get("function", {}).get("name", "")
                                    last_tool_result = content
                                    break
                        if last_tool_name:
                            break
                    if last_tool_name:
                        break

            # 根据工具类型生成有意义的摘要
            if last_tool_name == "write_file":
                # 从结果中提取文件大小或章节信息
                if "字符" in last_tool_result or "已写入" in last_tool_result:
                    tool_summary = "正在写报告，" + last_tool_result.replace("已写入", "").strip()[:50]
                else:
                    tool_summary = "正在整理报告内容..."
            elif last_tool_name in ("search_web", "multi_search"):
                # 提取搜索结果中的关键发现
                if "搜索" in last_tool_result:
                    # 提取第一条结果的标题
                    lines = [l.strip() for l in last_tool_result.split("\n") if l.strip() and not l.strip().startswith("搜索")]
                    if lines:
                        first_result = lines[0][:40]
                        tool_summary = f"刚搜到：{first_result}..."
                    else:
                        tool_summary = "正在搜索信息..."
                else:
                    tool_summary = "正在搜索信息..."
            elif last_tool_name in ("fetch_webpage", "jina_reader"):
                # 提取正在阅读的页面标题
                if "标题" in last_tool_result:
                    title_start = last_tool_result.find("标题") + 3
                    title_end = last_tool_result.find("\n", title_start)
                    if title_end == -1:
                        title_end = len(last_tool_result)
                    title = last_tool_result[title_start:title_end].strip()[:40]
                    tool_summary = f"正在阅读：{title}..."
                else:
                    tool_summary = "正在读取页面内容..."
            elif last_tool_name == "browse_open":
                if "打开" in last_tool_result and "失败" not in last_tool_result:
                    tool_summary = "正在浏览网页获取信息..."
                else:
                    tool_summary = "正在打开网页..."
            elif last_tool_name == "delegate":
                tool_summary = "正在并行处理多个子任务..."

            # 优先使用 Plan 进度生成进度消息
            if self.plan_store and self.plan_store.is_active():
                progress = self.plan_store.get_progress()
                current_desc = progress.get("current_desc", "处理中")
                completed = progress.get("completed", 0)
                total = progress.get("total_steps", 0)
                current_step = progress.get("current_step", 1)

                if current_step <= total:
                    base_msg = f"第 {current_step}/{total} 步：{current_desc[:30]}"
                    if tool_summary:
                        msg = f"{base_msg} | {tool_summary}"
                    else:
                        msg = base_msg
                else:
                    msg = random.choice(_HB_WRAP_UP)
                    if tool_summary:
                        msg = f"{msg} | {tool_summary}"
                self._emit(AgentEvent(type=AgentEventType.HEARTBEAT, message=msg))
                return

            # fallback 到 todo-based heartbeat
            todos = self.todo_store.read()
            in_progress = [t for t in todos if t.get("status") == "in_progress"]
            pending = [t for t in todos if t.get("status") == "pending"]
            completed = [t for t in todos if t.get("status") == "completed"]
            if in_progress:
                base_msg = f"正在做：{in_progress[0]['content'][:30]}...（已完成 {len(completed)}/{len(todos)} 项）"
            elif pending:
                base_msg = f"准备做：{pending[0]['content'][:30]}...（已完成 {len(completed)}/{len(todos)} 项）"
            else:
                base_msg = random.choice(_HB_WRAP_UP)

            if tool_summary:
                msg = f"{base_msg} | {tool_summary}"
            else:
                msg = base_msg
            self._emit(AgentEvent(type=AgentEventType.HEARTBEAT, message=msg))

        try:
            self._emit(AgentEvent(type=AgentEventType.AGENT_START, resumed=(start_iteration > 0)))

            # 模糊请求自动反思：首轮前查询历史经验
            if start_iteration == 0:
                reflection = self._reflect_on_past(messages)
                if reflection:
                    messages.append(AgentMessage.assistant(content=reflection))
                    logger.info("[Reflect] 模糊请求触发自我观察，已注入历史经验")

            for iteration in range(start_iteration, max_iterations):
                if cancel_event and cancel_event.is_set():
                    logger.info("[AgentLoop] 收到取消信号，退出循环")
                    self._emit(AgentEvent(type=AgentEventType.AGENT_END, message="已取消"))
                    return {
                        "reply": "已停止生成。你可以直接修改问题后重新发送。",
                        "final_messages": messages,
                        "care_signals": care_signals,
                        "iteration": iteration,
                    }

                # 三维预算检查：时间 / token / 迭代
                llm_messages = [m.to_llm() for m in messages]
                stop_reason = budget.check(iteration, llm_messages)
                if stop_reason:
                    budget.mark_stopped(stop_reason)
                    if checkpoint_path:
                        self._save_checkpoint(messages, iteration, checkpoint_path)
                    logger.warning(f"[AgentLoop] 预算耗尽 ({stop_reason})，优雅退出")
                    self._emit(AgentEvent(
                        type=AgentEventType.AGENT_END,
                        iteration=iteration,
                        message=f"预算耗尽: {stop_reason}",
                    ))
                    return {
                        "reply": self._build_budget_exhausted_reply(stop_reason),
                        "final_messages": messages,
                        "care_signals": care_signals,
                        "iteration": iteration,
                    }

                self._emit(AgentEvent(type=AgentEventType.TURN_START, iteration=iteration))

                now = time.time()
                if now - last_heartbeat > HEARTBEAT_INTERVAL:
                    _send_heartbeat()
                    last_heartbeat = now

                # Plan 阶段：复杂任务首轮且未制定计划时，提醒 LLM 先调用 plan 工具
                self._maybe_inject_plan_reminder(messages, iteration)

                messages = self._sanitize_messages(messages)

                # 注入 Plan 进度提醒（如果存在活跃计划）
                if self.plan_store and self.plan_store.is_active():
                    plan_injection = self.plan_store.format_for_injection()
                    if plan_injection:
                        # 找到最后一条 system 消息的位置，在其后插入 plan 提醒
                        # 使用 assistant 角色插入，避免污染 system prompt 结构
                        messages.append(AgentMessage.assistant(
                            content=f"【系统提醒】{plan_injection}"
                        ))

                # 在 LLM API 边界处转换为标准 dict 格式，并做 Hermes 风格清理
                llm_messages = [m.to_llm() for m in messages]
                llm_messages = sanitize_messages(llm_messages)
                resp = self.llm.chat_with_tools(
                    messages=llm_messages,
                    tools=tools,
                    model=self.llm.model_complex,
                    max_tokens=8192,
                    temperature=0.3,
                )

                if not resp or not resp.choices:
                    final_reply = "销销出了点问题，请您稍后再试。"
                    self._emit(AgentEvent(type=AgentEventType.TURN_END, iteration=iteration))
                    break

                msg = resp.choices[0].message

                if getattr(msg, "tool_calls", None):
                    valid_tool_calls = []
                    for tc in msg.tool_calls:
                        raw_args = tc.function.arguments
                        clean_args = self._clean_tool_arguments(raw_args)
                        if clean_args is not None:
                            valid_tool_calls.append({
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": clean_args,
                                },
                            })
                        else:
                            err_msg = f"工具参数不是合法 JSON：{raw_args[:200]!r}"
                            logger.error(err_msg)
                            messages.append(AgentMessage.tool(
                                content=f"错误：{err_msg}。请重新调用此工具，确保 arguments 是合法 JSON 字符串（换行用 \\n，引号用 \\\" 转义）。",
                                tool_call_id=tc.id,
                            ))

                    if valid_tool_calls:
                        messages.append(AgentMessage.assistant(
                            content=msg.content or "",
                            tool_calls=valid_tool_calls,
                            reasoning_content=getattr(msg, "reasoning_content", None),
                        ))

                    # 准备可执行任务列表
                    executables: list[Tuple] = []
                    for tc in msg.tool_calls:
                        tool_name = tc.function.name
                        clean_args = self._clean_tool_arguments(tc.function.arguments)
                        if clean_args is None:
                            continue
                        args = json.loads(clean_args)
                        executables.append((tc, tool_name, args))

                    # 判断是否可以并行：全部在白名单内且数量 > 1
                    can_parallel = (
                        len(executables) > 1
                        and all(name in PARALLEL_SAFE_TOOLS for _, name, _ in executables)
                    )

                    if can_parallel:
                        # 并行执行
                        with ThreadPoolExecutor(max_workers=len(executables)) as executor:
                            futures = {
                                executor.submit(
                                    self._execute_single_tool, tc, tool_name, args, iteration
                                ): tc
                                for tc, tool_name, args in executables
                            }
                            for future in futures:
                                tc_id, result_text, signals = future.result()
                                care_signals.extend(signals)
                                messages.append(AgentMessage.tool(
                                    content=result_text,
                                    tool_call_id=tc_id,
                                ))
                    else:
                        # 顺序执行（保持原有行为）
                        for tc, tool_name, args in executables:
                            tc_id, result_text, signals = self._execute_single_tool(
                                tc, tool_name, args, iteration
                            )
                            care_signals.extend(signals)
                            messages.append(AgentMessage.tool(
                                content=result_text,
                                tool_call_id=tc_id,
                            ))

                    # Plan 自动推进：如果执行了当前步骤预期的工具，标记步骤完成
                    self._advance_plan_if_applicable(executables)

                    # 上下文压缩：按 token 阈值触发，而非固定迭代次数
                    dict_messages = [m.to_llm() for m in messages]
                    if should_compress(dict_messages):
                        todo_injection = self.todo_store.format_for_injection()
                        compressed = compress_messages(
                            messages=dict_messages,
                            llm_chat_fn=self.llm.chat,
                            todo_injection=todo_injection,
                        )
                        if compressed is not dict_messages:
                            messages = [AgentMessage.from_llm(m) for m in compressed]
                            logger.info(f"迭代 {iteration} 完成上下文压缩")
                    else:
                        logger.debug(f"迭代 {iteration} 未达压缩阈值，跳过")

                    if checkpoint_path:
                        self._save_checkpoint(messages, iteration + 1, checkpoint_path)

                    self._emit(AgentEvent(type=AgentEventType.TURN_END, iteration=iteration))
                    continue

                else:
                    # Guardrail：涉及时效性内容但未调用工具 → 强制要求搜索
                    # 只在第 0 轮触发：LLM 已在执行中间步骤时不打断（避免读取 skill/文件后被反复拦截）
                    if iteration == 0 and self._guardrail_requires_tools(messages):
                        logger.warning(f"[Guardrail] 迭代 {iteration}：涉及时效性内容但未调用工具，强制要求重新搜索")
                        messages.append(AgentMessage.assistant(
                            content="我需要先搜索一下最新信息才能准确回答您的问题。"
                        ))
                        self._emit(AgentEvent(type=AgentEventType.TURN_END, iteration=iteration))
                        continue

                    final_content = msg.content or "销销想了想，但是有点糊涂了..."

                    # Evaluator：输出质量自检，在流式输出前拦截低质量回复
                    if final_content and len(final_content) > 200:
                        eval_issues = self._evaluate_output(final_content)
                        if eval_issues:
                            eval_feedback_count += 1
                            if eval_feedback_count <= MAX_EVAL_FEEDBACK:
                                logger.warning(f"[Evaluator] 检测到问题 (第{eval_feedback_count}/{MAX_EVAL_FEEDBACK}次): {eval_issues}")
                                messages.append(AgentMessage.system(
                                    content=f"【系统自检】上次回复存在以下问题，请修正：{eval_issues}。如需补充信息，请调用工具。"
                                ))
                                self._emit(AgentEvent(type=AgentEventType.TURN_END, iteration=iteration))
                                continue
                            else:
                                logger.warning(f"[Evaluator] 问题仍存在，但已达到最大反馈次数 {MAX_EVAL_FEEDBACK}，返回当前结果")

                    # Verify：基于目标/计划的最终校验（Plan → Execute → Verify 的 Verify 阶段）
                    if final_content and verify_feedback_count < MAX_VERIFY_FEEDBACK:
                        verify_issues = self._verify_result(messages, final_content)
                        if verify_issues:
                            verify_feedback_count += 1
                            logger.warning(f"[Verify] 检测到问题: {verify_issues}")
                            messages.append(AgentMessage.system(
                                content=f"【结果校验】当前回复尚未充分满足需求：{verify_issues}。请补充信息或修正回答。"
                            ))
                            self._emit(AgentEvent(type=AgentEventType.TURN_END, iteration=iteration))
                            continue

                    final_reply = self._stream_reply(final_content)
                    self._emit(AgentEvent(type=AgentEventType.TURN_END, iteration=iteration))
                    break

            else:
                final_reply = self._build_max_iter_reply()

            self._emit(AgentEvent(
                type=AgentEventType.AGENT_END,
                iteration=iteration if "iteration" in dir() else 0,
            ))

        except Exception as e:
            logger.error(f"Tool Calling 循环异常: {e}", exc_info=True)
            final_reply = "销销出了点问题，请您稍后再试，或者联系管理员帮忙看看。"
            try:
                if checkpoint_path:
                    self._save_checkpoint(
                        messages, iteration if "iteration" in dir() else 0, checkpoint_path
                    )
            except Exception as cp_err:
                logger.warning(f"异常时保存 checkpoint 失败: {cp_err}")

        return {
            "reply": final_reply,
            "final_messages": messages,
            "care_signals": care_signals,
            "iteration": iteration if "iteration" in dir() else 0,
        }

    def _execute_single_tool(self, tc, tool_name: str, args: dict, iteration: int) -> Tuple[str, str, list]:
        """执行单个工具调用，返回 (tool_call_id, result_text, care_signals)。"""
        logger.info(f"[AgentLoop] 工具执行开始: {tool_name}(args={args}), iteration={iteration}")
        self._emit(AgentEvent(
            type=AgentEventType.TOOL_EXECUTION_START,
            tool=tool_name,
            step=iteration + 1,
            iteration=iteration,
        ))

        signals = []
        t0 = time.time()
        if tool_name == "todo":
            result_text = todo_tool(
                todos=args.get("todos"),
                merge=args.get("merge", False),
                store=self.todo_store,
            )
        elif tool_name == "plan":
            result_text = plan_tool(
                steps=args.get("steps", []),
                reason=args.get("reason", ""),
                store=self.plan_store,
            )
            # 如果计划刚创建，发送 PLAN_CREATED 事件
            if self.plan_store and self.plan_store.is_active():
                progress = self.plan_store.get_progress()
                steps = self.plan_store._plan.get("steps", [])
                self._emit(AgentEvent(
                    type=AgentEventType.PLAN_CREATED,
                    total=progress.get("total_steps", 0),
                    message=f"计划已创建：共 {progress.get('total_steps', 0)} 步",
                    steps_summary=[s["description"] for s in steps],
                ))
                # 同时发送 STEP_START 事件（第一步开始）
                if steps:
                    self._emit(AgentEvent(
                        type=AgentEventType.STEP_START,
                        step=1,
                        total=progress.get("total_steps", 0),
                        description=steps[0]["description"],
                    ))
        elif tool_name == "delegate":
            from mind.subagent import run_subagents
            sub_tasks = args.get("tasks", [])
            if sub_tasks:
                result_text = run_subagents(
                    tasks=sub_tasks,
                    tools=self.toolkit.schema(),
                    llm=self.llm,
                    coordinator=self.coordinator,
                )
            else:
                result_text = "错误：delegate 工具需要提供 tasks 参数"
        elif tool_name == "spawn_task":
            from mind.coordinator_tools import spawn_task_tool
            if not self.coordinator:
                result_text = "错误：spawn_task 需要 Coordinator 支持（当前未启用 Coordinator 模式）"
            else:
                result_text = spawn_task_tool(
                    goal=args.get("goal", ""),
                    task_type=args.get("task_type", "research"),
                    dependencies=args.get("dependencies"),
                    coordinator=self.coordinator,
                    parent_task_id=self.current_task_id,
                )
        elif tool_name == "get_task_status":
            from mind.coordinator_tools import get_task_status_tool
            if not self.coordinator:
                result_text = "错误：get_task_status 需要 Coordinator 支持"
            else:
                result_text = get_task_status_tool(
                    task_id=args.get("task_id", ""),
                    coordinator=self.coordinator,
                )
        elif tool_name == "cancel_task":
            from mind.coordinator_tools import cancel_task_tool
            if not self.coordinator:
                result_text = "错误：cancel_task 需要 Coordinator 支持"
            else:
                result_text = cancel_task_tool(
                    task_id=args.get("task_id", ""),
                    coordinator=self.coordinator,
                )
        else:
            result = self.toolkit.execute(tool_name, args)
            if isinstance(result, ToolResult):
                signals = result.care_signals
            result_text = str(result)[:2000]

        latency_ms = (time.time() - t0) * 1000
        self._emit(AgentEvent(
            type=AgentEventType.TOOL_EXECUTION_END,
            tool=tool_name,
            step=iteration + 1,
            result_preview=str(result_text)[:300],
            iteration=iteration,
        ))
        logger.info(f"[AgentLoop] 工具执行完成: {tool_name}, result_preview={str(result_text)[:100]}")

        # 记录轨迹（OpenViking 启发 #4）
        if self.trace_store:
            self.trace_store.log_turn(
                iteration=iteration,
                tool_name=tool_name,
                args=args,
                result_preview=str(result_text),
                latency_ms=latency_ms,
            )

        tool_call_id = getattr(tc, "id", None) or tc.get("id") if isinstance(tc, dict) else None
        return tool_call_id, result_text, signals

    @staticmethod
    def _clean_tool_arguments(raw: str) -> Optional[str]:
        """清理并验证 tool_call arguments，确保是合法 JSON。"""
        try:
            parsed = json.loads(raw)
            return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            pass

        repaired = raw.replace("\n", "\\n").replace("\r", "\\r")
        try:
            parsed = json.loads(repaired)
            return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            pass

        for suffix in ('"}', "\"}", '"]\}', '"}\]}', "}"):
            try:
                parsed = json.loads(repaired + suffix)
                return json.dumps(parsed, ensure_ascii=False)
            except json.JSONDecodeError:
                pass

        return None

    def _build_max_iter_reply(self) -> str:
        """达到最大迭代次数时，检查已有成果并给出合适的回复。"""
        files = []
        try:
            for p in self.work_dir.rglob("*"):
                if p.is_file() and p.stat().st_size > 100:
                    rel = p.relative_to(self.work_dir)
                    files.append(f"- {rel}（{p.stat().st_size // 1024}KB）")
        except Exception:
            pass

        if files:
            file_list = "\n".join(files[:5])
            return (
                "销销已经写了一部分，但时间有点紧还没全部做完～\n\n"
                f"目前已有的文件：\n{file_list}\n\n"
                "您回复「继续」，我就接着把剩下的做完！"
            )

        todos = self.todo_store.read()
        completed = [t for t in todos if t.get("status") == "completed"]
        if completed:
            return (
                f"销销已经做完了 {len(completed)} 步，但后面几步时间不太够。\n"
                "您回复「继续」，我就接着把剩下的做完！"
            )

        return "这部分有点复杂，销销还需要一点时间才能做完。您回复「继续」，我接着干～"

    def _build_budget_exhausted_reply(self, reason: str) -> str:
        """时间/token/迭代预算耗尽时，给出可续接的回复。"""
        if reason.startswith("time_budget_exceeded"):
            prefix = "这部分需要的时间比预期长，销销先保存了当前进度。"
        elif reason.startswith("token_budget_exceeded"):
            prefix = "上下文有点长，销销先暂停一下，避免超出处理上限。"
        else:
            prefix = "这一步需要的轮次比较多，销销先记下了当前进度。"

        files = []
        try:
            for p in self.work_dir.rglob("*"):
                if p.is_file() and p.stat().st_size > 100:
                    rel = p.relative_to(self.work_dir)
                    files.append(f"- {rel}（{p.stat().st_size // 1024}KB）")
        except Exception:
            pass

        if files:
            file_list = "\n".join(files[:5])
            return f"{prefix}\n\n目前已有的文件：\n{file_list}\n\n您回复「继续」，我就接着把剩下的做完！"

        return f"{prefix} 您回复「继续」，我就接着把剩下的做完！"

    def _save_checkpoint(self, messages: List[AgentMessage], iteration: int, checkpoint_path):
        """保存当前任务状态到 checkpoint 文件。"""
        try:
            non_system = [m.to_llm() for m in messages if m.role != "system"]
            checkpoint = {
                "messages": non_system,
                "todos": self.todo_store.read(),
                "iteration": iteration,
                "timestamp": time.time(),
                "work_dir": str(self.work_dir),
            }
            checkpoint_path.write_text(
                json.dumps(checkpoint, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            logger.debug(f"Checkpoint 已保存: iteration={iteration}")
        except Exception as e:
            logger.warning(f"保存 checkpoint 失败: {e}")

    @staticmethod
    def _sanitize_messages(messages: List[AgentMessage]) -> List[AgentMessage]:
        """清理消息列表中的 orphaned tool_call / tool_result 配对。

        每轮 API 调用前必做，防止：
        1. tool result 没有对应的 assistant tool_call → API 400
        2. assistant tool_call 没有对应的 tool result → API 400
        """
        surviving_call_ids = set()
        for msg in messages:
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    cid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    if cid:
                        surviving_call_ids.add(cid)

        result_call_ids = set()
        for msg in messages:
            if msg.role == "tool" and msg.tool_call_id:
                result_call_ids.add(msg.tool_call_id)

        # 移除 orphaned tool results（没有对应 assistant tool_call 的）
        orphaned = result_call_ids - surviving_call_ids
        if orphaned:
            messages = [
                m for m in messages
                if not (m.role == "tool" and m.tool_call_id in orphaned)
            ]

        # 为缺少 result 的 tool_call 插入 stub
        missing = surviving_call_ids - result_call_ids
        if missing:
            patched = []
            for msg in messages:
                patched.append(msg)
                if msg.role == "assistant" and msg.tool_calls:
                    for tc in msg.tool_calls:
                        cid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                        if cid in missing:
                            patched.append(AgentMessage.tool(
                                content="[结果缺失 — 参见上下文摘要]",
                                tool_call_id=cid,
                            ))
            messages = patched

        return messages

    @staticmethod
    def _guardrail_requires_tools(messages: List[AgentMessage]) -> bool:
        """Guardrail：检查用户查询是否涉及时效性内容但未调用信息获取类工具。"""
        # 1. 找到最后一条用户消息
        last_user_query = ""
        for msg in reversed(messages):
            if msg.role == "user" and msg.content:
                last_user_query = msg.content
                break
        if not last_user_query:
            return False

        # 2. 检查是否包含时效性关键词
        query_lower = last_user_query.lower()
        has_time_sensitive = any(kw in query_lower for kw in _TIME_SENSITIVE_KEYWORDS)
        if not has_time_sensitive:
            return False

        # 3. 检查本轮是否已调用过信息获取类工具
        for msg in messages:
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("function", {}).get("name", "") if isinstance(tc, dict) else getattr(getattr(tc, "function", None), "name", "")
                    if name in _INFO_TOOLS:
                        return False  # 已调用过信息工具，Guardrail 不触发
        return True

    @staticmethod
    def _evaluate_output(reply: str) -> str:
        """Evaluator：轻量级输出质量自检，返回问题描述或空字符串。"""
        issues = []

        # 检查 AI 套话
        ai_phrases = ["综上所述", "首先，", "其次，", "最后，", "值得注意的是", "不难发现", "这意味着", "本质上", "换句话说"]
        found_phrases = [p for p in ai_phrases if p in reply]
        if found_phrases:
            issues.append(f"含AI套话({', '.join(found_phrases[:3])})")

        # 检查空洞形容词
        empty_adjectives = ["赋能", "抓手", "打造闭环", "沉淀", "落地", "对齐", "颗粒度"]
        found_adj = [a for a in empty_adjectives if a in reply]
        if found_adj:
            issues.append(f"含空洞词({', '.join(found_adj)})")

        # 检查自我矛盾（简单启发式：数字前后不一致）
        import re
        numbers = re.findall(r'\d+(?:\.\d+)?%?', reply)
        if len(numbers) >= 4:
            # 如果同一个数字以不同形式出现（如 "25.5%" 和 "25.5" 不算矛盾）
            pass  # 复杂数字一致性检查暂略，留待后续增强

        # 检查是否承诺了但未完成
        if "我马上" in reply and "查" in reply and "搜索" not in reply and "查到了" not in reply:
            issues.append("承诺搜索但未展示结果")

        return "；".join(issues) if issues else ""

    def _verify_result(self, messages: List[AgentMessage], final_content: str) -> str:
        """
        Verify 阶段：检查最终回复是否满足用户原始目标。
        返回问题描述或空字符串（表示通过）。
        """
        if not self.llm or not final_content:
            return ""

        # 提取原始目标（第一条 user 消息）
        original_goal = ""
        for msg in messages:
            if msg.role == "user" and msg.content:
                original_goal = msg.content
                break
        if not original_goal:
            return ""

        # 提取计划摘要（如果有）
        plan_summary = ""
        if self.plan_store and self.plan_store.has_plan():
            plan_summary = self.plan_store.get_summary()

        system = """你是一个严格的输出校验员。请判断下面的回复是否充分回答了用户的问题/完成了任务。

校验标准：
1. 回复是否直接回答了用户问题，没有回避或转移话题
2. 回复是否有具体信息/数据/结论，而不是泛泛而谈
3. 如果涉及多步骤任务，是否完成了所有关键步骤
4. 如果回复中说"需要进一步搜索/确认"但没有实际行动，视为不完整

只输出 JSON：{"passed": true/false, "issues": "问题描述，无则空字符串"}"""

        user_prompt = f"""用户目标：{original_goal}

执行计划：
{plan_summary or "无明确计划"}

---

助手回复：
{final_content[:2000]}

---

请输出 JSON 校验结果。"""

        try:
            response = self.llm.chat(system, user_prompt, max_tokens=300, temperature=0.3, json_mode=True)
            data = json.loads(response)
            if not data.get("passed", True):
                return data.get("issues", "校验未通过")
        except Exception as e:
            logger.warning(f"[Verify] 校验调用失败: {e}")
        return ""

    def _advance_plan_if_applicable(self, executables: list[Tuple]) -> None:
        """检查已执行的工具是否匹配当前 plan 步骤的预期工具，如果是则推进计划。"""
        if not self.plan_store or not self.plan_store.is_active():
            return

        progress = self.plan_store.get_progress()
        current_step_idx = progress.get("current_step", 1) - 1  # 0-based
        steps = self.plan_store._plan.get("steps", [])
        if not steps or current_step_idx < 0 or current_step_idx >= len(steps):
            return

        current_step = steps[current_step_idx]
        expected_tools = current_step.get("expected_tools", [])

        # 如果没有指定预期工具，则不自动推进（靠 LLM 自己调用 plan 更新）
        if not expected_tools:
            return

        # 检查本轮是否执行了当前步骤预期的任一工具
        executed_tools = {name for _, name, _ in executables}
        matched = bool(executed_tools & set(expected_tools))

        if matched:
            old_desc = current_step["description"]
            self.plan_store.advance(current_step["id"])

            # 发送 STEP_END 事件（旧步骤完成）
            self._emit(AgentEvent(
                type=AgentEventType.STEP_END,
                step=current_step_idx + 1,
                total=len(steps),
                description=old_desc,
            ))

            # 如果有下一步，发送 STEP_START 事件
            new_progress = self.plan_store.get_progress()
            new_step_idx = new_progress.get("current_step", 1) - 1
            if new_step_idx < len(steps):
                self._emit(AgentEvent(
                    type=AgentEventType.STEP_START,
                    step=new_step_idx + 1,
                    total=len(steps),
                    description=steps[new_step_idx]["description"],
                ))

    def _maybe_inject_plan_reminder(self, messages: List[AgentMessage], iteration: int) -> None:
        """Plan 阶段：复杂任务首轮且未制定计划时，提醒 LLM 先调用 plan 工具。"""
        if iteration != 0 or not self.plan_store:
            return
        if self.plan_store.has_plan():
            return

        # 提取最后一条用户消息
        last_user_query = ""
        for msg in reversed(messages):
            if msg.role == "user" and msg.content:
                last_user_query = msg.content
                break
        if not last_user_query:
            return

        query_lower = last_user_query.lower()
        has_planning_keyword = any(kw in query_lower for kw in _PLANNING_KEYWORDS)

        # 检查是否加载了规划型 skill（system prompt 中提及）
        has_planning_skill = False
        for msg in messages:
            if msg.role == "system" and msg.content:
                if any(skill in msg.content for skill in _PLANNING_SKILLS):
                    has_planning_skill = True
                    break

        if has_planning_keyword or has_planning_skill:
            reminder = (
                "【系统提醒】这是一个多步骤复杂任务，请先调用 plan 工具制定一个可执行计划，"
                "再按步骤执行。计划应包含清晰的步骤编号、描述和预计使用的工具。"
            )
            messages.append(AgentMessage.assistant(content=reminder))
            logger.info("[Plan] 检测到复杂任务，已注入计划制定提醒")

    def _reflect_on_past(self, messages: List[AgentMessage]) -> str:
        """
        模糊请求自动反思：遇到不明确的用户输入时，查询历史记录和学习规则。
        返回反思结果文本（空字符串表示无需反思）。
        """
        # 提取最后一条 user 消息
        last_user_msg = None
        for msg in reversed(messages):
            if msg.role == "user":
                last_user_msg = msg.content or ""
                break
        if not last_user_msg:
            return ""

        # 模糊请求判定：短文本 + 无明确动作词
        action_words = {
            "查", "搜", "找", "写", "分析", "生成", "计算", "对比", "总结", "推荐",
            "提醒", "下载", "打开", "浏览", "读", "看", "问", "发", "做", "整理",
            "制作", "创建", "导出", "导入", "扫描", "检查", "测试", "运行", "执行",
            "调用", "使用", "告诉", "说说", "讲讲", "聊聊", "分析", "研究", "预测",
            "评估", "比较", "列出", "显示", "展示", "打印", "导出", "保存", "发送",
        }
        has_action = any(w in last_user_msg for w in action_words)
        is_vague = len(last_user_msg) < 20 and not has_action

        if not is_vague:
            return ""

        try:
            from mind.analytics import get_store
            store = get_store()

            # 1. 查询相关学习记录
            learnings = store.get_relevant_learnings(last_user_msg[:10], limit=3)

            # 2. 查询历史执行（同一用户的）
            # 尝试从消息中提取 user_id（从 trace_store 或上下文）
            user_id = None
            if self.trace_store and hasattr(self.trace_store, "user_id"):
                user_id = self.trace_store.user_id

            past = store.search_past_executions(last_user_msg[:6], user_id)

            # 3. 组装反思提示
            parts = ["【自我观察】这个请求比较模糊，我查了一下自己的历史记录："]

            if learnings:
                parts.append("\n之前沉淀的经验：")
                for ln in learnings[:3]:
                    parts.append(f"- [{ln.get('category', '?')}] {ln.get('content', '')[:80]}...")

            if past:
                parts.append("\n类似的历史处理：")
                for p in past[:3]:
                    parts.append(f"- 工具: {p.get('tool', '?')}, 结果: {p.get('result_preview', '')[:60]}...")

            if not learnings and not past:
                return ""

            parts.append("\n请根据以上经验，判断用户最可能想要什么，并主动提出最合理的下一步。")
            return "\n".join(parts)

        except Exception as e:
            logger.debug(f"reflect_on_past 异常: {e}")
            return ""

    def _classify_intent(self, messages: List[AgentMessage]) -> Tuple[str, float]:
        """L1 意图分类，返回 (intent_type, confidence)"""
        from mind.intent_router import classify_intent
        last_user = ""
        for msg in reversed(messages):
            if msg.role == "user":
                last_user = msg.content or ""
                break
        return classify_intent(last_user)

    def _simple_reply(self, messages: List[AgentMessage]) -> dict:
        """
        简单请求快速回复：单次 LLM，最多一轮工具调用。
        不走 while 循环，延迟控制在 1-3 秒。
        """
        tools = self.toolkit.schema()
        llm_messages = [m.to_llm() for m in messages]

        self._emit(AgentEvent(type=AgentEventType.AGENT_START, resumed=False))

        # 第一轮：LLM 判断是否需要工具
        resp = self.llm.chat_with_tools(
            messages=llm_messages,
            tools=tools,
            model=self.llm.model_daily,
            max_tokens=2048,
            temperature=0.5,
        )

        if not resp or not resp.choices:
            return {
                "reply": "销销出了点问题，请您稍后再试。",
                "final_messages": messages,
                "care_signals": [],
                "iteration": 0,
            }

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if tool_calls:
            valid_tcs = []
            for tc in tool_calls:
                clean_args = self._clean_tool_arguments(tc.function.arguments)
                if clean_args is not None:
                    valid_tcs.append({
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": clean_args,
                        },
                    })

            if valid_tcs:
                reasoning = getattr(msg, "reasoning_content", None)
                # 只执行第一个工具，assistant message 也只放这一个，避免 tool message 数量不匹配
                tc_exec = valid_tcs[0]
                messages.append(AgentMessage.assistant(
                    content=msg.content or "",
                    tool_calls=[tc_exec],
                    reasoning_content=reasoning,
                ))

                # 只执行第一个工具（特殊工具走 _execute_single_tool，标准工具走 toolkit）
                tc = valid_tcs[0]
                tool_name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"])

                self._emit(AgentEvent(
                    type=AgentEventType.TOOL_EXECUTION_START,
                    tool=tool_name,
                    step=1,
                    iteration=0,
                ))
                logger.info(f"[AgentLoop] 工具执行开始: {tool_name}(args={args}), iteration=0")
                # 特殊工具（plan/todo/delegate 等）通过 _execute_single_tool 处理
                if tool_name in ("todo", "plan", "delegate", "spawn_task", "get_task_status", "cancel_task"):
                    from mind.tool_result import ToolResult
                    _, result_text, _ = self._execute_single_tool(tc, tool_name, args, 0)
                else:
                    result = self.toolkit.execute(tool_name, args)
                    result_text = str(result)
                self._emit(AgentEvent(
                    type=AgentEventType.TOOL_EXECUTION_END,
                    tool=tool_name,
                    step=1,
                    iteration=0,
                ))

                messages.append(AgentMessage.tool(
                    content=result_text,
                    tool_call_id=tc["id"],
                ))

                # 第二轮：生成最终回复
                llm_messages = [m.to_llm() for m in messages]
                resp2 = self.llm.chat_with_tools(
                    messages=llm_messages,
                    tools=[],
                    model=self.llm.model_daily,
                    max_tokens=2048,
                    temperature=0.5,
                )
                raw_reply = resp2.choices[0].message.content if resp2 and resp2.choices else msg.content or ""
                # 检测异常输出（DeepSeek thinking mode 可能泄漏 tool call 标记）
                if raw_reply and ("<｜｜DSML｜｜" in raw_reply or "tool_calls" in raw_reply or "function" in raw_reply):
                    # 降级：直接返回工具结果摘要
                    final_reply = f"查到了一些信息，您看看：\n\n{result_text[:800]}"
                else:
                    final_reply = raw_reply
            else:
                final_reply = msg.content or ""
        else:
            final_reply = msg.content or ""

        final_reply = self._stream_reply(final_reply)
        self._emit(AgentEvent(type=AgentEventType.AGENT_END, iteration=0))
        return {
            "reply": final_reply,
            "final_messages": messages,
            "care_signals": [],
            "iteration": 0,
        }

