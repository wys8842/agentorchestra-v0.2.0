"""events - Graph 执行事件（M2 图通信）。

供 trace / 回调 / 测试观察图执行过程。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class NodeEventType(str, Enum):
    """节点事件类型。"""

    NODE_START = "node_start"
    NODE_FINISH = "node_finish"
    NODE_ERROR = "node_error"
    NODE_SKIPPED = "node_skipped"  # 回环耗尽 / 条件不匹配


@dataclass
class NodeEvent:
    """单个节点事件。"""

    event_type: NodeEventType
    node_name: str
    graph_id: str
    thread_id: str
    message_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（时间用 ISO8601 字符串）。"""
        return {
            "event_type": self.event_type.value,
            "node_name": self.node_name,
            "graph_id": self.graph_id,
            "thread_id": self.thread_id,
            "message_id": self.message_id,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
            "error": self.error,
        }


@dataclass
class DeliveryEvent:
    """投递事件（供 delivery 重试/失败观察）。"""

    message_id: str
    to_node: str
    thread_id: str
    attempt: int
    status: str  # delivered | failed | retrying
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


__all__ = ["NodeEventType", "NodeEvent", "DeliveryEvent"]
