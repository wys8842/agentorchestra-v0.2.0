"""Checkpoint - durable checkpoint 抽象与存储接口。

Phase 2 重构：拆分为细粒度接口（见 interfaces.py），
原 CheckpointStore 保持向后兼容，实现 FullCheckpointStore。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .interfaces import (  # noqa: F401  # 导出细粒度接口
    AuditStore,
    CheckpointStore,
    DLQStore,
    FullCheckpointStore,
    IdempotencyStore,
    InboxStore,
    InterruptStore,
    IterationSnapshotStore,
    LockStore,
    SnapshotStore,
    ThreadStore,
    WALStore,
)

if TYPE_CHECKING:
    from .interrupt import Interrupt
    from .records import (
        AuditEntry,
        DLQEntry,
        IdempotencyRecord,
        InboxMessage,
        LockRecord,
    )
    from .snapshot import Snapshot
    from .wal import WALEntry


@dataclass
class Checkpoint:
    """一个 checkpoint。

    Attributes:
        thread_id: 所属 thread
        checkpoint_id: 全局唯一 id（默认用 uuid4）
        parent_id: 父 checkpoint id（链式）
        state: 序列化状态（通常是 {"history": [...], "step": int}）
        metadata: 任意元数据（如 token_count / tools_used）
        created_at: 创建时间
    """

    thread_id: str
    checkpoint_id: str
    state: Dict[str, Any]
    parent_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（created_at 用 ISO8601 字符串）。"""
        return {
            "thread_id": self.thread_id,
            "checkpoint_id": self.checkpoint_id,
            "parent_id": self.parent_id,
            "state": self.state,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Checkpoint":
        """从字典反序列化（兼容 ISO8601 字符串或 datetime）。"""
        return cls(
            thread_id=d["thread_id"],
            checkpoint_id=d["checkpoint_id"],
            state=d["state"],
            parent_id=d.get("parent_id"),
            metadata=d.get("metadata", {}),
            created_at=datetime.fromisoformat(d["created_at"])
            if isinstance(d.get("created_at"), str)
            else d.get("created_at", datetime.now()),
        )


class CheckpointStore(ABC):
    """Checkpoint 存储抽象。

    所有方法都是 async（基于 SQLAlchemy 2.0 async）。InMemory 实现保持 async 签名
    以保证调用方代码统一。
    """

    @abstractmethod
    async def init(self) -> None:
        """初始化存储（创建表/索引）。"""

    @abstractmethod
    async def close(self) -> None:
        """关闭连接。"""

    # ---------------- Thread ----------------

    @abstractmethod
    async def create_thread(
        self, thread_id: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """创建/初始化一个 thread。已存在则忽略。"""

    @abstractmethod
    async def get_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        """获取 thread 记录；不存在返回 None。"""

    @abstractmethod
    async def list_threads(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出所有 thread（snapshot worker 自动发现用）；可选按 status 过滤。"""

    @abstractmethod
    async def update_thread_status(self, thread_id: str, status: str) -> None:
        """更新 thread 状态。"""

    # ---------------- Checkpoint ----------------

    @abstractmethod
    async def save_checkpoint(self, cp: Checkpoint) -> None:
        """保存 checkpoint（已存在则覆盖）。"""

    @abstractmethod
    async def load_checkpoint(
        self, thread_id: str, checkpoint_id: str
    ) -> Optional[Checkpoint]:
        """加载指定 checkpoint。"""

    @abstractmethod
    async def list_checkpoints(
        self, thread_id: str, limit: int = 50
    ) -> List[Checkpoint]:
        """按时间倒序列出 checkpoints。"""

    @abstractmethod
    async def latest_checkpoint(self, thread_id: str) -> Optional[Checkpoint]:
        """获取 thread 最新 checkpoint。"""

    # ---------------- WAL ----------------

    @abstractmethod
    async def append_wal(self, entry: "WALEntry") -> int:
        """追加 WAL 条目，返回 sequence_no。"""

    @abstractmethod
    async def read_wal(
        self, thread_id: str, after_seq: int = 0, limit: int = 1000
    ) -> List["WALEntry"]:
        """从指定 sequence_no 之后读取 WAL 条目。"""

    @abstractmethod
    async def max_wal_seq(self, thread_id: str) -> int:
        """获取 thread 当前最大 sequence_no；无 WAL 则返回 0。"""

    # ---------------- Snapshot ----------------

    @abstractmethod
    async def save_snapshot(self, snap: "Snapshot") -> None:
        """保存快照。"""

    @abstractmethod
    async def latest_snapshot(self, thread_id: str) -> Optional["Snapshot"]:
        """获取最新快照。"""

    # ---------------- Interrupt ----------------

    @abstractmethod
    async def create_interrupt(self, intr: "Interrupt") -> None:
        """创建中断。"""

    @abstractmethod
    async def resolve_interrupt(self, token: str, response: Dict[str, Any]) -> None:
        """标记中断已解决（resume 触发）。"""

    @abstractmethod
    async def get_interrupt(self, token: str) -> Optional["Interrupt"]:
        """获取中断。"""

    @abstractmethod
    async def list_interrupts(
        self, status: Optional[str] = None, thread_id: Optional[str] = None
    ) -> List["Interrupt"]:
        """列出中断（InterruptResumer 用），可选按 status / thread_id 过滤。"""

    # ---------------- 锁（M1 事务引擎） ----------------

    @abstractmethod
    async def acquire_lock(
        self, resource_key: str, owner_tx: str, ttl_seconds: float = 30.0
    ) -> Optional[LockRecord]:
        """尝试获取锁。成功返回 LockRecord；已存在（未过期）返回 None。"""

    @abstractmethod
    async def compare_and_swap(
        self,
        resource_key: str,
        expected_version: int,
        owner_tx: str,
        expected_fencing_token: Optional[int] = None,
    ) -> bool:
        """CAS：版本等于 expected_version 才更新为 +1。

        可选 fencing_token 校验；提供时需匹配（防 epoch 错位）。
        """

    @abstractmethod
    async def release_lock(self, resource_key: str, owner_tx: str) -> bool:
        """释放锁（仅 owner 可释放）。"""

    @abstractmethod
    async def read_version(self, resource_key: str) -> Optional[int]:
        """读取资源当前版本；无锁记录返回 None。"""

    @abstractmethod
    async def read_fencing_token(self, resource_key: str) -> Optional[int]:
        """读取资源 fencing_token；无锁记录返回 None。"""

    # ---------------- 幂等（M1 事务引擎） ----------------

    @abstractmethod
    async def put_idempotency(self, record: IdempotencyRecord) -> None:
        """写入/更新幂等记录（同 key 覆盖）。"""

    @abstractmethod
    async def get_idempotency(self, key: str) -> Optional[IdempotencyRecord]:
        """读取幂等记录；不存在或已过期返回 None。"""

    @abstractmethod
    async def delete_expired_idempotency(self) -> int:
        """清理过期幂等记录，返回删除条数。"""

    # ---------------- DLQ（M1 事务引擎） ----------------

    @abstractmethod
    async def enqueue_dlq(self, entry: DLQEntry) -> None:
        """入死信队列。"""

    @abstractmethod
    async def list_dlq(
        self, limit: int = 100, status: str = "open"
    ) -> List[DLQEntry]:
        """列出死信条目（默认 open）。"""

    @abstractmethod
    async def resolve_dlq(self, dlq_id: int, note: Optional[str] = None) -> None:
        """（C14）：将 DLQ 条目标记为已解决（人工介入完成）。"""

    # ---------------- Iteration snapshot（C-N9） ----------------

    @abstractmethod
    async def save_iteration_snapshot(
        self, graph_id: str, thread_id: str, iteration: Dict[str, int]
    ) -> None:
        """保存图 iteration snapshot（崩溃恢复用）。

        专用 API 替代 WAL STATE_UPDATE 扫描。
        """

    @abstractmethod
    async def load_iteration_snapshot(
        self, graph_id: str, thread_id: str
    ) -> Dict[str, int]:
        """加载图最近 iteration snapshot；不存在返回 {}。

        O(1) 查询（按 graph_id 索引）。
        """

    # ---------------- Inbox（M2 图通信） ----------------

    @abstractmethod
    async def enqueue_message(self, msg: InboxMessage) -> None:
        """入队一条消息（status=queued）。同 msg_id 覆盖。"""

    @abstractmethod
    async def list_pending_messages(
        self, thread_id: str, to_node: Optional[str] = None, limit: int = 100
    ) -> List[InboxMessage]:
        """列出指定 thread（可选 to_node）的 queued 消息。"""

    @abstractmethod
    async def mark_delivered(self, msg_id: str, ack_token: str) -> None:
        """标记已投递（status=delivered + attempts+1 + delivered_at + ack_token）。"""

    @abstractmethod
    async def mark_failed(self, msg_id: str, error: str, attempts: int) -> None:
        """标记投递失败（status=failed）。"""

    @abstractmethod
    async def ack_message(
        self, msg_id: str, ack_token: Optional[str] = None, status: str = "acked"
    ) -> None:
        """写回执到 inbox_acks 表（acked/rejected）。"""

    @abstractmethod
    async def delete_expired_messages(self) -> int:
        """清理过期消息（expires_at < now），返回删除条数。"""

    # ---------------- 审计（M3 WORM） ----------------

    @abstractmethod
    async def append_audit(self, entry: AuditEntry) -> None:
        """追加审计条目（append-only；不提供 update/delete）。"""

    @abstractmethod
    async def query_audit(
        self,
        limit: int = 100,
        principal: Optional[str] = None,
        resource: Optional[str] = None,
    ) -> List[AuditEntry]:
        """查询审计条目（按时间倒序）。"""

    # ---------------- 便捷 ----------------

    async def count_checkpoints(self, thread_id: str) -> int:
        """统计 thread 的 checkpoint 数量（默认实现可被子类覆盖优化）。"""
        return len(await self.list_checkpoints(thread_id, limit=10_000_000))


def json_default(obj: Any) -> Any:
    """JSON 序列化兜底（datetime/Decimal/Set 等）。"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"无法序列化 {type(obj).__name__}")


def dumps_json(obj: Any) -> str:
    """序列化任意对象为 JSON 字符串（含 datetime 兜底）。"""
    return json.dumps(obj, ensure_ascii=False, default=json_default)


def loads_json(s: str) -> Any:
    """反序列化 JSON 字符串；空串返回 None。"""
    if not s:
        return None
    return json.loads(s)
