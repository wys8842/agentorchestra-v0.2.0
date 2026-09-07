"""细粒度存储接口 - Phase 2 重构。

按职责拆分为独立接口：
- ThreadStore: 线程管理
- CheckpointStore: 检查点 CRUD
- WALStore: Write-Ahead Log
- SnapshotStore: 快照
- InterruptStore: 中断管理
- LockStore: 分布式锁
- IdempotencyStore: 幂等记录
- DLQStore: 死信队列
- InboxStore: 消息 inbox
- AuditStore: 审计日志
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..records import (
        AuditEntry,
        DLQEntry,
        IdempotencyRecord,
        InboxMessage,
        LockRecord,
    )
    from ..snapshot import Snapshot
    from ..wal import WALEntry
    from .checkpoint import Checkpoint
    from .interrupt import Interrupt


class ThreadStore(ABC):
    """线程管理接口"""

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


class CheckpointStore(ABC):
    """检查点 CRUD 接口"""

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

    async def count_checkpoints(self, thread_id: str) -> int:
        """统计 thread 的 checkpoint 数量（默认实现可被子类覆盖优化）。"""
        return len(await self.list_checkpoints(thread_id, limit=10_000_000))


class WALStore(ABC):
    """Write-Ahead Log 接口"""

    @abstractmethod
    async def append_wal(self, entry: WALEntry) -> int:
        """追加 WAL 条目，返回 sequence_no。"""

    @abstractmethod
    async def read_wal(
        self, thread_id: str, after_seq: int = 0, limit: int = 1000
    ) -> List[WALEntry]:
        """从指定 sequence_no 之后读取 WAL 条目。"""

    @abstractmethod
    async def max_wal_seq(self, thread_id: str) -> int:
        """获取 thread 当前最大 sequence_no；无 WAL 则返回 0。"""


class SnapshotStore(ABC):
    """快照接口"""

    @abstractmethod
    async def save_snapshot(self, snap: Snapshot) -> None:
        """保存快照。"""

    @abstractmethod
    async def latest_snapshot(self, thread_id: str) -> Optional[Snapshot]:
        """获取最新快照。"""


class InterruptStore(ABC):
    """中断管理接口"""

    @abstractmethod
    async def create_interrupt(self, intr: Interrupt) -> None:
        """创建中断。"""

    @abstractmethod
    async def resolve_interrupt(self, token: str, response: Dict[str, Any]) -> None:
        """标记中断已解决（resume 触发）。"""

    @abstractmethod
    async def get_interrupt(self, token: str) -> Optional[Interrupt]:
        """获取中断。"""

    @abstractmethod
    async def list_interrupts(
        self, status: Optional[str] = None, thread_id: Optional[str] = None
    ) -> List[Interrupt]:
        """列出中断（InterruptResumer 用），可选按 status / thread_id 过滤。"""


class LockStore(ABC):
    """分布式锁接口"""

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


class IdempotencyStore(ABC):
    """幂等记录接口"""

    @abstractmethod
    async def put_idempotency(self, record: IdempotencyRecord) -> None:
        """写入/更新幂等记录（同 key 覆盖）。"""

    @abstractmethod
    async def get_idempotency(self, key: str) -> Optional[IdempotencyRecord]:
        """读取幂等记录；不存在或已过期返回 None。"""

    @abstractmethod
    async def delete_expired_idempotency(self) -> int:
        """清理过期幂等记录，返回删除条数。"""


class DLQStore(ABC):
    """死信队列接口"""

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


class InboxStore(ABC):
    """消息 inbox 接口"""

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


class AuditStore(ABC):
    """审计日志接口"""

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


class IterationSnapshotStore(ABC):
    """迭代快照接口"""

    @abstractmethod
    async def save_iteration_snapshot(
        self, graph_id: str, thread_id: str, iteration: Dict[str, int]
    ) -> None:
        """保存图 iteration snapshot（崩溃恢复用）。


        """

    @abstractmethod
    async def load_iteration_snapshot(
        self, graph_id: str, thread_id: str
    ) -> Dict[str, int]:
        """加载图最近 iteration snapshot；不存在返回 {}。


        """


# 组合接口：完整存储（继承所有细粒度接口）
class FullCheckpointStore(
    ThreadStore,
    CheckpointStore,
    WALStore,
    SnapshotStore,
    InterruptStore,
    LockStore,
    IdempotencyStore,
    DLQStore,
    InboxStore,
    AuditStore,
    IterationSnapshotStore,
    ABC,
):
    """完整存储接口 - 继承所有细粒度接口"""

    pass
