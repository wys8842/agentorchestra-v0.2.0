"""inbox - 持久化消息队列 + 回执 + 重试（M2 图通信）。

基于 CheckpointStore.inbox_messages / inbox_acks 表。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..state.records import InboxMessage

if TYPE_CHECKING:
    from ..state.checkpoint import CheckpointStore


class Inbox:
    """持久化 Inbox。

    用法：
        inbox = Inbox(store)
        msg_id = await inbox.send(graph_id, thread_id, "from", "to", {"task": "..."})
        msgs = await inbox.poll(thread_id, to_node="coder")
        await inbox.ack(msg_id, ack_token)
    """

    def __init__(
        self,
        store: "CheckpointStore",
        default_ttl_seconds: int = 604800,  # 7 天
    ):
        self.store = store
        self.default_ttl_seconds = default_ttl_seconds

    async def send(
        self,
        graph_id: str,
        thread_id: str,
        to_node: str,
        content: Dict[str, Any],
        from_node: Optional[str] = None,
        condition: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        """入队一条消息。返回 msg_id。"""
        msg_id = f"msg-{uuid.uuid4().hex[:12]}"
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        msg = InboxMessage(
            msg_id=msg_id,
            graph_id=graph_id,
            thread_id=thread_id,
            from_node=from_node,
            to_node=to_node,
            content=content,
            condition=condition,
            status="queued",
            expires_at=datetime.now() + timedelta(seconds=ttl),
        )
        await self.store.enqueue_message(msg)
        return msg_id

    async def poll(
        self,
        thread_id: str,
        to_node: Optional[str] = None,
        limit: int = 100,
    ) -> List[InboxMessage]:
        """取出 queued 消息（不消费，投递后再标 delivered/acked）。"""
        return await self.store.list_pending_messages(thread_id, to_node, limit)

    async def mark_delivered(self, msg_id: str) -> str:
        """标记投递，返回 ack_token。"""
        ack_token = f"ack-{uuid.uuid4().hex[:12]}"
        await self.store.mark_delivered(msg_id, ack_token)
        return ack_token

    async def ack(self, msg_id: str, ack_token: Optional[str] = None,
                  status: str = "acked") -> None:
        """写回执。"""
        await self.store.ack_message(msg_id, ack_token, status)

    async def mark_failed(self, msg_id: str, error: str, attempts: int) -> None:
        await self.store.mark_failed(msg_id, error, attempts)

    async def cleanup(self) -> int:
        """清理过期消息。"""
        return await self.store.delete_expired_messages()


__all__ = ["Inbox"]
