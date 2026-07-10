"""
AgentMessage — 应用层消息类型

在 agent.py（应用层）和 AgentLoop（LLM 运行时层）之间建立类型边界：
- AgentMessage 是应用层消息，可携带应用扩展字段
- to_llm() 在 LLM API 边界处转换为标准 dict
- from_llm() 从 LLM 返回的 dict 还原为 AgentMessage

未来如需扩展（如添加 annotations、attachments、source 等），
只需改 AgentMessage，不影响 LLM 层。
"""
from dataclasses import dataclass, field
from typing import Optional, Literal, Any


@dataclass
class AgentMessage:
    """应用层消息，封装 LLM API 格式并支持应用层扩展。"""

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""

    # LLM API 层字段
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = None
    reasoning_content: Optional[str] = None  # DeepSeek thinking mode 兼容

    # 应用层扩展字段（未来可添加 annotations、source、created_at 等）
    id: Optional[str] = None

    def to_llm(self) -> dict[str, Any]:
        """转换为 LLM API 标准格式（OpenAI 兼容）。"""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        if self.reasoning_content is not None:
            msg["reasoning_content"] = self.reasoning_content
        return msg

    @classmethod
    def from_llm(cls, msg: dict[str, Any]) -> "AgentMessage":
        """从 LLM API 标准 dict 构造 AgentMessage。"""
        return cls(
            role=msg.get("role", "user"),
            content=msg.get("content", ""),
            tool_calls=msg.get("tool_calls"),
            tool_call_id=msg.get("tool_call_id"),
            reasoning_content=msg.get("reasoning_content"),
        )

    @classmethod
    def system(cls, content: str) -> "AgentMessage":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "AgentMessage":
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str, tool_calls: Optional[list[dict]] = None, reasoning_content: Optional[str] = None) -> "AgentMessage":
        return cls(role="assistant", content=content, tool_calls=tool_calls, reasoning_content=reasoning_content)

    @classmethod
    def tool(cls, content: str, tool_call_id: str) -> "AgentMessage":
        return cls(role="tool", content=content, tool_call_id=tool_call_id)
