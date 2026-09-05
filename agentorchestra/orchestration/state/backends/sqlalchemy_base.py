"""SQLAlchemy 2.0 async 基类。

所有 SQL backend（SQLite/PostgreSQL）共用此基类，差异仅在 dialect 与连接串。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    insert,
    select,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..checkpoint import Checkpoint, CheckpointStore, dumps_json, loads_json
from ..interrupt import Interrupt, InterruptStatus
from ..records import (
    AuditEntry,
    DLQEntry,
    IdempotencyRecord,
    InboxMessage,
    LockRecord,
)
from ..snapshot import Snapshot
from ..thread import ThreadStatus
from ..wal import WALActionType, WALEntry


class Base(DeclarativeBase):
    """SQLAlchemy ORM 元数据基类（所有 backend 表模型共享）。"""
    pass


class _ThreadRow(Base):
    __tablename__ = "threads"

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    meta_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class _CheckpointRow(Base):
    __tablename__ = "checkpoints"

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    meta_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("idx_checkpoints_thread_created", "thread_id", "created_at"),
    )


#PostgreSQL WAL sequence 名称（用于原子 sequence 分配）
_PG_WAL_SEQ_NAME = "agentorchestra_wal_seq"
#fencing_token 单调递增序列（SQL 后端）
# 生产环境建议：使用可配置的 sequence 名称，避免跨应用冲突
_PG_FENCING_SEQ_NAME = "agentorchestra_fencing_seq"


def get_fencing_seq_name(config: Optional[Dict[str, Any]] = None) -> str:
    """获取 fencing sequence 名称，支持自定义配置。

    Args:
        config: 可选配置字典，支持 "fencing_seq_name" 键

    Returns:
        sequence 名称（默认 "agentorchestra_fencing_seq"）
    """
    if config and "fencing_seq_name" in config:
        return config["fencing_seq_name"]
    return _PG_FENCING_SEQ_NAME


class _WALRow(Base):
    __tablename__ = "wal"

    # SQLite 不支持 BigInteger autoincrement；用 Integer（rowid 即 autoincrement）。
    # Postgres 端 INTEGER 也可以 BIGSERIAL 替换（后续 PG backend 覆盖）。
    wal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    tx_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("thread_id", "sequence_no", name="uq_wal_thread_seq"),
        Index("idx_wal_thread_seq", "thread_id", "sequence_no"),
    )


class _SnapshotRow(Base):
    __tablename__ = "snapshots"

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    up_to_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    meta_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class _InterruptRow(Base):
    __tablename__ = "interrupts"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    response_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class _LockRow(Base):
    __tablename__ = "locks"

    resource_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #fencing_token 列（防僵尸事务绕过 TTL 后误写）
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    owner_tx: Mapped[str] = mapped_column(String(128), nullable=False)
    held_since: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class _IdempotencyRow(Base):
    __tablename__ = "idempotency_keys"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tx_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class _DLQRow(Base):
    __tablename__ = "dead_letter"

    dlq_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tx_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action_name: Mapped[str] = mapped_column(String(255), nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class _IterationSnapshotRow(Base):
    """（H-7）：iteration snapshot 表（O(1) 查询/写入）。"""

    __tablename__ = "iteration_snapshots"

    graph_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    iteration_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class _SequenceRow(Base):
    """ C-1：SQLite 多进程安全序列（使用数据库表模拟 sequence）。

    用于替代进程内 _fencing_seq 计数器，确保跨进程单调递增。
    """
    __tablename__ = "sequences"

    seq_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    seq_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class _InboxMessageRow(Base):
    __tablename__ = "inbox_messages"

    msg_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    graph_id: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_node: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    to_node: Mapped[str] = mapped_column(String(128), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    condition: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ack_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("idx_inbox_thread_status", "thread_id", "status"),
    )


class _InboxAckRow(Base):
    __tablename__ = "inbox_acks"

    msg_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    ack_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="acked")
    acked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class _AuditLogRow(Base):
    __tablename__ = "audit_log"

    entry_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    principal: Mapped[str] = mapped_column(String(255), nullable=False)
    resource: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    obj_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    success: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    detail_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tx_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("idx_audit_resource", "resource"),
        Index("idx_audit_principal", "principal"),
    )


class SQLAlchemyCheckpointStore(CheckpointStore):
    """SQLAlchemy 2.0 async 基类。

    子类只需要指定 `_db_url` 和 `_dialect`（用于 SQL 兼容）。
    """

    _dialect: str = "generic"
    _fencing_seq: int = 0  #fencing_token 计数器（fallback）

    def __init__(self, db_url: str):
        self._db_url = db_url
        self._engine: Optional[AsyncEngine] = None
        self._initialized = False

    async def init(self) -> None:
        if self._initialized:
            return
        # echo=False 避免噪声；生产可调
        self._engine = create_async_engine(self._db_url, echo=False, future=True)
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            #PostgreSQL 路径创建 wal_seq sequence（原子 sequence 分配）
            #同步创建 fencing_seq sequence
            if self._engine.dialect.name == "postgresql":
                from sqlalchemy import text
                await conn.execute(
                    text(
                        f"CREATE SEQUENCE IF NOT EXISTS {_PG_WAL_SEQ_NAME} INCREMENT 1 START 1"
                    )
                )
                #fencing_token 序列
                await conn.execute(
                    text(
                        f"CREATE SEQUENCE IF NOT EXISTS {_PG_FENCING_SEQ_NAME} "
                        f"INCREMENT 1 START 1"
                    )
                )
        self._initialized = True

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._initialized = False

    # ---------------- Thread ----------------

    async def create_thread(
        self, thread_id: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        assert self._engine is not None
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        now = datetime.now()
        meta_str = dumps_json(metadata or {}) if metadata else None
        async with self._engine.begin() as conn:
            if self._dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                stmt: Any = sqlite_insert(_ThreadRow).values(
                    thread_id=thread_id,
                    created_at=now,
                    updated_at=now,
                    meta_json=meta_str,
                    status=ThreadStatus.ACTIVE.value,
                ).on_conflict_do_nothing(index_elements=["thread_id"])
            else:  # postgres
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                stmt = pg_insert(_ThreadRow).values(
                    thread_id=thread_id,
                    created_at=now,
                    updated_at=now,
                    meta_json=meta_str,
                    status=ThreadStatus.ACTIVE.value,
                ).on_conflict_do_nothing(index_elements=["thread_id"])
            await conn.execute(stmt)

    async def get_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        assert self._engine is not None
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(_ThreadRow).where(_ThreadRow.thread_id == thread_id)
                )
            ).first()
            if not row:
                return None
            return {
                "thread_id": row.thread_id,
                "status": row.status,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
                "metadata": loads_json(row.meta_json) if row.meta_json else {},
            }

    async def update_thread_status(self, thread_id: str, status: str) -> None:
        assert self._engine is not None
        from sqlalchemy import update

        async with self._engine.begin() as conn:
            await conn.execute(
                update(_ThreadRow)
                .where(_ThreadRow.thread_id == thread_id)
                .values(status=status, updated_at=datetime.now())
            )

    async def list_threads(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出所有 thread（snapshot worker 自动发现用）。"""
        assert self._engine is not None
        async with self._engine.connect() as conn:
            stmt = select(_ThreadRow)
            if status is not None:
                stmt = stmt.where(_ThreadRow.status == status)
            rows = (await conn.execute(stmt)).all()
            return [
                {
                    "thread_id": r.thread_id,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                    "metadata": loads_json(r.metadata_json or "{}"),
                }
                for r in rows
            ]

    # ---------------- Checkpoint ----------------

    async def save_checkpoint(self, cp: Checkpoint) -> None:
        assert self._engine is not None
        from sqlalchemy import delete

        async with self._engine.begin() as conn:
            # 已存在则覆盖
            await conn.execute(
                delete(_CheckpointRow).where(
                    (_CheckpointRow.thread_id == cp.thread_id)
                    & (_CheckpointRow.checkpoint_id == cp.checkpoint_id)
                )
            )
            await conn.execute(
                insert(_CheckpointRow).values(
                    thread_id=cp.thread_id,
                    checkpoint_id=cp.checkpoint_id,
                    parent_id=cp.parent_id,
                    state_json=dumps_json(cp.state),
                    meta_json=dumps_json(cp.metadata) if cp.metadata else None,
                    created_at=cp.created_at,
                )
            )

    async def load_checkpoint(
        self, thread_id: str, checkpoint_id: str
    ) -> Optional[Checkpoint]:
        assert self._engine is not None
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(_CheckpointRow).where(
                        (_CheckpointRow.thread_id == thread_id)
                        & (_CheckpointRow.checkpoint_id == checkpoint_id)
                    )
                )
            ).first()
            if not row:
                return None
            return Checkpoint(
                thread_id=row.thread_id,
                checkpoint_id=row.checkpoint_id,
                parent_id=row.parent_id,
                state=loads_json(row.state_json),
                metadata=loads_json(row.meta_json) if row.meta_json else {},
                created_at=row.created_at,
            )

    async def list_checkpoints(
        self, thread_id: str, limit: int = 50
    ) -> List[Checkpoint]:
        assert self._engine is not None
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(_CheckpointRow)
                    .where(_CheckpointRow.thread_id == thread_id)
                    .order_by(_CheckpointRow.created_at.desc())
                    .limit(limit)
                )
            ).all()
            return [
                Checkpoint(
                    thread_id=r.thread_id,
                    checkpoint_id=r.checkpoint_id,
                    parent_id=r.parent_id,
                    state=loads_json(r.state_json),
                    metadata=loads_json(r.meta_json) if r.meta_json else {},
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def latest_checkpoint(self, thread_id: str) -> Optional[Checkpoint]:
        cps = await self.list_checkpoints(thread_id, limit=1)
        return cps[0] if cps else None

    # ---------------- WAL ----------------

    async def append_wal(self, entry: WALEntry) -> int:
        assert self._engine is not None
        from sqlalchemy import func, select

        #原实现的 `SELECT MAX + INSERT` 非原子，并发场景会丢条。
        # 改用数据库写入序列化：
        # - SQLite：BEGIN IMMEDIATE 串行化写事务（消除 race）
        # - PostgreSQL：原生 nextval()（在 _init_schema 启用 sequence 即可）— C-N11
        # - 其他：fallback 但同一事务内读 max + insert（多数 DB 仍需应用层互斥）
        async with self._engine.begin() as conn:
            backend_dialect = self._engine.dialect.name

            if backend_dialect == "postgresql":
                #PostgreSQL 原生 nextval()（在 _init_schema 已创建 sequence）
                result = await conn.execute(
                    select(func.nextval(_PG_WAL_SEQ_NAME))
                )
                seq = result.scalar() or 1
            else:
                # SQLite / 其他：应用层 thread-keyed 互斥 + 原子 INSERT-SELECT
                # 用 UPSERT 思想，在同一事务内原子完成：read max → insert
                # 通过 SQLite 的 BEGIN IMMEDIATE 序列化写
                if backend_dialect == "sqlite":
                    from sqlalchemy import text
                    await conn.execute(text("BEGIN IMMEDIATE"))
                result = await conn.execute(
                    select(func.coalesce(func.max(_WALRow.sequence_no), 0)).where(
                        _WALRow.thread_id == entry.thread_id
                    )
                )
                current_max = result.scalar() or 0
                seq = current_max + 1

            await conn.execute(
                insert(_WALRow).values(
                    thread_id=entry.thread_id,
                    sequence_no=seq,
                    action_type=entry.action_type.value
                    if isinstance(entry.action_type, WALActionType)
                    else str(entry.action_type),
                    payload_json=dumps_json(entry.payload),
                    tx_id=entry.tx_id,
                    created_at=entry.created_at,
                )
            )
        entry.sequence_no = seq
        return seq

    async def read_wal(
        self, thread_id: str, after_seq: int = 0, limit: int = 1000
    ) -> List[WALEntry]:
        assert self._engine is not None
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(_WALRow)
                    .where(
                        (_WALRow.thread_id == thread_id)
                        & (_WALRow.sequence_no > after_seq)
                    )
                    .order_by(_WALRow.sequence_no.asc())
                    .limit(limit)
                )
            ).all()
            return [
                WALEntry(
                    thread_id=r.thread_id,
                    sequence_no=r.sequence_no,
                    action_type=WALActionType(r.action_type),
                    payload=loads_json(r.payload_json),
                    tx_id=r.tx_id,
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def max_wal_seq(self, thread_id: str) -> int:
        assert self._engine is not None
        from sqlalchemy import func, select

        async with self._engine.connect() as conn:
            result = await conn.execute(
                select(func.coalesce(func.max(_WALRow.sequence_no), 0)).where(
                    _WALRow.thread_id == thread_id
                )
            )
            return int(result.scalar() or 0)

    # ---------------- Snapshot ----------------

    async def save_snapshot(self, snap: Snapshot) -> None:
        assert self._engine is not None
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_SnapshotRow).values(
                    thread_id=snap.thread_id,
                    snapshot_id=snap.snapshot_id,
                    up_to_seq=snap.up_to_seq,
                    state_json=dumps_json(snap.state),
                    meta_json=dumps_json(snap.metadata) if snap.metadata else None,
                    created_at=snap.created_at,
                )
            )

    async def latest_snapshot(self, thread_id: str) -> Optional[Snapshot]:
        assert self._engine is not None
        from sqlalchemy import select

        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(_SnapshotRow)
                    .where(_SnapshotRow.thread_id == thread_id)
                    .order_by(_SnapshotRow.created_at.desc())
                    .limit(1)
                )
            ).first()
            if not row:
                return None
            return Snapshot(
                thread_id=row.thread_id,
                snapshot_id=row.snapshot_id,
                up_to_seq=row.up_to_seq,
                state=loads_json(row.state_json),
                metadata=loads_json(row.meta_json) if row.meta_json else {},
                created_at=row.created_at,
            )

    # ---------------- Interrupt ----------------

    async def create_interrupt(self, intr: Interrupt) -> None:
        assert self._engine is not None
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_InterruptRow).values(
                    token=intr.token,
                    thread_id=intr.thread_id,
                    checkpoint_id=intr.checkpoint_id,
                    reason=intr.reason,
                    payload_json=dumps_json(intr.payload) if intr.payload else None,
                    status=intr.status.value,
                    response_json=None,
                    created_at=intr.created_at,
                    resolved_at=None,
                )
            )

    async def resolve_interrupt(self, token: str, response: Dict[str, Any]) -> None:
        assert self._engine is not None
        from sqlalchemy import update

        async with self._engine.begin() as conn:
            await conn.execute(
                update(_InterruptRow)
                .where(_InterruptRow.token == token)
                .values(
                    status=InterruptStatus.RESUMED.value,
                    response_json=dumps_json(response) if response else None,
                    resolved_at=datetime.now(),
                )
            )

    async def get_interrupt(self, token: str) -> Optional[Interrupt]:
        assert self._engine is not None
        from sqlalchemy import select

        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(_InterruptRow).where(_InterruptRow.token == token)
                )
            ).first()
            if not row:
                return None
            return Interrupt(
                token=row.token,
                thread_id=row.thread_id,
                checkpoint_id=row.checkpoint_id,
                reason=row.reason,
                payload=loads_json(row.payload_json) if row.payload_json else {},
                status=InterruptStatus(row.status),
                response=loads_json(row.response_json) if row.response_json else None,
                created_at=row.created_at,
                resolved_at=row.resolved_at,
            )

    # ---------------- 锁（M1） ----------------

    async def acquire_lock(
        self, resource_key: str, owner_tx: str, ttl_seconds: float = 30.0
    ) -> Optional[LockRecord]:
        assert self._engine is not None
        from sqlalchemy import delete, func, select
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        now = datetime.now()
        expires_at = None
        if ttl_seconds and ttl_seconds > 0:
            from datetime import timedelta

            expires_at = now + timedelta(seconds=ttl_seconds)

        #fencing_token 单调递增
        # - PostgreSQL：原 nextval() 原子分配
        # - SQLite / 其他：进程内 _fencing_seq 自增（同一进程内单调；跨进程不安全，
        #   生产建议用 PG 或为 fencing_token 字段加 distributed coordination）
        new_fencing_token: int
        async with self._engine.begin() as conn:
            # ① 已存在且未过期：加锁失败
            row = (
                await conn.execute(
                    select(_LockRow).where(_LockRow.resource_key == resource_key)
                )
            ).first()
            prev_version = 0
            prev_fencing = 0
            if row is not None:
                exp = row.expires_at
                if exp is None or exp > now:
                    return None
                # 过期锁：抢占，记录旧 version/fencing（/C-1：单调递增）
                prev_version = row.version
                prev_fencing = row.fencing_token
                await conn.execute(
                    delete(_LockRow).where(_LockRow.resource_key == resource_key)
                )

            #使用 per-resource 单调递增 version（与 memory_backend 对齐）
            new_version = prev_version + 1
            #fencing_token 单调递增
            if self._engine.dialect.name == "postgresql":
                seq_result = await conn.execute(
                    select(func.nextval(_PG_FENCING_SEQ_NAME))
                )
                new_fencing_token = seq_result.scalar() or (prev_fencing + 1)
            elif self._dialect == "sqlite":
                # SQLite：使用数据库表实现多进程安全序列
                # 先尝试原子更新序列值
                result = await conn.execute(
                    sqlite_insert(_SequenceRow)
                    .values(seq_name="fencing_token", seq_value=1)
                    .on_conflict_do_update(
                        index_elements=["seq_name"],
                        set_={"seq_value": _SequenceRow.seq_value + 1}
                    )
                )
                # 回读最新序列值
                seq_row = (
                    await conn.execute(
                        select(_SequenceRow).where(_SequenceRow.seq_name == "fencing_token")
                    )
                ).first()
                new_fencing_token = seq_row.seq_value if seq_row else max(1, prev_fencing + 1)
            else:
                # 其他数据库 fallback 到进程内计数器（警告：不安全）
                import warnings
                warnings.warn(
                    f"fencing_token using in-process counter for {self._dialect}, "
                    "consider using PostgreSQL for production multi-process deployments"
                )
                self._fencing_seq += 1
                new_fencing_token = max(self._fencing_seq, prev_fencing + 1)

            # ② 插入锁（version + fencing_token 单调递增；on conflict 防并发抢占双插）
            if self._dialect == "sqlite":
                stmt: Any = sqlite_insert(_LockRow).values(
                    resource_key=resource_key,
                    version=new_version,
                    fencing_token=new_fencing_token,
                    owner_tx=owner_tx,
                    held_since=now,
                    expires_at=expires_at,
                ).on_conflict_do_nothing(index_elements=["resource_key"])
            else:
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                stmt = pg_insert(_LockRow).values(
                    resource_key=resource_key,
                    version=new_version,
                    fencing_token=new_fencing_token,
                    owner_tx=owner_tx,
                    held_since=now,
                    expires_at=expires_at,
                ).on_conflict_do_nothing(index_elements=["resource_key"])
            result = await conn.execute(stmt)
            # 若冲突（并发抢锁），回读检查 owner
            if result.rowcount == 0:
                got = (
                    await conn.execute(
                        select(_LockRow).where(_LockRow.resource_key == resource_key)
                    )
                ).first()
                if got is not None and got.owner_tx == owner_tx:
                    return LockRecord(
                        resource_key=got.resource_key,
                        version=got.version,
                        fencing_token=got.fencing_token,
                        owner_tx=got.owner_tx,
                        held_since=got.held_since,
                        expires_at=got.expires_at,
                    )
                return None

        return LockRecord(
            resource_key=resource_key,
            version=new_version,
            fencing_token=new_fencing_token,
            owner_tx=owner_tx,
            held_since=now,
            expires_at=expires_at,
        )

    async def compare_and_swap(
        self,
        resource_key: str,
        expected_version: int,
        owner_tx: str,
        expected_fencing_token: Optional[int] = None,
    ) -> bool:
        """ C-1：CAS 可选 fencing_token 校验。"""
        assert self._engine is not None
        from sqlalchemy import update

        async with self._engine.begin() as conn:
            stmt = (
                update(_LockRow)
                .where(
                    (_LockRow.resource_key == resource_key)
                    & (_LockRow.owner_tx == owner_tx)
                    & (_LockRow.version == expected_version)
                )
                .values(version=expected_version + 1)
            )
            if expected_fencing_token is not None:
                stmt = stmt.where(
                    _LockRow.fencing_token == expected_fencing_token
                )
            result = await conn.execute(stmt)
            return result.rowcount > 0

    async def release_lock(self, resource_key: str, owner_tx: str) -> bool:
        assert self._engine is not None
        from sqlalchemy import delete

        async with self._engine.begin() as conn:
            result = await conn.execute(
                delete(_LockRow).where(
                    (_LockRow.resource_key == resource_key)
                    & (_LockRow.owner_tx == owner_tx)
                )
            )
            return result.rowcount > 0

    async def read_version(self, resource_key: str) -> Optional[int]:
        assert self._engine is not None
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(_LockRow).where(_LockRow.resource_key == resource_key)
                )
            ).first()
            return row.version if row else None

    async def read_fencing_token(self, resource_key: str) -> Optional[int]:
        """ C-1：读取 fencing_token。"""
        assert self._engine is not None
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(_LockRow).where(_LockRow.resource_key == resource_key)
                )
            ).first()
            return row.fencing_token if row else None

    # ---------------- 幂等（M1） ----------------

    async def put_idempotency(self, record: IdempotencyRecord) -> None:
        assert self._engine is not None
        from sqlalchemy import delete

        async with self._engine.begin() as conn:
            await conn.execute(
                delete(_IdempotencyRow).where(
                    _IdempotencyRow.idempotency_key == record.idempotency_key
                )
            )
            await conn.execute(
                insert(_IdempotencyRow).values(
                    idempotency_key=record.idempotency_key,
                    request_hash=record.request_hash,
                    tx_id=record.tx_id,
                    status=record.status,
                    result_json=dumps_json(record.result) if record.result else None,
                    created_at=record.created_at,
                    expires_at=record.expires_at,
                )
            )

    async def get_idempotency(self, key: str) -> Optional[IdempotencyRecord]:
        assert self._engine is not None
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(_IdempotencyRow).where(
                        _IdempotencyRow.idempotency_key == key
                    )
                )
            ).first()
            if not row:
                return None
            # 已过期视为不存在
            if row.expires_at is not None and row.expires_at < datetime.now():
                return None
            return IdempotencyRecord(
                idempotency_key=row.idempotency_key,
                request_hash=row.request_hash,
                tx_id=row.tx_id,
                status=row.status,
                result=loads_json(row.result_json) if row.result_json else None,
                created_at=row.created_at,
                expires_at=row.expires_at,
            )

    async def delete_expired_idempotency(self) -> int:
        assert self._engine is not None
        from sqlalchemy import delete

        async with self._engine.begin() as conn:
            result = await conn.execute(
                delete(_IdempotencyRow).where(
                    _IdempotencyRow.expires_at < datetime.now()
                )
            )
            return result.rowcount

    # ---------------- DLQ（M1） ----------------

    async def enqueue_dlq(self, entry: DLQEntry) -> None:
        assert self._engine is not None
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_DLQRow).values(
                    tx_id=entry.tx_id,
                    action_name=entry.action_name,
                    error=entry.error,
                    attempts=entry.attempts,
                    status=entry.status,
                    created_at=entry.created_at,
                    resolved_at=entry.resolved_at,
                )
            )

    async def list_dlq(
        self, limit: int = 100, status: str = "open"
    ) -> List[DLQEntry]:
        assert self._engine is not None
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(_DLQRow)
                    .where(_DLQRow.status == status)
                    .order_by(_DLQRow.created_at.asc())
                    .limit(limit)
                )
            ).all()
            return [
                DLQEntry(
                    id=r.dlq_id,  #映射 dlq_id 字段
                    tx_id=r.tx_id,
                    action_name=r.action_name,
                    error=r.error,
                    attempts=r.attempts,
                    status=r.status,
                    created_at=r.created_at,
                    resolved_at=r.resolved_at,
                )
                for r in rows
            ]

    async def resolve_dlq(self, dlq_id: int, note: Optional[str] = None) -> None:
        """（C-N3）：SQL 后端实现 DLQ resolve（按 dlq_id 定位）。"""
        assert self._engine is not None
        from sqlalchemy import update

        async with self._engine.begin() as conn:
            # 先读取当前 error，用于拼接 note
            row = (
                await conn.execute(
                    select(_DLQRow.error).where(_DLQRow.dlq_id == dlq_id)
                )
            ).first()
            if row is None:
                raise ValueError(f"DLQ entry id={dlq_id} not found")
            new_error = (
                f"{row.error} | resolved_note={note}" if note else row.error
            )
            await conn.execute(
                update(_DLQRow)
                .where(_DLQRow.dlq_id == dlq_id)
                .values(status="resolved", resolved_at=datetime.now(), error=new_error)
            )

    # ---------------- Iteration snapshot ----------------

    async def save_iteration_snapshot(
        self, graph_id: str, thread_id: str, iteration: Dict[str, int]
    ) -> None:
        """（H-7）：upsert 专用表（O(1)）。"""
        assert self._engine is not None
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        payload = dumps_json(iteration)
        async with self._engine.begin() as conn:
            if self._dialect == "sqlite":
                stmt = sqlite_insert(_IterationSnapshotRow).values(
                    graph_id=graph_id,
                    thread_id=thread_id,
                    iteration_json=payload,
                    updated_at=datetime.now(),
                ).on_conflict_do_update(
                    index_elements=["graph_id", "thread_id"],
                    set_={"iteration_json": payload, "updated_at": datetime.now()},
                )
                await conn.execute(stmt)
            else:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                stmt = pg_insert(_IterationSnapshotRow).values(
                    graph_id=graph_id,
                    thread_id=thread_id,
                    iteration_json=payload,
                    updated_at=datetime.now(),
                ).on_conflict_do_update(
                    index_elements=["graph_id", "thread_id"],
                    set_={"iteration_json": payload, "updated_at": datetime.now()},
                )
                await conn.execute(stmt)

    async def load_iteration_snapshot(
        self, graph_id: str, thread_id: str
    ) -> Dict[str, int]:
        """（H-7）：O(1) 按 (graph_id, thread_id) 主键查询。"""
        assert self._engine is not None
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(_IterationSnapshotRow).where(
                        (_IterationSnapshotRow.graph_id == graph_id)
                        & (_IterationSnapshotRow.thread_id == thread_id)
                    )
                )
            ).first()
            if row is None:
                return {}
            try:
                return dict(loads_json(row.iteration_json))
            except (json.JSONDecodeError, TypeError):
                return {}

    # ---------------- Inbox（M2） ----------------

    async def enqueue_message(self, msg: InboxMessage) -> None:
        assert self._engine is not None
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_InboxMessageRow).values(
                    msg_id=msg.msg_id,
                    graph_id=msg.graph_id,
                    thread_id=msg.thread_id,
                    from_node=msg.from_node,
                    to_node=msg.to_node,
                    content_json=dumps_json(msg.content),
                    condition=msg.condition,
                    status=msg.status,
                    attempts=msg.attempts,
                    created_at=msg.created_at,
                    expires_at=msg.expires_at,
                    delivered_at=msg.delivered_at,
                    ack_token=msg.ack_token,
                )
            )

    async def list_pending_messages(
        self, thread_id: str, to_node: Optional[str] = None, limit: int = 100
    ) -> List[InboxMessage]:
        assert self._engine is not None
        async with self._engine.connect() as conn:
            stmt = select(_InboxMessageRow).where(
                (_InboxMessageRow.thread_id == thread_id)
                & (_InboxMessageRow.status == "queued")
            )
            if to_node is not None:
                stmt = stmt.where(_InboxMessageRow.to_node == to_node)
            stmt = stmt.order_by(_InboxMessageRow.created_at.asc()).limit(limit)
            rows = (await conn.execute(stmt)).all()
            return [self._row_to_inbox_msg(r) for r in rows]

    @staticmethod
    def _row_to_inbox_msg(r: Any) -> InboxMessage:
        return InboxMessage(
            msg_id=r.msg_id,
            graph_id=r.graph_id,
            thread_id=r.thread_id,
            from_node=r.from_node,
            to_node=r.to_node,
            content=loads_json(r.content_json),
            condition=r.condition,
            status=r.status,
            attempts=r.attempts,
            created_at=r.created_at,
            expires_at=r.expires_at,
            delivered_at=r.delivered_at,
            ack_token=r.ack_token,
        )

    async def mark_delivered(self, msg_id: str, ack_token: str) -> None:
        assert self._engine is not None
        from sqlalchemy import update

        async with self._engine.begin() as conn:
            await conn.execute(
                update(_InboxMessageRow)
                .where(_InboxMessageRow.msg_id == msg_id)
                .values(
                    status="delivered",
                    delivered_at=datetime.now(),
                    ack_token=ack_token,
                )
            )

    async def mark_failed(self, msg_id: str, error: str, attempts: int) -> None:
        assert self._engine is not None
        from sqlalchemy import update

        async with self._engine.begin() as conn:
            await conn.execute(
                update(_InboxMessageRow)
                .where(_InboxMessageRow.msg_id == msg_id)
                .values(status="failed", attempts=attempts)
            )

    async def ack_message(
        self, msg_id: str, ack_token: Optional[str] = None, status: str = "acked"
    ) -> None:
        assert self._engine is not None
        from sqlalchemy import delete, update

        async with self._engine.begin() as conn:
            # upsert ack
            await conn.execute(
                delete(_InboxAckRow).where(_InboxAckRow.msg_id == msg_id)
            )
            await conn.execute(
                insert(_InboxAckRow).values(
                    msg_id=msg_id,
                    ack_token=ack_token,
                    status=status,
                    acked_at=datetime.now(),
                )
            )
            # 消息同步状态到 acked
            await conn.execute(
                update(_InboxMessageRow)
                .where(_InboxMessageRow.msg_id == msg_id)
                .values(status=status)
            )

    async def delete_expired_messages(self) -> int:
        """ C-4 / H-9：删除过期 inbox 消息 + 级联清理 ack 记录。"""
        assert self._engine is not None
        from sqlalchemy import delete

        async with self._engine.begin() as conn:
            # 先查过期 msg_ids（避免 cascade 子查询的 dialect 差异）
            expired_rows = (
                await conn.execute(
                    select(_InboxMessageRow.msg_id).where(
                        _InboxMessageRow.expires_at < datetime.now()
                    )
                )
            ).all()
            expired_ids = [r.msg_id for r in expired_rows]
            if not expired_ids:
                return 0
            # 删除 ack 记录
            await conn.execute(
                delete(_InboxAckRow).where(_InboxAckRow.msg_id.in_(expired_ids))
            )
            # 删除 message 记录
            result = await conn.execute(
                delete(_InboxMessageRow).where(
                    _InboxMessageRow.msg_id.in_(expired_ids)
                )
            )
            return result.rowcount or 0

    # ---------------- 审计（M3 WORM） ----------------

    async def append_audit(self, entry: AuditEntry) -> None:
        assert self._engine is not None
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_AuditLogRow).values(
                    ts=entry.ts,
                    principal=entry.principal,
                    resource=entry.resource,
                    action=entry.action,
                    obj_id=entry.obj_id,
                    success=1 if entry.success else 0,
                    detail_json=dumps_json(entry.detail) if entry.detail else None,
                    tx_id=entry.tx_id,
                )
            )

    async def query_audit(
        self,
        limit: int = 100,
        principal: Optional[str] = None,
        resource: Optional[str] = None,
    ) -> List[AuditEntry]:
        assert self._engine is not None
        async with self._engine.connect() as conn:
            stmt = select(_AuditLogRow)
            if principal:
                stmt = stmt.where(_AuditLogRow.principal == principal)
            if resource:
                stmt = stmt.where(_AuditLogRow.resource == resource)
            stmt = stmt.order_by(_AuditLogRow.entry_id.desc()).limit(limit)
            rows = (await conn.execute(stmt)).all()
            return [
                AuditEntry(
                    principal=r.principal,
                    resource=r.resource,
                    action=r.action,
                    obj_id=r.obj_id,
                    success=bool(r.success),
                    detail=loads_json(r.detail_json) if r.detail_json else {},
                    tx_id=r.tx_id,
                    ts=r.ts,
                )
                for r in rows
            ]
