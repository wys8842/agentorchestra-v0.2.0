"""dlq - DeadLetterQueue（M1 事务运行时）。

补偿耗尽后入库，人工介入解决。roadmap §3.2『补偿失败进 DLQ，不无限重试』。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from agentorchestra.orchestration.state.records import DLQEntry

if TYPE_CHECKING:
    from agentorchestra.orchestration.state.checkpoint import CheckpointStore


class DeadLetterQueue:
    """死信队列（基于 CheckpointStore.dead_letter 表）。"""

    def __init__(self, store: "CheckpointStore"):
        self.store = store

    async def enqueue(
        self,
        tx_id: str,
        action_name: str,
        error: str,
        attempts: int,
        extra: Dict[str, Any] | None = None,
    ) -> None:
        """入死信队列。"""
        entry = DLQEntry(
            tx_id=tx_id,
            action_name=action_name,
            error=f"{error}（attempts={attempts}）",
            attempts=attempts,
            status="open",
        )
        await self.store.enqueue_dlq(entry)

    async def list(self, limit: int = 100, status: str = "open") -> List[DLQEntry]:
        """列出死信条目。"""
        return await self.store.list_dlq(limit=limit, status=status)

    async def count(self, status: str = "open") -> int:
        """统计死信条数。"""
        return len(await self.store.list_dlq(limit=100_000, status=status))
