"""migration - TaskTool → Graph 迁移 helper + 指南（M2）。

roadmap §4.2『现有 TaskTool 保留为简单场景工具；新通信层走 Graph/Inbox』。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from agentorchestra.runtime.core.agent import Agent

from .nodes import AgentNode

if TYPE_CHECKING:
    pass


class TaskToolGraphAdapter:
    """把 TaskTool 兼容的 agent_factory 包成 Graph 节点。

    TaskTool 的 agent_factory 签名是 agent_factory(agent_type) -> Agent。
    本适配器暴露一个 Graph 工厂方法，把固定的 agent_type 绑定为 AgentNode。

    用法：
        adapter = TaskToolGraphAdapter(agent_factory, agent_type="react")
        graph.add_node("coder", adapter.make_node("coder", task_key="task"))
    """

    def __init__(
        self,
        agent_factory: Callable[[str], Agent],
        agent_type: str = "react",
    ):
        self.agent_factory = agent_factory
        self.agent_type = agent_type

    def make_node(self, input_key: str = "task") -> AgentNode:
        """创建绑定固定 agent_type 的 AgentNode。"""
        def _factory() -> Agent:
            return self.agent_factory(self.agent_type)

        return AgentNode(agent_factory=_factory, input_key=input_key)


# ---------------------------------------------------------------- migration guide


GUIDE = """
TaskTool → Graph 迁移指南
==========================

何时用 TaskTool（保持现状）：
- 一次性子任务：主 Agent 调一次 TaskTool 启动子 agent，拿回摘要
- 无跨 agent 协作 / 无条件分支 / 无需消息回溯

何时用 Graph（M2 新通信层）：
- 多 agent 对等协作（编码→审查→测试）
- 条件路由（审批通过→部署；驳回→重算）
- 有界回环（reviewer 驳回循环回写 coder，最多 N 次）
- 消息需 7 天回溯与投递回执

迁移示例（父子 tool → 图）：
    旧：parent.run("帮我实现X") 内部用 TaskTool 启动 coder+reviewer
    新：g = Graph(store=checkpoint_store)
        g.add_node("coder", AgentNode(agent_factory=coder_factory))
        g.add_node("reviewer", AgentNode(agent_factory=reviewer_factory))
        g.add_edge("coder", "reviewer", when="done")
        g.add_edge("reviewer", "coder", when="rejected")
        await g.run({"task": "实现X"}, thread_id="t1")
"""

__all__ = ["TaskToolGraphAdapter", "GUIDE"]
