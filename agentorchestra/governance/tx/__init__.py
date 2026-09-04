"""agentorchestra.tx - 事务引擎运行时（M1 / P1）。

路线图 §3。设计见 docs/superpowers/specs/2026-09-03-m1-transaction-runtime-design.md

公共 API：
- TransactionCoordinator: async 事务协调器
- TxAction / TxContext / TxAbort / TxConflict / TxReplay / TxStatus
- sync_transaction / run_sync: 同步桥接
"""

from .compensation import CompensationExecutor
from .context import (
    TxAbort,
    TxAction,
    TxConflict,
    TxContext,
    TxReplay,
    TxStatus,
)
from .coordinator import TransactionCoordinator
from .dlq import DeadLetterQueue
from .idempotency import IdempotencyStore
from .lock import OptimisticLock
from .sync import run_sync
from .wal import TxActionLog

__all__ = [
    "TransactionCoordinator",
    "TxAction",
    "TxContext",
    "TxAbort",
    "TxConflict",
    "TxReplay",
    "TxStatus",
    "CompensationExecutor",
    "DeadLetterQueue",
    "IdempotencyStore",
    "OptimisticLock",
    "TxActionLog",
    "run_sync",
]
