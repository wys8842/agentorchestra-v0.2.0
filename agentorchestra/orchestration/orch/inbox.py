"""inbox - 持久化消息队列 + 回执 + 重试（M2 图通信）。

基于 CheckpointStore.inbox_messages / inbox_acks 表。
支持优先级（高→低）与 max_depth 背压。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..state.records import InboxMessage

if TYPE_CHECKING:
    from ..state.checkpoint import CheckpointStore


class InboxDepthExceeded(Exception):
    """Inbox 队列深度超限（背压信号）。

    调用方（scheduler._route_downstream）应捕获此异常停止向下游投递，
    并把当前节点标记为错误。
    """


class Inbox:
    """持久化 Inbox（M2 图通信），支持优先级与队列深度上限。"""

    def __init__(
        self,
        store: "CheckpointStore",
        default_ttl_seconds: int = 604800,
        max_depth: int = 0,
    ):
        self.store = store
        self.default_ttl_seconds = default_ttl_seconds
        self.max_depth = max_depth

    async def depth(self, thread_id: str) -> int:
        """返回指定 thread 当前 queued 消息数（用于背压检查）。"""
        msgs = await self.store.list_pending_messages(thread_id, limit=1_000_000)
        return len(msgs)

    async def send(
        self,
        graph_id: str,
        thread_id: str,
        to_node: str,
        content: Dict[str, Any],
        from_node: Optional[str] = None,
        condition: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        priority: int = 0,
    ) -> str:
        """入队一条消息（priority 越高越先被 poll，默认 0）。"""
        if self.max_depth > 0:
            cur = await self.depth(thread_id)
            if cur >= self.max_depth:
                raise InboxDepthExceeded(
                    f"thread {thread_id} inbox depth {cur} >= max {self.max_depth}"
                )
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
            priority=priority,
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
        ack_token = f"ack-{uuid.uuid4().hex[:12]}"
        await self.store.mark_delivered(msg_id, ack_token)
        return ack_token

    async def ack(self, msg_id: str, ack_token: Optional[str] = None,
                  status: str = "acked") -> None:
        await self.store.ack_message(msg_id, ack_token, status)

    async def mark_failed(self, msg_id: str, error: str, attempts: int) -> None:
        await self.store.mark_failed(msg_id, error, attempts)

    async def cleanup(self) -> int:
        return await self.store.delete_expired_messages()


__all__ = ["Inbox", "InboxDepthExceeded"]