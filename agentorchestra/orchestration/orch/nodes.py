"""nodes - AgentNode / RouterNode / MergeNode（M2 图通信）。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from agentorchestra.runtime.core.agent import Agent

from .graph import Node, NodeContext, NodeOutput

if TYPE_CHECKING:
    pass


class AgentNode(Node):
    """Agent 节点：接收消息 → 执行 Agent → 输出结果。

    - async 优先：agent.arun(input)；无 arun 则 run_in_executor(run)
    - 输入：message["task"]（默认键）作为 Agent 输入；可通过 input_key 定制。
    """

    def __init__(
        self,
        agent_factory: Callable[[], Agent],
        system_prompt: Optional[str] = None,
        input_key: str = "task",
    ):
        """初始化 AgentNode。

        Args:
            agent_factory: 返回 Agent 实例的工厂（无参）。
                兼容 TaskTool 的 agent_factory(agent_type)；可包一层 lambda。
            system_prompt: 可选覆盖 Agent 系统提示词
            input_key: 从消息 dict 取 Agent 输入的键（默认 "task"）
        """
        self._agent_factory = agent_factory
        self._system_prompt = system_prompt
        self._input_key = input_key

    def create_agent(self) -> Agent:
        """实例化 Agent（可选应用 system_prompt 覆盖）。"""
        agent = self._agent_factory()
        if self._system_prompt is not None:
            agent.system_prompt = self._system_prompt
        return agent

    async def run(self, message: Dict[str, Any], ctx: NodeContext) -> NodeOutput:
        agent = self.create_agent()
        task = message.get(self._input_key, message)

        # async 优先
        if hasattr(agent, "arun"):
            result = await agent.arun(str(task) if not isinstance(task, str) else task)
        else:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: agent.run(str(task) if not isinstance(task, str) else task)
            )

        return NodeOutput(
            result=result,
            route=None,  # 由 scheduler 按下游边 when 匹配；AgentNode 默认无条件
            data={"agent_type": type(agent).__name__},
        )


class RouterNode(Node):
    """路由节点：输入消息 → 决策函数 → 返回 when 标签。

    - route_fn(message, ctx) -> str：返回要激活的条件边标签
    - 无匹配边 → 消息被丢弃（不投递）
    """

    def __init__(self, route_fn: Callable[[Dict[str, Any], NodeContext], str]):
        self._route_fn = route_fn

    async def run(self, message: Dict[str, Any], ctx: NodeContext) -> NodeOutput:
        label = self._route_fn(message, ctx)
        return NodeOutput(result=label, route=label or None)


class MergeNode(Node):
    """汇聚节点：把多上游消息合并为单条输出。

    语义：scheduler 保证该节点在所有入边消息都到达后才执行一次（见 scheduler 的
    fan-in 等待逻辑）。此 run 直接合并内容。
    """

    async def run(self, messages: Any, ctx: NodeContext) -> NodeOutput:
        """由 scheduler 调用：messages 为待合并消息列表。"""
        if isinstance(messages, dict) and "messages" in messages:
            msgs = messages["messages"]
        else:
            msgs = [messages] if messages else []
        combined: Dict[str, Any] = {}
        for m in msgs:
            if isinstance(m, dict):
                combined.update(m)
        return NodeOutput(result=combined)


class FunctionalNode(Node):
    """纯函数节点：输入消息 → 函数 → 输出（无 Agent/无 IO）。"""

    def __init__(self, fn: Callable[[Dict[str, Any], NodeContext], NodeOutput]):
        self._fn = fn

    async def run(self, message: Dict[str, Any], ctx: NodeContext) -> NodeOutput:
        return self._fn(message, ctx)


__all__ = ["AgentNode", "RouterNode", "MergeNode", "FunctionalNode"]
