"""
Task 抽象层 — 可追踪、可恢复、可观测的执行单元

把一次执行封装为 Task，每个 Task 有自己的状态空间（messages、todo、plan、work_dir）。
TaskStore 管理任务的注册、查询、依赖调度和生命周期跟踪。
"""
import json
import logging
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

from mind.todo_store import TodoStore
from mind.plan_store import PlanStore
from mind.agent_message import AgentMessage

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    """任务状态机"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskType:
    """任务类型常量"""
    RESEARCH = "research"          # 只读信息收集（搜索、浏览）
    ANALYSIS = "analysis"          # 分析综合（基于信息做判断）
    WRITING = "writing"            # 内容生成（文章、报告）
    CODING = "coding"              # 代码操作（读、编辑、测试）
    VERIFICATION = "verification"  # 验证检查（测试、复查）
    COMPOSITE = "composite"        # 复合任务（由子任务组成）


# 任务 ID 前缀映射
_TASK_ID_PREFIXES = {
    TaskType.RESEARCH: "r",
    TaskType.ANALYSIS: "a",
    TaskType.WRITING: "w",
    TaskType.CODING: "c",
    TaskType.VERIFICATION: "v",
    TaskType.COMPOSITE: "x",
}


def _generate_task_id(task_type: str) -> str:
    """生成任务 ID：类型前缀 + 8 位随机字母数字"""
    prefix = _TASK_ID_PREFIXES.get(task_type, "x")
    suffix = uuid.uuid4().hex[:8]
    return f"{prefix}-{suffix}"


class Task:
    """可追踪、可恢复、可观测的执行单元

    每个 Task 拥有独立的：
    - messages: 对话历史（含 system prompt）
    - todo_store: 任务清单
    - plan_store: 执行计划
    - work_dir: 工作目录（文件操作隔离）
    - checkpoint: 断点续作支持
    """

    def __init__(
        self,
        goal: str,
        task_type: str,
        parent_id: Optional[str] = None,
        work_dir_base: Optional[Path] = None,
    ):
        self.id = _generate_task_id(task_type)
        self.goal = goal
        self.task_type = task_type
        self.parent_id = parent_id
        self.children: List[str] = []         # 子任务 ID 列表
        self.dependencies: List[str] = []     # 依赖任务 ID 列表
        self.status = TaskStatus.PENDING
        self.result: Optional[str] = None
        self.error: Optional[str] = None
        self.iteration = 0
        self.max_iterations = 50

        # 独立状态空间
        self.messages: List[AgentMessage] = []
        self.todo_store = TodoStore()
        self.plan_store = PlanStore(self.todo_store)

        # 工作目录（隔离文件操作）
        if work_dir_base:
            self.work_dir = work_dir_base / "tasks" / self.id
        else:
            self.work_dir = Path("./data") / "tasks" / self.id
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # Artifact（产出物文件路径列表）
        self.artifacts: List[str] = []

        # 时间戳
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None

        # Checkpoint 路径
        self.checkpoint_path = self.work_dir / "checkpoint.json"

    def to_dict(self) -> dict:
        """序列化为字典（用于 checkpoint 保存）"""
        return {
            "id": self.id,
            "goal": self.goal,
            "task_type": self.task_type,
            "parent_id": self.parent_id,
            "children": self.children,
            "dependencies": self.dependencies,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "work_dir": str(self.work_dir),
            "artifacts": self.artifacts,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    def save_checkpoint(self, messages: List[AgentMessage], iteration: int) -> None:
        """保存任务状态到 checkpoint"""
        try:
            data = {
                "task": self.to_dict(),
                "messages": [m.to_llm() for m in messages],
                "todos": self.todo_store.read(),
                "iteration": iteration,
                "timestamp": time.time(),
            }
            self.checkpoint_path.write_text(
                json.dumps(data, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            logger.debug(f"[Task] Checkpoint 已保存: {self.id} iteration={iteration}")
        except Exception as e:
            logger.warning(f"[Task] Checkpoint 保存失败: {self.id}, {e}")

    def load_checkpoint(self) -> Optional[dict]:
        """从 checkpoint 恢复任务状态"""
        if not self.checkpoint_path.exists():
            return None
        try:
            data = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            # 恢复 todo
            for item in data.get("todos", []):
                self.todo_store.write([item], merge=True)
            self.iteration = data.get("iteration", 0)
            logger.info(f"[Task] Checkpoint 已恢复: {self.id} iteration={self.iteration}")
            return data
        except Exception as e:
            logger.warning(f"[Task] Checkpoint 恢复失败: {self.id}, {e}")
            return None

    def add_artifact(self, path: str) -> None:
        """添加产出物"""
        if path not in self.artifacts:
            self.artifacts.append(path)

    def save_artifact(self, name: str, content: str) -> str:
        """保存产出物到工作目录，返回文件路径"""
        artifact_path = self.work_dir / name
        try:
            artifact_path.write_text(content, encoding="utf-8")
            self.add_artifact(str(artifact_path))
            return str(artifact_path)
        except Exception as e:
            logger.warning(f"[Task] Artifact 保存失败: {artifact_path}, {e}")
            return ""

    def read_artifact(self, name: str) -> str:
        """读取产出物内容"""
        artifact_path = self.work_dir / name
        if artifact_path.exists():
            return artifact_path.read_text(encoding="utf-8")
        return ""

    def is_terminal(self) -> bool:
        """是否处于终止状态"""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)

    def __repr__(self) -> str:
        return f"Task({self.id}, {self.task_type}, {self.status.value}, goal={self.goal[:30]!r})"


class TaskStore:
    """内存中的任务注册表（每个 Coordinator 一个实例）"""

    def __init__(self):
        self._tasks: Dict[str, Task] = {}

    def create(
        self,
        goal: str,
        task_type: str,
        parent_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
        work_dir_base: Optional[Path] = None,
    ) -> Task:
        """创建新任务"""
        task = Task(goal, task_type, parent_id, work_dir_base)
        if dependencies:
            task.dependencies = list(dependencies)
        self._tasks[task.id] = task

        # 如果指定了 parent_id，注册到父任务的 children 列表
        if parent_id and parent_id in self._tasks:
            self._tasks[parent_id].children.append(task.id)

        logger.info(f"[TaskStore] 任务已创建: {task}")
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def update_status(self, task_id: str, status: TaskStatus) -> bool:
        """更新任务状态"""
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.status = status
        if status == TaskStatus.RUNNING and not task.started_at:
            task.started_at = time.time()
        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.completed_at = time.time()
        logger.info(f"[TaskStore] 任务状态更新: {task_id} -> {status.value}")
        return True

    def get_children(self, parent_id: str) -> List[Task]:
        """获取子任务列表"""
        parent = self._tasks.get(parent_id)
        if not parent:
            return []
        return [self._tasks[cid] for cid in parent.children if cid in self._tasks]

    def get_by_status(self, status: TaskStatus) -> List[Task]:
        return [t for t in self._tasks.values() if t.status == status]

    def get_ready_tasks(self) -> List[Task]:
        """获取所有依赖已满足、可以启动的任务"""
        ready = []
        for task in self._tasks.values():
            if task.status != TaskStatus.PENDING:
                continue
            deps_satisfied = all(
                self._tasks.get(dep_id) and self._tasks[dep_id].status == TaskStatus.COMPLETED
                for dep_id in task.dependencies
            )
            if deps_satisfied:
                ready.append(task)
        return ready

    def get_running_count(self) -> int:
        return len(self.get_by_status(TaskStatus.RUNNING))

    def get_tree(self, root_id: str) -> dict:
        """获取任务树的可视化表示"""
        root = self._tasks.get(root_id)
        if not root:
            return {}

        def _build_node(task_id: str) -> dict:
            task = self._tasks.get(task_id)
            if not task:
                return {"id": task_id, "error": "not found"}
            return {
                "id": task.id,
                "goal": task.goal,
                "type": task.task_type,
                "status": task.status.value,
                "children": [_build_node(cid) for cid in task.children],
            }

        return _build_node(root_id)

    def get_progress_summary(self, root_id: str) -> dict:
        """获取任务树的整体进度"""
        root = self._tasks.get(root_id)
        if not root:
            return {}

        total = 0
        completed = 0
        failed = 0
        running = 0

        def _count(task_id: str):
            nonlocal total, completed, failed, running
            task = self._tasks.get(task_id)
            if not task:
                return
            total += 1
            if task.status == TaskStatus.COMPLETED:
                completed += 1
            elif task.status == TaskStatus.FAILED:
                failed += 1
            elif task.status == TaskStatus.RUNNING:
                running += 1
            for cid in task.children:
                _count(cid)

        _count(root_id)
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "pending": total - completed - failed - running,
            "percent": int((completed / total) * 100) if total > 0 else 0,
        }

    def all_tasks(self) -> List[Task]:
        return list(self._tasks.values())
