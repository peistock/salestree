"""
子 Agent 并行执行器（简化版，适配 销销/SalesMind）
借鉴 Hermes delegate_tool.py 的核心思想：
- ThreadPoolExecutor 并行执行多个子任务
- 每个子任务有独立的 Tool Calling while 循环
- 限制迭代次数和工具集
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from mind.llm_client import LLMClient
from mind.tool_result import ToolResult, ToolRegistry
from mind.context_compressor import compress_messages

logger = logging.getLogger(__name__)

MAX_CHILDREN = 3
MAX_ITERATIONS = 15


def _run_single_subagent(
    task_index: int,
    goal: str,
    context: Optional[str],
    tools: List[Dict],
    llm: LLMClient,
) -> Dict[str, Any]:
    """运行单个子 Agent，执行 Tool Calling while 循环"""
    start = time.monotonic()
    system = """你是一个专注的子任务执行助手。用工具完成指定任务，提供简洁的总结。

【铁律】你当前是子任务执行者，禁止再创建子任务或委托他人：
- 禁止使用 delegate 工具
- 禁止使用 spawn_task 工具
- 禁止使用 get_task_status / cancel_task 工具
- 禁止要求再派生子 Agent 帮你收集信息

你必须直接使用 search_web、browse_open、fetch_webpage、read_file 等工具完成研究，然后返回一份结构化的简洁摘要（500-1500字）。摘要要包含关键事实、数据来源和核心结论，不要只写"进行中"。
"""
    if context:
        system += f"\n\n背景信息：{context}"

    # 子 Agent 禁止递归委托
    forbidden = {"delegate", "spawn_task", "get_task_status", "cancel_task"}
    filtered_tools = [t for t in tools if t.get("function", {}).get("name") not in forbidden]

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"任务：{goal}"},
    ]

    registry = ToolRegistry()
    # 扫描工具方法到注册表（简化：只注册核心工具）
    from mind.tools import Toolkit
    tk = Toolkit(work_dir=None)  # work_dir 会在执行时处理
    registry.scan_instance(tk)

    summary = ""
    api_calls = 0

    try:
        for iteration in range(MAX_ITERATIONS):
            resp = llm.chat_with_tools(
                messages=messages,
                tools=filtered_tools,
                model=None,  # 使用默认模型
                max_tokens=4096,
                temperature=0.5,
            )
            api_calls += 1

            if not resp or not resp.choices:
                summary = "子任务执行失败：LLM 无响应"
                break

            msg = resp.choices[0].message
            if getattr(msg, "tool_calls", None):
                valid_tool_calls = []
                for tc in msg.tool_calls:
                    raw_args = tc.function.arguments
                    try:
                        parsed = json.loads(raw_args)
                        clean_args = json.dumps(parsed, ensure_ascii=False)
                        valid_tool_calls.append({
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": clean_args,
                            }
                        })
                    except json.JSONDecodeError:
                        continue

                if valid_tool_calls:
                    messages.append({
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": valid_tool_calls,
                    })

                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        continue
                    result = registry.execute(tc.function.name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result)[:2000],
                    })

                # 上下文压缩
                if iteration > 0 and iteration % 4 == 0:
                    compressed = compress_messages(
                        messages=messages,
                        llm_chat_fn=llm.chat,
                        todo_injection=None,
                    )
                    if compressed is not messages:
                        messages = compressed
                continue
            else:
                summary = msg.content or ""
                break
        else:
            summary = "子任务迭代次数用尽，未能完成。"

    except Exception as e:
        logger.error(f"子 Agent {task_index} 执行异常: {e}")
        summary = f"子任务执行异常: {e}"

    duration = round(time.monotonic() - start, 2)
    return {
        "task_index": task_index,
        "status": "completed" if summary and not summary.startswith("子任务") else "failed",
        "summary": summary,
        "api_calls": api_calls,
        "duration_seconds": duration,
    }


def run_subagents(
    tasks: List[Dict[str, Any]],
    tools: List[Dict],
    llm: LLMClient,
    coordinator=None,
) -> str:
    """
    并行运行多个子 Agent 任务。

    Args:
        tasks: 每个元素是 {"goal": str, "context": str(optional)}
        tools: 子 Agent 可用的工具定义
        llm: LLMClient 实例
        coordinator: 可选的 TaskCoordinator，如果提供则使用 Task 抽象层

    Returns:
        JSON 字符串，包含每个子任务的结果
    """
    if not tasks:
        return json.dumps({"error": "没有提供任务"}, ensure_ascii=False)

    if len(tasks) > MAX_CHILDREN:
        return json.dumps({
            "error": f"任务太多：{len(tasks)} 个，最多支持 {MAX_CHILDREN} 个并行"
        }, ensure_ascii=False)

    # Coordinator 模式：每个子任务变成独立的 Task
    if coordinator is not None:
        from mind.task_engine import TaskType
        task_ids = []
        for task in tasks:
            context = task.get("context", "")
            goal = task["goal"]
            if context:
                goal = f"{goal}\n\n背景信息：{context}"
            t = coordinator.create_task(
                goal=goal,
                task_type=TaskType.RESEARCH,
            )
            task_ids.append(t.id)
        # 启动所有就绪任务
        coordinator.start_ready_tasks()
        # 等待所有子任务完成
        for tid in task_ids:
            coordinator.wait_for_task(tid)
        # 收集结果
        results = []
        for idx, tid in enumerate(task_ids):
            task = coordinator.task_store.get(tid)
            results.append({
                "task_index": idx,
                "status": "completed" if task.status.value == "completed" else task.status.value,
                "summary": task.result or "",
                "task_id": tid,
                "api_calls": 0,
                "duration_seconds": 0,
            })
        return json.dumps({
            "task_count": len(tasks),
            "completed": sum(1 for r in results if r["status"] == "completed"),
            "failed": sum(1 for r in results if r["status"] != "completed"),
            "results": results,
        }, ensure_ascii=False, indent=2)

    # 传统模式（直接 ThreadPoolExecutor）
    results = []

    if len(tasks) == 1:
        # 单任务直接执行，省去线程池开销
        result = _run_single_subagent(
            0, tasks[0]["goal"], tasks[0].get("context"), tools, llm
        )
        results.append(result)
    else:
        # 并行执行
        with ThreadPoolExecutor(max_workers=MAX_CHILDREN) as executor:
            futures = {}
            for i, task in enumerate(tasks):
                future = executor.submit(
                    _run_single_subagent,
                    i,
                    task["goal"],
                    task.get("context"),
                    tools,
                    llm,
                )
                futures[future] = i

            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as e:
                    idx = futures[future]
                    result = {
                        "task_index": idx,
                        "status": "error",
                        "summary": None,
                        "error": str(e),
                        "api_calls": 0,
                        "duration_seconds": 0,
                    }
                results.append(result)

    # 按 task_index 排序
    results.sort(key=lambda x: x["task_index"])

    return json.dumps({
        "task_count": len(tasks),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] != "completed"),
        "results": results,
    }, ensure_ascii=False, indent=2)
