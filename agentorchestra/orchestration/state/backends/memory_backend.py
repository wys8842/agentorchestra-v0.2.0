"""InMemory backend - 兼容层。

保留现有 session_store.py 的行为。零 DB 依赖。
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..checkpoint import Checkpoint, CheckpointStore
from ..interrupt import Interrupt, InterruptStatus
from ..records import (
    AuditEntry,
    DLQEntry,
    IdempotencyRecord,
    InboxAck,
    InboxMessage,
    LockRecord,
)
from ..snapshot import Snapshot
from ..thread import ThreadStatus
from ..wal import WALEntry


class InMemoryCheckpointStore(CheckpointStore):
    """内存 CheckpointStore。

    - 线程安全（threading.Lock）
    - 零依赖、可序列化（to_dict/from_dict）
    - 用于：
        1. 默认 `persistence_mode='in_memory'`（无 DB 依赖）
        2. session_store.py 兼容层
        3. 单元测试
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._threads: Dict[str, Dict[str, Any]] = {}
        self._checkpoints: Dict[str, Dict[str, Checkpoint]] = {}  # thread_id -> {cp_id -> Checkpoint}
        self._wal: Dict[str, List[WALEntry]] = {}
        self._snapshots: Dict[str, List[Snapshot]] = {}
        self._interrupts: Dict[str, Interrupt] = {}
        self._locks: Dict[str, LockRecord] = {}
        self._idempotency: Dict[str, IdempotencyRecord] = {}
        self._dlq: List[DLQEntry] = []
        self._inbox_messages: Dict[str, InboxMessage] = {}
        self._inbox_acks: Dict[str, InboxAck] = {}
        self._audit: List[AuditEntry] = []

    async def init(self) -> None:
        return None

    async def close(self) -> None:
        return None

    # ---------------- Thread ----------------

    async def create_thread(
        self, thread_id: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        with self._lock:
            if thread_id in self._threads:
                return  # 已存在：忽略
            now = datetime.now()
            self._threads[thread_id] = {
                "thread_id": thread_id,
                "created_at": now,
                "updated_at": now,
                "metadata": dict(metadata or {}),
                "status": ThreadStatus.ACTIVE.value,
            }

    async def get_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            t = self._threads.get(thread_id)
            if not t:
                return None
            return {
                "thread_id": t["thread_id"],
                "status": t["status"],
                "created_at": t["created_at"].isoformat(),
                "updated_at": t["updated_at"].isoformat(),
                "metadata": dict(t["metadata"]),
            }

    async def update_thread_status(self, thread_id: str, status: str) -> None:
        with self._lock:
            t = self._threads.get(thread_id)
            if t:
                t["status"] = status
                t["updated_at"] = datetime.now()

    # ---------------- Checkpoint ----------------

    async def save_checkpoint(self, cp: Checkpoint) -> None:
        with self._lock:
            self._checkpoints.setdefault(cp.thread_id, {})[cp.checkpoint_id] = cp

    async def load_checkpoint(
        self, thread_id: str, checkpoint_id: str
    ) -> Optional[Checkpoint]:
        with self._lock:
            cps = self._checkpoints.get(thread_id, {})
            return cps.get(checkpoint_id)

    async def list_checkpoints(
        self, thread_id: str, limit: int = 50
    ) -> List[Checkpoint]:
        with self._lock:
            cps = self._checkpoints.get(thread_id, {})
            ordered = sorted(cps.values(), key=lambda c: c.created_at, reverse=True)
            return ordered[:limit]

    async def latest_checkpoint(self, thread_id: str) -> Optional[Checkpoint]:
        cps = await self.list_checkpoints(thread_id, limit=1)
        return cps[0] if cps else None

    # ---------------- WAL ----------------

    async def append_wal(self, entry: WALEntry) -> int:
        with self._lock:
            seqs = self._wal.setdefault(entry.thread_id, [])
            seq = (seqs[-1].sequence_no if seqs else 0) + 1
            entry.sequence_no = seq
            seqs.append(entry)
            return seq

    async def read_wal(
        self, thread_id: str, after_seq: int = 0, limit: int = 1000
    ) -> List[WALEntry]:
        with self._lock:
            seqs = self._wal.get(thread_id, [])
            result = [e for e in seqs if e.sequence_no > after_seq]
            return result[:limit]

    async def max_wal_seq(self, thread_id: str) -> int:
        with self._lock:
            seqs = self._wal.get(thread_id, [])
            return seqs[-1].sequence_no if seqs else 0

    # ---------------- Snapshot ----------------

    async def save_snapshot(self, snap: Snapshot) -> None:
        with self._lock:
            self._snapshots.setdefault(snap.thread_id, []).append(snap)

    async def latest_snapshot(self, thread_id: str) -> Optional[Snapshot]:
        with self._lock:
            snaps = self._snapshots.get(thread_id, [])
            return snaps[-1] if snaps else None

    # ---------------- Interrupt ----------------

    async def create_interrupt(self, intr: Interrupt) -> None:
        with self._lock:
            self._interrupts[intr.token] = intr

    async def resolve_interrupt(self, token: str, response: Dict[str, Any]) -> None:
        with self._lock:
            intr = self._interrupts.get(token)
            if intr:
                intr.status = InterruptStatus.RESUMED
                intr.response = response
                intr.resolved_at = datetime.now()

    async def get_interrupt(self, token: str) -> Optional[Interrupt]:
        with self._lock:
            return self._interrupts.get(token)

    # ---------------- 锁（M1） ----------------

    async def acquire_lock(
        self, resource_key: str, owner_tx: str, ttl_seconds: float = 30.0
    ) -> Optional[LockRecord]:
        now = datetime.now()
        with self._lock:
            existing = self._locks.get(resource_key)
            if existing is not None:
                exp = existing.expires_at
                if exp is None or exp > now:
                    return None
                # 过期：抢占
                del self._locks[resource_key]
            record = LockRecord(
                resource_key=resource_key,
                version=0,
                owner_tx=owner_tx,
                held_since=now,
                expires_at=(
                    now + timedelta(seconds=ttl_seconds) if ttl_seconds else None
                ),
            )
            self._locks[resource_key] = record
            return record

    async def compare_and_swap(
        self, resource_key: str, expected_version: int, owner_tx: str
    ) -> bool:
        with self._lock:
            existing = self._locks.get(resource_key)
            if existing is None or existing.owner_tx != owner_tx:
                return False
            if existing.version != expected_version:
                return False
            existing.version = expected_version + 1
            return True

    async def release_lock(self, resource_key: str, owner_tx: str) -> bool:
        with self._lock:
            existing = self._locks.get(resource_key)
            if existing is None or existing.owner_tx != owner_tx:
                return False
            del self._locks[resource_key]
            return True

    async def read_version(self, resource_key: str) -> Optional[int]:
        with self._lock:
            existing = self._locks.get(resource_key)
            return existing.version if existing else None

    # ---------------- 幂等（M1） ----------------

    async def put_idempotency(self, record: IdempotencyRecord) -> None:
        with self._lock:
            self._idempotency[record.idempotency_key] = record

    async def get_idempotency(self, key: str) -> Optional[IdempotencyRecord]:
        with self._lock:
            record = self._idempotency.get(key)
            if record is None:
                return None
            if record.expires_at is not None and record.expires_at < datetime.now():
                return None
            return record

    async def delete_expired_idempotency(self) -> int:
        now = datetime.now()
        with self._lock:
            expired = [
                k for k, v in self._idempotency.items()
                if v.expires_at is not None and v.expires_at < now
            ]
            for k in expired:
                del self._idempotency[k]
            return len(expired)

    # ---------------- DLQ（M1） ----------------

    async def enqueue_dlq(self, entry: DLQEntry) -> None:
        with self._lock:
            self._dlq.append(entry)

    async def list_dlq(
        self, limit: int = 100, status: str = "open"
    ) -> List[DLQEntry]:
        with self._lock:
            return [
                e for e in self._dlq if e.status == status
            ][:limit]

    # ---------------- Inbox（M2） ----------------

    async def enqueue_message(self, msg: InboxMessage) -> None:
        with self._lock:
            self._inbox_messages[msg.msg_id] = msg

    async def list_pending_messages(
        self, thread_id: str, to_node: Optional[str] = None, limit: int = 100
    ) -> List[InboxMessage]:
        with self._lock:
            result = []
            for m in self._inbox_messages.values():
                if m.thread_id != thread_id or m.status != "queued":
                    continue
                if to_node is not None and m.to_node != to_node:
                    continue
                if m.expired:
                    continue
                result.append(m)
            result.sort(key=lambda m: m.created_at)
            return result[:limit]

    async def mark_delivered(self, msg_id: str, ack_token: str) -> None:
        with self._lock:
            m = self._inbox_messages.get(msg_id)
            if m:
                m.status = "delivered"
                m.attempts += 1
                m.delivered_at = datetime.now()
                m.ack_token = ack_token

    async def mark_failed(self, msg_id: str, error: str, attempts: int) -> None:
        with self._lock:
            m = self._inbox_messages.get(msg_id)
            if m:
                m.status = "failed"
                m.attempts = attempts

    async def ack_message(
        self, msg_id: str, ack_token: Optional[str] = None, status: str = "acked"
    ) -> None:
        with self._lock:
            ack = InboxAck(msg_id=msg_id, ack_token=ack_token, status=status)
            self._inbox_acks[msg_id] = ack
            m = self._inbox_messages.get(msg_id)
            if m:
                m.status = status

    async def delete_expired_messages(self) -> int:
        now = datetime.now()
        with self._lock:
            expired = [
                mid for mid, m in self._inbox_messages.items()
                if m.expires_at is not None and m.expires_at < now
            ]
            for mid in expired:
                del self._inbox_messages[mid]
            return len(expired)

    # ---------------- 审计（M3 WORM） ----------------

    async def append_audit(self, entry: AuditEntry) -> None:
        with self._lock:
            self._audit.append(entry)

    async def query_audit(
        self,
        limit: int = 100,
        principal: Optional[str] = None,
        resource: Optional[str] = None,
    ) -> List[AuditEntry]:
        with self._lock:
            entries = self._audit
            if principal:
                entries = [e for e in entries if e.principal == principal]
            if resource:
                entries = [e for e in entries if e.resource == resource]
            return list(reversed(entries))[:limit]
