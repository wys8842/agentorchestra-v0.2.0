"""Thread - 会话/任务管理。

    一个 thread = 一个独立的 Agent/任务运行实例。thread 内所有 checkpoint/WAL/snapshot 按 thread_id 隔离。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from .checkpoint import Checkpoint, CheckpointStore
from .wal import WALEntry


class ThreadStatus(str, Enum):
    """线程状态。"""

    ACTIVE = "active"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ThreadState:
    """线程状态（in-memory 视图）。"""

    thread_id: str
    status: ThreadStatus = ThreadStatus.ACTIVE
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "ThreadState":
        """从存储行构造（兼容 datetime 或 ISO8601 字符串）。"""
        return cls(
            thread_id=row["thread_id"],
            status=ThreadStatus(row.get("status", "active")),
            metadata=row.get("metadata") or {},
            created_at=_parse_dt(row.get("created_at")),
            updated_at=_parse_dt(row.get("updated_at")),
        )


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None
    return None


class ThreadManager:
    """线程/任务生命周期管理。

    用法：
        manager = ThreadManager(store)
        tid = manager.create_thread(metadata={"x": 1})
        mgr.update_status(tid, ThreadStatus.COMPLETED)
    """

    def __init__(self, store: CheckpointStore):
        self.store = store

    async def create_thread(
        self, thread_id: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """创建 thread（默认生成 UUID4）。"""
        if thread_id is None:
            import uuid as _uuid

            thread_id = f"thr-{_uuid.uuid4().hex[:12]}"
        await self.store.create_thread(thread_id, metadata or {})
        return thread_id

    async def get(self, thread_id: str) -> Optional[ThreadState]:
        row = await self.store.get_thread(thread_id)
        return ThreadState.from_row(row) if row else None

    async def update_status(self, thread_id: str, status: ThreadStatus) -> None:
        await self.store.update_thread_status(thread_id, status.value)

    async def latest_checkpoint(self, thread_id: str) -> Optional[Checkpoint]:
        return await self.store.latest_checkpoint(thread_id)

    async def list_checkpoints(
        self, thread_id: str, limit: int = 50
    ) -> list[Checkpoint]:
        return await self.store.list_checkpoints(thread_id, limit)

    async def save_checkpoint(self, cp: Checkpoint) -> None:
        """保存 checkpoint 并自动同步一条 WAL（action_type=CHECKPOINT）。"""
        await self.store.save_checkpoint(cp)
        wal_entry = WALEntry(
            thread_id=cp.thread_id,
            action_type="checkpoint",  # type: ignore[arg-type]
            payload={
                "checkpoint_id": cp.checkpoint_id,
                "parent_id": cp.parent_id,
                "metadata_keys": list(cp.metadata.keys()),
            },
        )
        await self.store.append_wal(wal_entry)
