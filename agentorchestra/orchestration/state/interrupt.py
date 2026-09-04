"""Interrupt - HITL（Human-in-the-Loop）中断与恢复。

Agent 主动发起中断 → 业务侧 resume(token, response) → 注入 response 继续执行。

"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class InterruptStatus(str, Enum):
    """中断状态。"""

    PENDING = "pending"
    RESUMED = "resumed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class Interrupt:
    """一个中断请求。

    Attributes:
        token: UUIDv4，全局唯一
        thread_id: 所属 thread
        checkpoint_id: 中断时所在 checkpoint
        reason: 中断原因（如"需要审批"）
        payload: 任意 JSON（业务侧用于决策）
        status: 当前状态
        response: resume 时的响应
        created_at: 创建时间
        resolved_at: 解决时间
    """

    token: str
    thread_id: str
    checkpoint_id: str
    reason: str
    payload: Dict[str, Any] = field(default_factory=dict)
    status: InterruptStatus = InterruptStatus.PENDING
    response: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=datetime.now)
    resolved_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（状态/时间为可读字符串）。"""
        return {
            "token": self.token,
            "thread_id": self.thread_id,
            "checkpoint_id": self.checkpoint_id,
            "reason": self.reason,
            "payload": self.payload,
            "status": self.status.value,
            "response": self.response,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


class InterruptPending(Exception):
    """Agent 因等待外部输入而暂停。

    业务侧捕获后调用 `agent.resume_with(token, response)` 恢复。
    """

    def __init__(self, token: str, reason: str, payload: Dict[str, Any]):
        super().__init__(f"中断待处理 token={token} reason={reason}")
        self.token = token
        self.reason = reason
        self.payload = payload
