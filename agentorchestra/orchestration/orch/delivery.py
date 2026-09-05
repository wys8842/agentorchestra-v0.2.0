"""delivery - 投递回执 / 超时 / 指数退避重试（M2 图通信）。

默认最多 5 次尝试；耗尽标记 failed 并触发 on_delivery_failed。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

from .events import DeliveryEvent

if TYPE_CHECKING:
    from .inbox import Inbox

logger = logging.getLogger("agentorchestra.orchestration.delivery")


class DeliveryManager:
    """投递管理：把 Inbox 消息派发给消费者，重试带指数退避。

    .. deprecated::
        v0.1.1（C-N8）：GraphScheduler 已内置消息投递（mark_delivered → 节点执行 →
        ack → 路由下游），本类不再被 scheduler 调用。

        作为可复用组件保留（供自定义 consumer 场景使用），但不再作为
        GraphScheduler 的投递路径。v0.2 若仍无调用方则整体移除。

        对"节点执行失败重试"的需求，建议在自定义 Node.run 内部自处理重试，
        或使用 CheckpointStore + DLQ 做持久化重试。
    """

    def __init__(
        self,
        inbox: "Inbox",
        max_attempts: int = 5,
        base_backoff: float = 0.1,
        backoff_factor: float = 2.0,
    ):
        self.inbox = inbox
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff
        self.backoff_factor = backoff_factor
        self._on_event: Optional[Callable[[DeliveryEvent], Any]] = None

    def on_event(self, cb: Callable[[DeliveryEvent], Any]) -> None:
        """注册投递事件回调。"""
        self._on_event = cb

    def _emit(self, ev: DeliveryEvent) -> None:
        if self._on_event:
            try:
                self._on_event(ev)
            except Exception:
                pass

    async def deliver(
        self,
        thread_id: str,
        msg: Any,
        consumer: Callable[[Any], Any],
        on_delivery_failed: Optional[Callable[[Any], Any]] = None,
    ) -> bool:
        """投递一条消息给 consumer，失败指数退避重试。

        Args:
            thread_id: thread id
            msg: InboxMessage
            consumer: async consumer（接收 msg 内容）
            on_delivery_failed: 耗尽后的回调

        Returns:
            True 成功 / False 耗尽失败
        """
        attempt = 0
        last_error: Optional[str] = None

        while attempt < self.max_attempts:
            attempt += 1
            try:
                result = consumer(msg.content)
                if asyncio.iscoroutine(result):
                    await result
                # 投递成功：标记 + ack
                ack_token = await self.inbox.mark_delivered(msg.msg_id)
                await self.inbox.ack(msg.msg_id, ack_token, "acked")
                self._emit(DeliveryEvent(
                    message_id=msg.msg_id, to_node=msg.to_node,
                    thread_id=thread_id, attempt=attempt, status="delivered",
                ))
                return True
            except Exception as e:
                last_error = str(e)
                if attempt < self.max_attempts:
                    self._emit(DeliveryEvent(
                        message_id=msg.msg_id, to_node=msg.to_node,
                        thread_id=thread_id, attempt=attempt, status="retrying",
                        error=last_error,
                    ))
                    delay = self.base_backoff * (self.backoff_factor ** (attempt - 1))
                    await asyncio.sleep(delay)

        # 耗尽
        await self.inbox.mark_failed(msg.msg_id, last_error or "unknown", attempt)
        self._emit(DeliveryEvent(
            message_id=msg.msg_id, to_node=msg.to_node, thread_id=thread_id,
            attempt=attempt, status="failed", error=last_error,
        ))
        if on_delivery_failed:
            try:
                out = on_delivery_failed(msg)
                if asyncio.iscoroutine(out):
                    await out
            except Exception as e:
                logger.warning(f"on_delivery_failed callback error: {e}")
        return False


__all__: list = []  #
