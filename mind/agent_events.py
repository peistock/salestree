"""
Agent 事件定义

把 Agent 循环中的进度回调改为标准事件流，
让 process_text() / TUI / 其他消费者可以统一订阅。
"""
from enum import Enum
from dataclasses import dataclass
from typing import Optional


class AgentEventType(Enum):
    """Agent 执行过程中的标准事件类型"""

    # 生命周期
    AGENT_START = "agent_start"  # 开始执行（可能携带 resumed=True）
    AGENT_END = "agent_end"      # 执行结束

    # Turn 生命周期（一轮 = 一次 LLM 调用 + 工具执行）
    TURN_START = "turn_start"
    TURN_END = "turn_end"

    # 工具执行生命周期
    TOOL_EXECUTION_START = "tool_execution_start"  # 工具开始执行
    TOOL_EXECUTION_END = "tool_execution_end"      # 工具执行结束

    # 计划与步骤生命周期
    PLAN_CREATED = "plan_created"  # 计划生成完成
    STEP_START = "step_start"      # 开始执行某一步
    STEP_END = "step_end"          # 某一步执行完成

    # 任务生命周期
    TASK_CREATED = "task_created"      # 任务创建
    TASK_STARTED = "task_started"      # 任务开始执行
    TASK_COMPLETED = "task_completed"  # 任务完成
    TASK_FAILED = "task_failed"        # 任务失败
    TASK_CANCELLED = "task_cancelled"  # 任务取消

    # 心跳
    HEARTBEAT = "heartbeat"  # 长任务进度提示

    # 流式输出
    TOKEN = "token"  # 最终回复的逐字 token


@dataclass
class AgentEvent:
    """Agent 事件标准信封"""

    type: AgentEventType
    tool: Optional[str] = None          # 工具名（TOOL_EXECUTION_* 事件）
    step: Optional[int] = None          # 当前步数（STEP_START/STEP_END）
    total: Optional[int] = None         # 总步数（STEP_START/STEP_END / PLAN_CREATED）
    result_preview: Optional[str] = None  # 工具结果预览（TOOL_EXECUTION_END）
    message: Optional[str] = None       # 人类可读消息（HEARTBEAT / AGENT_START / PLAN_CREATED）
    resumed: bool = False               # 是否从 checkpoint 恢复
    iteration: Optional[int] = None     # 当前迭代次数
    description: Optional[str] = None   # 步骤描述（STEP_START/STEP_END）
    steps_summary: Optional[list] = None  # 计划步骤摘要列表（PLAN_CREATED）
    task_id: Optional[str] = None       # 关联的任务 ID（TASK_* 事件）
    goal: Optional[str] = None          # 任务目标（TASK_CREATED）
    task_type: Optional[str] = None     # 任务类型（TASK_CREATED）
    parent_id: Optional[str] = None     # 父任务 ID（TASK_CREATED）
    error: Optional[str] = None         # 错误信息（TASK_FAILED）
