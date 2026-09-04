"""wal - TxActionLog（M1 事务运行时）。

复用 M0 `state.wal`（同一张 wal 表，tx_id 关联）。事务首尾写 TX_BEGIN / TX_COMMIT。
roadmap §3.3『TxActionLog: append-only 动作日志』
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from agentorchestra.orchestration.state.wal import WALActionType, WALEntry

if TYPE_CHECKING:
    from agentorchestra.orchestration.state.checkpoint import CheckpointStore


class TxActionLog:
    """事务动作日志（state.wal 薄包装）。"""

    def __init__(self, store: "CheckpointStore", thread_id: str = "default"):
        self.store = store
        self.thread_id = thread_id

    async def log_begin(self, tx_id: str, meta: Dict[str, Any]) -> None:
        """写 TX_BEGIN。"""
        await self.store.append_wal(WALEntry(
            thread_id=self.thread_id,
            action_type=WALActionType.TX_BEGIN,
            payload={"tx_id": tx_id, **meta},
            tx_id=tx_id,
        ))

    async def log_action(
        self, tx_id: str, action_name: str,
        params: Dict[str, Any], result: Any,
    ) -> None:
        """写动作执行记录（STATE_UPDATE with tx_id）。"""
        await self.store.append_wal(WALEntry(
            thread_id=self.thread_id,
            action_type=WALActionType.STATE_UPDATE,
            payload={
                "tx_id": tx_id,
                "op": "tx_action",
                "action": action_name,
                "params": params,
                "result": result,
            },
            tx_id=tx_id,
        ))

    async def log_commit(self, tx_id: str, status: str) -> None:
        """写 TX_COMMIT。"""
        await self.store.append_wal(WALEntry(
            thread_id=self.thread_id,
            action_type=WALActionType.TX_COMMIT,
            payload={"tx_id": tx_id, "status": status},
            tx_id=tx_id,
        ))
