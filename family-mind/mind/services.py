"""
Agent 基础设施服务层

职责：承载全局复用的基础设施，无会话状态。
- LLMClient：模型调用、可用性检查
- 未来可扩展：知识库、向量检索、浏览器代理等全局服务

与 AgentSession 的关系：
- AgentServices 被多个 Session 共享（单例或缓存）
- AgentSession 持有 services 引用，通过它访问基础设施
"""
from dataclasses import dataclass

from mind.llm_client import LLMClient


@dataclass
class AgentServices:
    """基础设施服务容器 — 全局复用，无会话状态"""

    llm: LLMClient

    @classmethod
    def default(cls) -> "AgentServices":
        """构造默认服务实例（生产入口）"""
        return cls(llm=LLMClient())
