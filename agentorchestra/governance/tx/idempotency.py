"""idempotency - IdempotencyStore（M1 事务运行时）。

幂等键哈希去重 + TTL（默认 24h）。同 key 二次提交直接返回首次结果。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, Optional

from agentorchestra.orchestration.state.records import IdempotencyRecord

if TYPE_CHECKING:
    from agentorchestra.orchestration.state.checkpoint import CheckpointStore


class IdempotencyStore:
    """幂等存储（基于 CheckpointStore.idempotency_keys 表）。"""

    def __init__(self, store: "CheckpointStore", ttl_seconds: int = 86400):
        self.store = store
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def generate_key(*parts: Any) -> str:
        """自动生成幂等键：sha256(部分签名)。"""
        h = hashlib.sha256()
        for p in parts:
            h.update(json.dumps(p, ensure_ascii=False, sort_keys=True,
                                default=str).encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()

    @staticmethod
    def request_hash(key: str, steps: Any) -> str:
        """请求签名（key + steps 列表）用于重放检测。"""
        return IdempotencyStore.generate_key(key, steps)

    async def begin(self, key: str, request_hash: str, tx_id: str) -> bool:
        """登记幂等记录（running）。若已 completed 返回 False（命中重放）。"""
        existing = await self.store.get_idempotency(key)
        if existing is not None and existing.status == "completed":
            return False

        record = IdempotencyRecord(
            idempotency_key=key,
            request_hash=request_hash,
            tx_id=tx_id,
            status="running",
            expires_at=datetime.now() + timedelta(seconds=self.ttl_seconds),
        )
        await self.store.put_idempotency(record)
        return True

    async def complete(
        self, key: str, result: Dict[str, Any], tx_id: str
    ) -> None:
        """标记完成并保存首次结果。"""
        record = IdempotencyRecord(
            idempotency_key=key,
            request_hash=IdempotencyStore.generate_key(key),
            tx_id=tx_id,
            status="completed",
            result=result,
            expires_at=datetime.now() + timedelta(seconds=self.ttl_seconds),
        )
        await self.store.put_idempotency(record)

    async def mark_failed(self, key: str, tx_id: str) -> None:
        """事务失败标记（允许后续重试，不视为 completed）。"""
        record = IdempotencyRecord(
            idempotency_key=key,
            request_hash=IdempotencyStore.generate_key(key),
            tx_id=tx_id,
            status="failed",
            expires_at=datetime.now() + timedelta(seconds=self.ttl_seconds),
        )
        await self.store.put_idempotency(record)

    async def get(self, key: str) -> Optional[IdempotencyRecord]:
        """读取幂等记录（含已完成的首次结果）。"""
        return await self.store.get_idempotency(key)

    async def cleanup(self) -> int:
        """清理过期记录。"""
        return await self.store.delete_expired_idempotency()
