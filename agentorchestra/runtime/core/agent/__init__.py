"""agentorchestra.runtime.core.agent - Agent 基类与生命周期。

- ``base.py``      Agent 抽象基类（决策/工具/记忆/子代理/持久化集成）
- ``lifecycle.py`` Agent 异步生命周期事件系统（AgentEvent / EventType / Hook）
"""

from .base import Agent

__all__ = ["Agent"]
