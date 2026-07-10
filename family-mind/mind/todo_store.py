"""
Todo 工具（照搬 Hermes todo_tool.py 核心逻辑）
让 LLM 在复杂任务中自己维护任务列表、跟踪进度。
"""
import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}


class TodoStore:
    """内存中的 todo 列表，每个 Agent 实例一个"""

    def __init__(self):
        self._items: List[Dict[str, str]] = []

    def write(self, todos: List[Dict[str, Any]], merge: bool = False) -> List[Dict[str, str]]:
        """写入 todo 列表。merge=False 时替换全部，merge=True 时按 id 更新。"""
        if not todos:
            return self.read()

        if not merge:
            self._items = [self._validate(t) for t in self._dedupe_by_id(todos)]
        else:
            existing = {item["id"]: item for item in self._items}
            for t in self._dedupe_by_id(todos):
                item_id = str(t.get("id", "")).strip()
                if not item_id:
                    continue
                if item_id in existing:
                    if "content" in t and t["content"]:
                        existing[item_id]["content"] = str(t["content"]).strip()
                    if "status" in t and t["status"]:
                        status = str(t["status"]).strip().lower()
                        if status in VALID_STATUSES:
                            existing[item_id]["status"] = status
                else:
                    validated = self._validate(t)
                    existing[validated["id"]] = validated
                    self._items.append(validated)
            # 重建列表，保持顺序
            seen = set()
            rebuilt = []
            for item in self._items:
                current = existing.get(item["id"], item)
                if current["id"] not in seen:
                    rebuilt.append(current)
                    seen.add(current["id"])
            self._items = rebuilt
        return self.read()

    def read(self) -> List[Dict[str, str]]:
        return [item.copy() for item in self._items]

    def clear(self):
        self._items = []

    def has_items(self) -> bool:
        return bool(self._items)

    def format_for_injection(self) -> Optional[str]:
        """渲染 todo 列表用于注入对话上下文（压缩后重新提醒 LLM）"""
        if not self._items:
            return None
        active = [i for i in self._items if i["status"] in ("pending", "in_progress")]
        if not active:
            return None
        lines = ["[你的任务列表]"]
        for item in active:
            marker = {
                "in_progress": "[>]",
                "pending": "[ ]",
            }.get(item["status"], "[?]")
            lines.append(f"- {marker} {item['id']}. {item['content']}")
        return "\n".join(lines)

    @staticmethod
    def _validate(item: Dict[str, Any]) -> Dict[str, str]:
        item_id = str(item.get("id", "")).strip()
        if not item_id:
            item_id = "?"
        content = str(item.get("content", "")).strip()
        if not content:
            content = "(no description)"
        status = str(item.get("status", "pending")).strip().lower()
        if status not in VALID_STATUSES:
            status = "pending"
        return {"id": item_id, "content": content, "status": status}

    @staticmethod
    def _dedupe_by_id(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        last_index: Dict[str, int] = {}
        for i, item in enumerate(todos):
            item_id = str(item.get("id", "")).strip() or "?"
            last_index[item_id] = i
        return [todos[i] for i in sorted(last_index.values())]


def todo_tool(todos: Optional[List[Dict[str, Any]]] = None,
              merge: bool = False,
              store: Optional[TodoStore] = None) -> str:
    """todo 工具入口"""
    if store is None:
        return json.dumps({"error": "TodoStore 未初始化"}, ensure_ascii=False)

    if todos is not None:
        items = store.write(todos, merge)
    else:
        items = store.read()

    pending = sum(1 for i in items if i["status"] == "pending")
    in_progress = sum(1 for i in items if i["status"] == "in_progress")
    completed = sum(1 for i in items if i["status"] == "completed")

    return json.dumps({
        "todos": items,
        "summary": {
            "total": len(items),
            "pending": pending,
            "in_progress": in_progress,
            "completed": completed,
        },
    }, ensure_ascii=False)
