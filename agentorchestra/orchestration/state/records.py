"""records - 锁 / 幂等键 / DLQ / Inbox 记录类型（M1 事务引擎 / M2 图通信用）。

放在 state 包内，使 CheckpointStore 抽象能引用它们而不引入对 tx/ 或 orchestration/ 的反向依赖。

"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class LockRecord:
    """乐观锁记录（locks 表）。

    Attributes:
        resource_key: 被锁资源键（如 "order:12345"，已含租户 namespace 前缀）
        version: 当前版本号（per-resource 单调递增，CAS 基准）
        fencing_token: 单调递增令牌（：防止僵尸事务绕过 TTL 后误写）
        owner_tx: 持有锁的事务 id
        held_since: 获取时间
        expires_at: 过期时间（TTL 释放）
    """

    resource_key: str
    version: int
    owner_tx: str
    fencing_token: int = 0  #单调递增令牌（每次 acquire + 1）
    held_since: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None


@dataclass
class IdempotencyRecord:
    """幂等键记录（idempotency_keys 表）。

    Attributes:
        idempotency_key: 幂等键（必填；未显式传时自动生成）
        request_hash: 请求签名哈希
        tx_id: 关联事务 id
        status: running | completed | failed
        result: 首次执行的返回结果（completed 后重放返回它）
        created_at: 创建时间
        expires_at: TTL 过期时间（默认 24h）
    """

    idempotency_key: str
    request_hash: str
    tx_id: Optional[str] = None
    status: str = "running"
    result: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None


@dataclass
class DLQEntry:
    """死信条目（dead_letter 表）。

    Attributes:
        id: 全局唯一 id（：用于 resolve_dlq(id) 定位条目；内存后端自增，SQL 后端 DB autoincrement）
        tx_id: 关联事务 id
        action_name: 补偿失败的动作名
        error: 最后一次错误信息
        attempts: 已尝试补偿次数
        status: open | resolved
        created_at: 入队时间
        resolved_at: 人工解决时间
    """

    tx_id: str
    action_name: str
    error: Optional[str] = None
    attempts: int = 0
    status: str = "open"
    created_at: datetime = field(default_factory=datetime.now)
    resolved_at: Optional[datetime] = None
    id: Optional[int] = None  #DLQEntry 增加 id 字段供 resolve_dlq 定位


@dataclass
class InboxMessage:
    """Inbox 消息（inbox_messages 表，M2 图通信）。

    Attributes:
        msg_id: 全局唯一消息 id
        graph_id: 所属图实例
        thread_id: 所属 thread
        from_node: 源节点（None = 图入口）
        to_node: 目标节点
        content: 消息内容（任意 JSON 可序列化 dict）
        condition: 条件边标签（None = 无条件，总是投递）
        status: queued | delivered | failed | expired | acked
        attempts: 投递尝试次数
        created_at: 入队时间
        expires_at: TTL 过期时间（默认 +7 天）
        delivered_at: 最后投递时间
        ack_token: 投递回执 token
    """

    msg_id: str
    graph_id: str
    thread_id: str
    to_node: str
    content: Dict[str, Any]
    from_node: Optional[str] = None
    condition: Optional[str] = None
    status: str = "queued"
    attempts: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    ack_token: Optional[str] = None

    @property
    def expired(self) -> bool:
        """是否已超过过期时间。"""
        return self.expires_at is not None and self.expires_at < datetime.now()


@dataclass
class InboxAck:
    """投递回执（inbox_acks 表，M2 图通信）。

    Attributes:
        msg_id: 关联消息 id
        ack_token: 回执 token
        status: acked | rejected
        acked_at: 回执时间
    """

    msg_id: str
    ack_token: Optional[str] = None
    status: str = "acked"
    acked_at: datetime = field(default_factory=datetime.now)


@dataclass
class AuditEntry:
    """审计条目（audit_log 表，M3 WORM）。

    WORM 语义：只允许 append + query，接口层不提供 update/delete。

    Attributes:
        principal: 操作者
        resource: 资源（对象类型名 或 "order:o1"）
        action: 动作（read/write/delete/execute...）
        obj_id: 对象 id（行级，可选）
        success: 是否成功
        detail: 附加详情（JSON 可序列化）
        tx_id: 关联事务 id（可选）
        ts: 时间戳
    """

    principal: str
    resource: str
    action: str
    obj_id: Optional[str] = None
    success: bool = True
    detail: Dict[str, Any] = field(default_factory=dict)
    tx_id: Optional[str] = None
    ts: datetime = field(default_factory=datetime.now)
