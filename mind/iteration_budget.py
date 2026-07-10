"""
迭代预算管理（Hermes iteration_budget.py 精简适配）
- 时间预算：防止单个任务无限运行
- Token 预算：防止上下文无限膨胀
- 迭代预算：保留 max_iterations 控制
- 预算耗尽时提供优雅退出和状态保存支持
"""
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# 默认预算
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_MAX_DURATION_SECONDS = 180  # 3 分钟
DEFAULT_MAX_TOTAL_TOKENS = 32000


def _estimate_text_tokens(text: str) -> int:
    """粗略估算 token 数"""
    if not text:
        return 0
    return int(len(text) * 0.6)


def _estimate_messages_tokens(messages: list) -> int:
    """估算消息列表 token 数（适配 OpenAI 格式）"""
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        if isinstance(content, list):
            content = " ".join(str(item.get("text", "")) for item in content)
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                total += _estimate_text_tokens(tc.get("function", {}).get("arguments", ""))
        total += _estimate_text_tokens(content)
    return total


@dataclass
class IterationBudget:
    """跟踪 Agent 循环的三维预算"""
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_duration_seconds: float = DEFAULT_MAX_DURATION_SECONDS
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS

    start_time: float = field(default_factory=time.monotonic)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    stopped_reason: Optional[str] = None

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def check(self, iteration: int, messages: list, response_content: str = "") -> Optional[str]:
        """
        检查预算是否耗尽。返回停止原因或 None。
        调用方应在每轮迭代开始时调用。
        """
        self.total_input_tokens = _estimate_messages_tokens(messages)
        self.total_output_tokens += _estimate_text_tokens(response_content)

        if iteration >= self.max_iterations:
            return f"iteration_budget_exceeded:{iteration}"

        if self.elapsed_seconds >= self.max_duration_seconds:
            return f"time_budget_exceeded:{self.elapsed_seconds:.0f}s"

        if self.total_tokens >= self.max_total_tokens:
            return f"token_budget_exceeded:{self.total_tokens}"

        return None

    def should_compress(self, threshold_ratio: float = 0.75) -> bool:
        """根据 token 预算判断是否需要主动压缩上下文"""
        return self.total_tokens >= int(self.max_total_tokens * threshold_ratio)

    def summary(self) -> dict:
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "max_iterations": self.max_iterations,
            "max_duration_seconds": self.max_duration_seconds,
            "max_total_tokens": self.max_total_tokens,
            "stopped_reason": self.stopped_reason,
        }

    def mark_stopped(self, reason: str):
        """标记预算已耗尽"""
        self.stopped_reason = reason
        logger.warning(f"[IterationBudget] 预算耗尽: {reason}")


def build_budget(
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_duration_seconds: Optional[float] = None,
    max_total_tokens: Optional[int] = None,
) -> IterationBudget:
    """从环境变量或参数构建预算对象"""
    duration = max_duration_seconds
    if duration is None:
        duration = float(os.getenv("AGENT_MAX_DURATION_SECONDS", DEFAULT_MAX_DURATION_SECONDS))
    tokens = max_total_tokens
    if tokens is None:
        tokens = int(os.getenv("AGENT_MAX_TOTAL_TOKENS", DEFAULT_MAX_TOTAL_TOKENS))
    return IterationBudget(
        max_iterations=max_iterations,
        max_duration_seconds=duration,
        max_total_tokens=tokens,
    )
