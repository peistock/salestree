"""
Plan 工具 — 让 LLM 在复杂任务中制定和更新执行计划

用法：
    plan(steps=[
        {"id": "1", "description": "搜索中国财险最新业绩", "expected_tools": ["search_web"]},
        {"id": "2", "description": "分析财务数据", "expected_tools": ["fetch_webpage"]},
    ])
"""
import json
import logging
from typing import Dict, Any, List, Optional

from mind.plan_store import PlanStore

logger = logging.getLogger(__name__)


def plan_tool(
    steps: List[Dict[str, Any]],
    reason: str = "",
    store: Optional[PlanStore] = None,
) -> str:
    """LLM 调用此工具提交/更新执行计划。

    参数:
        steps: 步骤列表，每项包含:
            - id: 步骤编号（如 "1", "2a"）
            - description: 步骤描述（一句话说明要做什么）
            - expected_tools: 可选，预计使用的工具列表
        reason: 为什么需要这个计划（如"这是一个多阶段深度分析任务"）
        store: PlanStore 实例
    """
    if store is None:
        return json.dumps({"error": "PlanStore 未初始化"}, ensure_ascii=False)

    if not steps:
        return json.dumps({"error": "steps 不能为空"}, ensure_ascii=False)

    try:
        progress = store.create(steps)
        # 改动3：Plan 结果精简 — reason 可能很长，截断避免占用上下文
        reason_short = reason[:200] + "..." if len(reason) > 200 else reason
        return json.dumps({
            "status": "ok",
            "reason": reason_short,
            "progress": progress,
            "message": f"计划已创建，共 {progress.get('total_steps', len(steps))} 步，当前聚焦第 1 步。",
        }, ensure_ascii=False)
    except Exception as e:
        logger.error(f"plan_tool 异常: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)
