"""
Coordinator 工具 — spawn_task / get_task_status / cancel_task

让 LLM 在 AgentLoop 中调用这些工具来创建和管理子任务。
实际执行由 AgentLoop._execute_single_tool 特殊处理（类似 delegate），
不通过 ToolRegistry 注册（因为需要 coordinator 和 parent_task_id 上下文）。
"""
import json
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def spawn_task_tool(
    goal: str,
    task_type: str = "research",
    dependencies: Optional[List[str]] = None,
    coordinator=None,
    parent_task_id: Optional[str] = None,
) -> str:
    """创建子任务。Coordinator 自动调度执行。

    当复杂任务需要拆分为多个子任务并行执行时使用。
    每个子任务有独立的 work_dir、todo_store、plan_store 和 checkpoint。

    Args:
        goal: 子任务目标，描述清楚要做什么
        task_type: 任务类型（research/analysis/writing/coding/verification/composite）
        dependencies: 依赖的任务 ID 列表，这些任务完成后本子任务才会启动
        coordinator: TaskCoordinator 实例（由 AgentLoop 自动传入）
        parent_task_id: 父任务 ID（由 AgentLoop 自动传入，LLM 无需填写）
    """
    if coordinator is None:
        return json.dumps({"error": "Coordinator 未初始化"}, ensure_ascii=False)

    try:
        task = coordinator.create_task(
            goal=goal,
            task_type=task_type,
            parent_id=parent_task_id,
            dependencies=dependencies,
        )
        # 自动尝试启动就绪任务（如果并发槽位有空闲）
        coordinator.start_ready_tasks()

        return json.dumps({
            "task_id": task.id,
            "status": "created",
            "goal": task.goal,
            "type": task.task_type,
            "message": f"子任务已创建: {task.id}",
        }, ensure_ascii=False)
    except Exception as e:
        logger.error(f"spawn_task 失败: {e}", exc_info=True)
        return json.dumps({"error": f"创建子任务失败: {e}"}, ensure_ascii=False)


def get_task_status_tool(
    task_id: str,
    coordinator=None,
) -> str:
    """查询任务状态和进度。

    查看指定任务的当前状态、结果摘要、子任务数量和整体进度。
    """
    if coordinator is None:
        return json.dumps({"error": "Coordinator 未初始化"}, ensure_ascii=False)

    task = coordinator.task_store.get(task_id)
    if not task:
        return json.dumps({"error": f"任务不存在: {task_id}"}, ensure_ascii=False)

    children = coordinator.task_store.get_children(task_id)
    progress = coordinator.task_store.get_progress_summary(task_id)

    return json.dumps({
        "task_id": task.id,
        "goal": task.goal,
        "type": task.task_type,
        "status": task.status.value,
        "result_preview": task.result[:300] if task.result else "",
        "error": task.error,
        "children_count": len(children),
        "progress": progress,
    }, ensure_ascii=False)


def cancel_task_tool(
    task_id: str,
    coordinator=None,
) -> str:
    """取消任务（级联取消子任务）。

    停止指定任务及其所有子任务的执行。
    """
    if coordinator is None:
        return json.dumps({"error": "Coordinator 未初始化"}, ensure_ascii=False)

    coordinator.cancel_task(task_id)
    return json.dumps({
        "task_id": task_id,
        "status": "cancelled",
    }, ensure_ascii=False)
