"""
任务栈 — 真打断-恢复（借鉴 Hermes）

核心设计：
- 每个用户有一个任务栈（LIFO）
- 长任务执行时被打断 → 当前任务状态压入栈
- 回答完打断问题后 → 自动恢复栈顶任务
- 支持多层嵌套（老人连续插嘴多次）

对比旧版（Phase 2.x）：
- 旧版：单任务挂起，内存级，不支持嵌套
- 新版：栈结构，支持 save/load 持久化，支持多层嵌套
"""
import logging
import time
from typing import Optional, Dict, List
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class SuspendedTask:
    """被挂起的任务状态（完整快照）"""
    user_id: str
    original_query: str
    partial_result: str = ""          # 已获取的部分结果
    tool_results: List[str] = field(default_factory=list)
    # 新增：完整状态快照
    messages: List[Dict] = field(default_factory=list)  # 对话历史（去掉 system）
    todos: List[Dict] = field(default_factory=list)     # todo 列表
    iteration: int = 0                                   # 当前迭代数
    work_dir: str = ""                                   # 工作目录
    timestamp: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict) -> "SuspendedTask":
        return SuspendedTask(
            user_id=data.get("user_id", ""),
            original_query=data.get("original_query", ""),
            partial_result=data.get("partial_result", ""),
            tool_results=data.get("tool_results", []),
            messages=data.get("messages", []),
            todos=data.get("todos", []),
            iteration=data.get("iteration", 0),
            work_dir=data.get("work_dir", ""),
            timestamp=data.get("timestamp", time.time()),
        )


# 用户任务栈（user_id -> List[SuspendedTask]）
_task_stacks: Dict[str, List[SuspendedTask]] = {}


def _get_stack(user_id: str) -> List[SuspendedTask]:
    """获取用户的任务栈，不存在则创建"""
    if user_id not in _task_stacks:
        _task_stacks[user_id] = []
    return _task_stacks[user_id]


def is_interruption(user_id: str, current_query: str, thread_context: str = "") -> bool:
    """
    判断当前查询是否是打断。
    启发式规则：
    1. 栈不为空（有正在执行的长任务）
    2. 当前查询与栈顶任务主题明显不同（且更短/更急）
    3. 当前查询是简单事实查询（血压、天气、时间等）
    """
    stack = _get_stack(user_id)
    if not stack:
        return False

    top_task = stack[-1]

    # 如果当前查询包含原查询的关键词，可能是追问而非打断
    orig_keywords = set(top_task.original_query.replace("？", "").replace("?", "").split())
    curr_keywords = set(current_query.replace("？", "").replace("?", "").split())
    overlap = len(orig_keywords & curr_keywords)
    if overlap >= min(2, len(orig_keywords)):
        return False  # 追问，不是打断

    # 简单事实查询通常是打断（插嘴）
    simple_patterns = ["多少", "血压", "血糖", "几点", "天气", "温度", "药", "吃", "睡", "儿子", "孙子", "继续"]
    is_simple = any(p in current_query for p in simple_patterns) and len(current_query) < 20

    # "继续"不算打断，是恢复
    if "继续" in current_query:
        return False

    if is_simple:
        logger.info(f"检测到打断: user={user_id}, orig='{top_task.original_query}', curr='{current_query}'")
        return True

    return False


def suspend(
    user_id: str,
    original_query: str,
    partial_result: str = "",
    tool_results: List[str] = None,
    messages: List[Dict] = None,
    todos: List[Dict] = None,
    iteration: int = 0,
    work_dir: str = "",
):
    """挂起当前任务，压入栈顶"""
    task = SuspendedTask(
        user_id=user_id,
        original_query=original_query,
        partial_result=partial_result,
        tool_results=tool_results or [],
        messages=messages or [],
        todos=todos or [],
        iteration=iteration,
        work_dir=work_dir,
    )
    stack = _get_stack(user_id)
    stack.append(task)
    logger.info(f"任务已挂起并压栈: user={user_id}, depth={len(stack)}, query='{original_query}'")


def resume(user_id: str, new_query: str, new_reply: str) -> Optional[str]:
    """
    尝试恢复栈顶任务。
    返回续接文案（如果不需要续接则返回 None）

    注意：这里只是生成续接文案，真正的恢复在 agent.run() 中通过 checkpoint 完成
    """
    stack = _get_stack(user_id)
    if not stack:
        return None

    top_task = stack[-1]

    # 同一请求内正常结束，不是打断恢复，不续接
    if new_query == top_task.original_query:
        clear(user_id)  # 清空整个栈
        logger.info(f"同一请求正常结束，清空栈: user={user_id}")
        return None

    # 如果是打断后恢复，生成续接文案
    follow_up = ""
    if top_task.partial_result:
        follow_up = f"对了，刚才您问的{top_task.original_query[:20]}，我查好了：{top_task.partial_result[:100]}"
    elif top_task.tool_results:
        summary = "；".join(top_task.tool_results)[:100]
        follow_up = f"对了，刚才您问的{top_task.original_query[:20]}，我查到了：{summary}"
    else:
        follow_up = f"对了，刚才您问的{top_task.original_query[:20]}，我还没说完呢。"

    logger.info(f"任务已恢复（生成续接文案）: user={user_id}")
    return follow_up


def pop_task(user_id: str) -> Optional[SuspendedTask]:
    """弹出栈顶任务（用于恢复执行）"""
    stack = _get_stack(user_id)
    if not stack:
        return None
    task = stack.pop()
    logger.info(f"任务出栈: user={user_id}, depth={len(stack)}, query='{task.original_query}'")
    return task


def peek_task(user_id: str) -> Optional[SuspendedTask]:
    """查看栈顶任务（不出栈）"""
    stack = _get_stack(user_id)
    if not stack:
        return None
    return stack[-1]


def clear(user_id: str):
    """清空用户的任务栈"""
    if user_id in _task_stacks:
        del _task_stacks[user_id]
        logger.info(f"任务栈已清空: user={user_id}")


def has_suspended(user_id: str) -> bool:
    """检查用户是否有挂起的任务"""
    return len(_get_stack(user_id)) > 0


def stack_depth(user_id: str) -> int:
    """返回用户任务栈深度"""
    return len(_get_stack(user_id))


def get_stack_info(user_id: str) -> Optional[List[Dict]]:
    """获取任务栈信息（用于调试）"""
    stack = _get_stack(user_id)
    if not stack:
        return None
    return [
        {
            "original_query": t.original_query,
            "partial_result": t.partial_result[:100],
            "tool_count": len(t.tool_results),
            "iteration": t.iteration,
            "elapsed_seconds": time.time() - t.timestamp,
        }
        for t in stack
    ]
