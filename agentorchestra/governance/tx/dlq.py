"""dlq - DeadLetterQueue（M1 事务运行时）。

补偿耗尽后入库，人工介入解决。roadmap §3.2『补偿失败进 DLQ，不无限重试』。

： resolve() / replay() API，允许人工干预后标记为 resolved
或重新触发补偿；扩展 store ABC 与实现。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from agentorchestra.orchestration.state.records import DLQEntry

if TYPE_CHECKING:
    from agentorchestra.orchestration.state.checkpoint import CheckpointStore

    from .coordinator import TransactionCoordinator

logger = logging.getLogger("agentorchestra.tx.dlq")


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

    async def resolve(self, dlq_id: int, note: Optional[str] = None) -> bool:
        """（C14）：将 DLQ 条目标记为已解决（人工介入完成）。

        Returns:
            True 如果标记成功；False 如果条目不存在。
        """
        try:
            await self.store.resolve_dlq(dlq_id, note=note)
            logger.info("DLQ entry %s 标记为 resolved (note=%s)", dlq_id, note)
            return True
        except AttributeError:
            logger.warning("store 不支持 resolve_dlq；请升级到 v0.1+ 后端")
            return False

    async def replay(
        self,
        dlq_id: int,
        coordinator: "TransactionCoordinator",
        note: Optional[str] = None,
    ) -> bool:
        """（C14）：重新触发补偿动作（从 DLQ 取 action → coordinator 重跑）。

        Returns:
            True 如果 replay 成功；False 如果条目不存在或 replay 失败。
        """
        # 取出 DLQ 条目
        items = await self.store.list_dlq(limit=1_000_000, status="open")
        target = next((e for e in items if e.id == dlq_id), None)
        if target is None:
            logger.warning("DLQ entry %s 不存在或已 resolved", dlq_id)
            return False

        action = coordinator.get_action(target.action_name)
        if action is None or action.compensate_fn is None:
            logger.warning("DLQ entry %s action=%s 无补偿函数，跳过 replay", dlq_id, target.action_name)
            return False

        # 重新触发补偿
        try:
            # 构造一个最小 ctx（仅含 tx_id，无 store/lock 等）
            from agentorchestra.governance.tx.context import TxContext, TxStatus

            ctx = TxContext(
                tx_id=target.tx_id,
                coordinator=coordinator,
                principal="replay",
                roles=["system"],
                permission_checker=None,
            )
            ctx.status = TxStatus.COMPENSATING
            result = action.compensate_fn({}, ctx)
            if hasattr(result, "__await__"):
                await result  # type: ignore[func-returns-value]
            await self.resolve(dlq_id, note=f"replayed: {note or 'auto'}")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("DLQ replay 失败 entry=%s: %s", dlq_id, e)
            return False
