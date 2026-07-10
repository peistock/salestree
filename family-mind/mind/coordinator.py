"""
Coordinator — 任务协调器

编排层（不是执行层）：
- 创建 Task、管理生命周期
- 根据依赖关系调度执行
- 并发控制（ThreadPoolExecutor）
- 在 Task 间传递结果（artifact store）

核心原则：AgentLoop 不改，Task 内部仍然运行现有的 Tool Calling while 循环。
Coordinator 只负责创建 Task 和调度执行。
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, Future, wait
from typing import Optional, Callable, Dict, List

from mind.task_engine import TaskStore, Task, TaskStatus, TaskType
from mind.agent_loop import AgentLoop
from mind.agent_message import AgentMessage
from mind.agent_events import AgentEvent, AgentEventType
from mind.tools import Toolkit

logger = logging.getLogger(__name__)

# 子任务的默认 system prompt
_DEFAULT_SUBAGENT_SYSTEM = """你是一个专注的子任务执行助手。用工具完成指定任务，提供简洁的总结。
工作目录路径已在工具中配置，所有文件操作自动使用任务专属目录。

【铁律】你当前是子任务执行者，禁止再创建子任务或委托他人：
- 禁止使用 delegate 工具
- 禁止使用 spawn_task 工具
- 禁止使用 get_task_status / cancel_task 工具
- 禁止要求再派生子 Agent 帮你收集信息

你必须直接使用 search_web、browse_open、fetch_webpage、read_file 等工具完成研究，然后返回一份结构化的简洁摘要（500-1500字）。摘要要包含关键事实、数据来源和核心结论，不要只写"进行中"。
"""

# 子任务禁止递归委托的工具
_FORBIDDEN_SUBAGENT_TOOLS = {"delegate", "spawn_task", "get_task_status", "cancel_task"}


class TaskCoordinator:
    """任务协调器

    职责：
    1. 创建和管理 Task 生命周期
    2. 根据依赖关系调度任务执行
    3. 在任务间传递结果（通过 artifact store）
    4. 发射任务级事件
    """

    def __init__(
        self,
        llm,
        toolkit,
        event_sink: Optional[Callable[[AgentEvent], None]] = None,
        max_concurrent: int = 3,
    ):
        self.llm = llm
        # toolkit 用于获取工具 schema 参考，每个 Task 会创建独立 Toolkit 实例隔离 work_dir
        self.toolkit = toolkit
        self.event_sink = event_sink
        self.max_concurrent = max_concurrent
        self.task_store = TaskStore()
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent)
        self._running_futures: Dict[str, Future] = {}
        self._shutdown = False

    def _emit(self, event: AgentEvent) -> None:
        if self.event_sink:
            try:
                self.event_sink(event)
            except Exception as e:
                logger.warning(f"Coordinator 事件发送失败: {e}")

    def create_task(
        self,
        goal: str,
        task_type: str,
        parent_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
        work_dir_base=None,
    ) -> Task:
        """创建任务，发射 TASK_CREATED 事件"""
        task = self.task_store.create(
            goal=goal,
            task_type=task_type,
            parent_id=parent_id,
            dependencies=dependencies,
            work_dir_base=work_dir_base,
        )
        self._emit(AgentEvent(
            type=AgentEventType.TASK_CREATED,
            task_id=task.id,
            goal=task.goal,
            task_type=task.task_type,
            parent_id=task.parent_id,
        ))
        return task

    def run_task(
        self,
        task: Task,
        system_prompt: str = "",
        initial_messages: Optional[List[AgentMessage]] = None,
    ) -> None:
        """在后台线程中执行 Task

        内部创建 AgentLoop，传入 Task 的独立状态空间（messages、todo_store、
        plan_store、work_dir）。AgentLoop 执行完成后，更新 Task 状态并保存结果。

        Args:
            task: 要执行的任务
            system_prompt: 覆盖默认 system prompt（根任务传入完整 prompt）
            initial_messages: 覆盖默认消息列表（根任务传入完整 messages）
        """
        if self._shutdown:
            logger.warning(f"[Coordinator] 已关闭，拒绝执行任务: {task.id}")
            return

        self.task_store.update_status(task.id, TaskStatus.RUNNING)
        self._emit(AgentEvent(
            type=AgentEventType.TASK_STARTED,
            task_id=task.id,
            goal=task.goal,
        ))

        def _execute():
            try:
                # 构建初始 messages
                if initial_messages:
                    messages = list(initial_messages)
                else:
                    sys = system_prompt or _DEFAULT_SUBAGENT_SYSTEM
                    # 注入父任务上下文（artifact store 传递）
                    if task.parent_id:
                        parent = self.task_store.get(task.parent_id)
                        if parent and parent.result:
                            sys += f"\n\n【父任务结果摘要】{parent.result[:800]}"
                        # 注入父任务的 artifacts 文件列表
                        if parent and parent.artifacts:
                            artifact_files = [a for a in parent.artifacts if a.endswith(".md")]
                            if artifact_files:
                                sys += "\n\n【父任务产出文件】"
                                for af in artifact_files[:3]:
                                    sys += f"\n- {af}"
                    messages = [
                        AgentMessage.system(sys),
                        AgentMessage.user(f"任务：{task.goal}"),
                    ]

                # 每个 Task 独立 Toolkit（隔离 work_dir）
                task_toolkit = Toolkit(task.work_dir)
                # 子任务禁止递归委托，移除相关工具
                if task.parent_id:
                    task_toolkit = task_toolkit.without_tools(_FORBIDDEN_SUBAGENT_TOOLS)

                # 创建 AgentLoop（完全复用现有代码）
                loop = AgentLoop(
                    llm=self.llm,
                    toolkit=task_toolkit,
                    todo_store=task.todo_store,
                    work_dir=task.work_dir,
                    event_sink=self._wrap_event_sink(task.id),
                    trace_store=None,
                    plan_store=task.plan_store,
                    coordinator=self,
                    current_task_id=task.id,
                )

                # 执行 Tool Calling while 循环
                result = loop.run(
                    messages=messages,
                    max_iterations=task.max_iterations,
                    checkpoint_path=task.checkpoint_path,
                )

                # 保存结果到 Task
                task.result = result.get("reply", "")
                task.save_artifact("result.md", task.result)

                # 保存 care_signals 到 artifact
                care_signals = result.get("care_signals", [])
                if care_signals:
                    task.save_artifact(
                        "care_signals.json",
                        json.dumps(care_signals, ensure_ascii=False)
                    )

                return result

            except Exception as e:
                logger.error(f"[Coordinator] 任务执行异常: {task.id}, {e}", exc_info=True)
                task.error = str(e)
                task.result = f"任务执行失败: {e}"
                raise

        # 提交到线程池
        future = self._executor.submit(_execute)
        self._running_futures[task.id] = future
        future.add_done_callback(lambda f, tid=task.id: self._on_task_done(tid, f))

    def _wrap_event_sink(self, task_id: str):
        """包装事件 sink，给所有事件添加 task_id"""
        def wrapper(event: AgentEvent):
            event.task_id = task_id
            self._emit(event)
        return wrapper

    def _on_task_done(self, task_id: str, future: Future) -> None:
        """任务完成回调 — 更新状态、发射事件、尝试启动下游任务"""
        self._running_futures.pop(task_id, None)
        task = self.task_store.get(task_id)
        if not task:
            return

        try:
            future.result()  # 触发异常（如果有）
            if not task.error:
                self.task_store.update_status(task_id, TaskStatus.COMPLETED)
                self._emit(AgentEvent(
                    type=AgentEventType.TASK_COMPLETED,
                    task_id=task_id,
                    goal=task.goal,
                    result_preview=task.result[:200] if task.result else "",
                ))
                logger.info(f"[Coordinator] 任务完成: {task_id}")
        except Exception as e:
            self.task_store.update_status(task_id, TaskStatus.FAILED)
            self._emit(AgentEvent(
                type=AgentEventType.TASK_FAILED,
                task_id=task_id,
                goal=task.goal,
                error=str(e),
            ))
            logger.error(f"[Coordinator] 任务失败: {task_id}, {e}")

        # 尝试启动下游依赖已满足的任务
        self.start_ready_tasks()

    def start_ready_tasks(self) -> None:
        """启动所有依赖已满足、且未超并发上限的任务"""
        if self._shutdown:
            return

        running_count = len(self._running_futures)
        if running_count >= self.max_concurrent:
            return

        ready = self.task_store.get_ready_tasks()
        # 排除已经在运行的
        ready = [t for t in ready if t.id not in self._running_futures]

        slots = self.max_concurrent - running_count
        for task in ready[:slots]:
            self.run_task(task)

    def cancel_task(self, task_id: str) -> None:
        """取消任务（级联取消子任务）"""
        task = self.task_store.get(task_id)
        if not task:
            return

        # 级联取消子任务
        for child_id in list(task.children):
            self.cancel_task(child_id)

        # 从运行中移除（Future 无法真正中断线程，只能标记状态）
        future = self._running_futures.pop(task_id, None)
        if future and not future.done():
            # Python ThreadPoolExecutor 不支持强制终止线程
            pass

        if not task.is_terminal():
            self.task_store.update_status(task_id, TaskStatus.CANCELLED)
            self._emit(AgentEvent(
                type=AgentEventType.TASK_CANCELLED,
                task_id=task_id,
                goal=task.goal,
            ))
            logger.info(f"[Coordinator] 任务已取消: {task_id}")

    def wait_for_task(self, task_id: str, timeout: Optional[float] = None) -> bool:
        """等待指定任务完成

        Returns:
            True: 任务已完成（成功或失败）
            False: 超时或任务不存在
        """
        future = self._running_futures.get(task_id)
        if not future:
            task = self.task_store.get(task_id)
            if task and task.is_terminal():
                return True
            return False

        try:
            future.result(timeout=timeout)
            return True
        except Exception:
            return False

    def wait_for_all(self, timeout: Optional[float] = None) -> bool:
        """等待所有正在运行的任务完成

        Returns:
            True: 所有任务都已完成
            False: 超时
        """
        futures = list(self._running_futures.values())
        if not futures:
            # 检查是否还有非终端状态的任务（未被启动的 PENDING）
            pending = self.task_store.get_by_status(TaskStatus.PENDING)
            if pending:
                # 尝试启动它们
                self.start_ready_tasks()
                futures = list(self._running_futures.values())
                if not futures:
                    return True
            else:
                return True

        done, not_done = wait(futures, timeout=timeout)
        return len(not_done) == 0

    def get_result(self, task_id: str) -> Optional[str]:
        """获取任务结果"""
        task = self.task_store.get(task_id)
        if not task:
            return None
        return task.result

    def get_task_tree(self, root_id: str) -> dict:
        """获取任务树的可视化表示"""
        return self.task_store.get_tree(root_id)

    def get_progress(self, root_id: str) -> dict:
        """获取任务树的整体进度"""
        return self.task_store.get_progress_summary(root_id)

    def shutdown(self):
        """关闭协调器，取消所有未完成任务"""
        self._shutdown = True
        for task_id in list(self._running_futures.keys()):
            self.cancel_task(task_id)
        self._executor.shutdown(wait=False)
