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
    """todo 列表，支持可选的数据库持久化"""

    def __init__(self, store_key: Optional[str] = None, user_id: Optional[str] = None):
        self._items: List[Dict[str, str]] = []
        self.store_key = store_key
        self.user_id = user_id
        if store_key and user_id:
            self.load()

    def load(self) -> List[Dict[str, str]]:
        """从数据库加载 todo 列表"""
        if not self.store_key or not self.user_id:
            return self.read()
        try:
            from mind.memory import load_todos
            loaded = load_todos(self.store_key)
            self._items = [self._validate(t) for t in loaded]
            logger.debug(f"[TodoStore] 已加载 {len(self._items)} 条 todos: {self.store_key}")
        except Exception as e:
            logger.warning(f"[TodoStore] 加载失败，使用空列表: {e}")
            self._items = []
        return self.read()

    def save(self) -> bool:
        """保存 todo 列表到数据库"""
        if not self.store_key or not self.user_id:
            return False
        try:
            from mind.memory import save_todos
            return save_todos(self.store_key, self.user_id, self.read())
        except Exception as e:
            logger.warning(f"[TodoStore] 保存失败: {e}")
            return False

    def write(self, todos: List[Dict[str, Any]], merge: bool = False) -> List[Dict[str, str]]:
        """写入 todo 列表。merge=False 时替换全部，merge=True 时按 id 更新。"""
        if not todos and not merge:
            self._items = []
            self.save()
            return self.read()
        if not todos:
            result = self.read()
            self.save()
            return result

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
        self.save()
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
