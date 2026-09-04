"""agentorchestra.orchestration - Agent 通信升到图/DAG（M2 / P2）。

路线图 §4。设计见 docs/superpowers/specs/2026-09-04-m2-agent-graph-design.md

公共 API：
- Graph / GraphResult / Edge
- AgentNode / RouterNode / MergeNode / FunctionalNode
- Inbox / DeliveryManager / GraphScheduler
- NodeEvent / NodeEventType / DeliveryEvent
"""

from .delivery import DeliveryManager
from .events import DeliveryEvent, NodeEvent, NodeEventType
from .graph import Edge, Graph, GraphResult, Node, NodeContext, NodeOutput
from .inbox import Inbox
from .migration import TaskToolGraphAdapter
from .nodes import AgentNode, FunctionalNode, MergeNode, RouterNode
from .scheduler import GraphScheduler

__all__ = [
    "Graph",
    "GraphResult",
    "GraphScheduler",
    "Edge",
    "Node",
    "NodeContext",
    "NodeOutput",
    "AgentNode",
    "RouterNode",
    "MergeNode",
    "FunctionalNode",
    "Inbox",
    "DeliveryManager",
    "NodeEvent",
    "NodeEventType",
    "DeliveryEvent",
    "TaskToolGraphAdapter",
]
