"""lock - OptimisticLock（M1 事务运行时）。

基于 CheckpointStore.locks 表的 CAS 乐观锁。业务对象无需自带 version。
设计见 docs/superpowers/specs/2026-09-03-m1-transaction-runtime-design.md §4.2
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from agentorchestra.orchestration.state.records import LockRecord

if TYPE_CHECKING:
    from agentorchestra.orchestration.state.checkpoint import CheckpointStore


class OptimisticLock:
    """乐观锁（基于 locks 表）。"""

    def __init__(self, store: "CheckpointStore", ttl_seconds: float = 30.0):
        self.store = store
        self.ttl_seconds = ttl_seconds
        self._held: Dict[str, LockRecord] = {}

    async def acquire(self, resource_key: str, owner_tx: str) -> bool:
        """获取锁。成功记录到本地 held，供 commit 时释放。"""
        if resource_key in self._held:
            return True  # 已持有
        record = await self.store.acquire_lock(
            resource_key, owner_tx, ttl_seconds=self.ttl_seconds
        )
        if record is None:
            return False
        self._held[resource_key] = record
        return True

    async def read_version(self, resource_key: str) -> Optional[int]:
        """读版本（CAS 基准）。"""
        return await self.store.read_version(resource_key)

    async def compare_and_swap(
        self, resource_key: str, expected_version: int, owner_tx: str
    ) -> bool:
        """CAS：版本匹配则 +1。"""
        ok = await self.store.compare_and_swap(
            resource_key, expected_version, owner_tx
        )
        return ok

    async def release_all(self, owner_tx: str) -> List[str]:
        """释放全部持有的锁，返回释放列表。"""
        released = []
        for key in list(self._held.keys()):
            if await self.store.release_lock(key, owner_tx):
                released.append(key)
        self._held.clear()
        return released

    def clear(self) -> None:
        """清空本地 held（不触达后端；测试/异常清理用）。"""
        self._held.clear()
