"""
Plan 存储层 — 结构化执行计划管理

与 TodoStore 自动同步，让系统知道"当前在哪一步"，
从而提供准确的进度可视化。
"""
import json
import logging
from typing import Dict, Any, List, Optional

from mind.todo_store import TodoStore

logger = logging.getLogger(__name__)


class PlanStore:
    """管理结构化执行计划，与 TodoStore 自动同步"""

    def __init__(self, todo_store: TodoStore):
        self._todo_store = todo_store
        self._plan: Optional[dict] = None

    def create(self, steps: List[dict]) -> dict:
        """从 LLM 提交的计划步骤创建计划。

        步骤格式: [{"id": "1", "description": "搜索竞品信息", "expected_tools": ["search_web"]}]
        同时自动同步到 TodoStore。
        """
        validated_steps = []
        for i, s in enumerate(steps):
            step_id = str(s.get("id", str(i + 1))).strip()
            desc = str(s.get("description", "")).strip()
            if not desc:
                desc = f"步骤 {step_id}"
            expected_tools = s.get("expected_tools", [])
            if isinstance(expected_tools, str):
                expected_tools = [expected_tools]
            validated_steps.append({
                "id": step_id,
                "description": desc,
                "expected_tools": [str(t) for t in expected_tools if t],
                "status": "pending",
            })

        if validated_steps:
            validated_steps[0]["status"] = "in_progress"

        self._plan = {
            "steps": validated_steps,
            "current_step_index": 0,
        }

        # 同步到 TodoStore
        todo_items = []
        for s in validated_steps:
            todo_items.append({
                "id": f"plan-{s['id']}",
                "content": s["description"],
                "status": s["status"],
            })
        if todo_items:
            self._todo_store.write(todo_items, merge=False)

        logger.info(f"[PlanStore] 计划已创建: {len(validated_steps)} 步")
        return self.get_progress()

    def advance(self, step_id: Optional[str] = None) -> bool:
        """将指定步骤（或当前步骤）标记为完成，自动推进到下一步。
        同时更新 TodoStore 中对应项状态。
        """
        if not self._plan:
            return False

        steps = self._plan["steps"]
        idx = self._plan["current_step_index"]

        if step_id:
            # 找到指定步骤
            found = False
            for i, s in enumerate(steps):
                if s["id"] == step_id:
                    s["status"] = "completed"
                    idx = i
                    found = True
                    break
            if not found:
                return False
        else:
            # 标记当前步骤完成
            if 0 <= idx < len(steps):
                steps[idx]["status"] = "completed"

        # 推进到下一步
        next_idx = idx + 1
        if next_idx < len(steps):
            steps[next_idx]["status"] = "in_progress"
            self._plan["current_step_index"] = next_idx
            logger.info(f"[PlanStore] 步骤推进: {steps[idx]['id']} 完成 -> {steps[next_idx]['id']} 进行中")
        else:
            self._plan["current_step_index"] = len(steps)
            logger.info(f"[PlanStore] 所有步骤已完成: {len(steps)}/{len(steps)}")

        # 同步到 TodoStore
        todo_items = []
        for s in steps:
            todo_items.append({
                "id": f"plan-{s['id']}",
                "content": s["description"],
                "status": s["status"],
            })
        self._todo_store.write(todo_items, merge=False)
        return True

    def get_progress(self) -> dict:
        """返回当前进度信息。"""
        if not self._plan:
            return {}

        steps = self._plan["steps"]
        idx = self._plan["current_step_index"]
        total = len(steps)
        completed = sum(1 for s in steps if s["status"] == "completed")
        current_desc = steps[idx]["description"] if 0 <= idx < total else "收尾中"

        return {
            "current_step": idx + 1 if idx < total else total,
            "total_steps": total,
            "current_desc": current_desc,
            "completed": completed,
            "remaining": total - completed,
            "percent": int((completed / total) * 100) if total > 0 else 0,
        }

    def format_for_injection(self) -> Optional[str]:
        """渲染计划注入上下文，提醒 LLM 当前步骤。"""
        if not self._plan:
            return None

        steps = self._plan["steps"]
        idx = self._plan["current_step_index"]
        lines = ["[当前执行计划]"]

        for i, s in enumerate(steps):
            marker = {
                "completed": "[✓]",
                "in_progress": "[>]",
                "pending": "[ ]",
            }.get(s["status"], "[?]")
            prefix = f"{marker} 步骤 {s['id']}: {s['description']}"
            if s.get("expected_tools"):
                prefix += f" (可用工具: {', '.join(s['expected_tools'][:3])})"
            lines.append(prefix)

        if 0 <= idx < len(steps):
            lines.append(f"\n👉 当前应聚焦：步骤 {steps[idx]['id']} - {steps[idx]['description']}")
            lines.append("如需调整计划，可调用 plan 工具更新步骤。")

        return "\n".join(lines)

    def is_active(self) -> bool:
        """是否有正在执行的计划（未全部完成）。"""
        if not self._plan:
            return False
        steps = self._plan["steps"]
        completed = sum(1 for s in steps if s["status"] == "completed")
        return completed < len(steps)

    def has_plan(self) -> bool:
        """是否存在计划（无论是否已完成）。"""
        return self._plan is not None and len(self._plan.get("steps", [])) > 0

    def get_summary(self) -> str:
        """返回计划的简洁文本摘要。"""
        if not self._plan:
            return "无计划"
        steps = self._plan["steps"]
        lines = []
        for s in steps:
            marker = "✓" if s.get("status") == "completed" else (">" if s.get("status") == "in_progress" else "○")
            lines.append(f"{marker} {s.get('id')}. {s.get('description', '')}")
        return "\n".join(lines)

    def get_current_step_tools(self) -> List[str]:
        """获取当前步骤建议的工具列表。"""
        if not self._plan:
            return []
        idx = self._plan["current_step_index"]
        steps = self._plan["steps"]
        if 0 <= idx < len(steps):
            return steps[idx].get("expected_tools", [])
        return []

    def read(self) -> Optional[dict]:
        """返回计划原始数据（用于 checkpoint 保存）。"""
        return self._plan.copy() if self._plan else None
