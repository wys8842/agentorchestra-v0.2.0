"""lock - OptimisticLock（M1 事务运行时）。

基于 CheckpointStore.locks 表的 CAS 乐观锁。业务对象无需自带 version。
设计见 docs/superpowers/specs/2026-09-03-m1-transaction-runtime-design.md §4.2

：acquire 返回 `Optional[LockRecord]` 而非 `bool`；
调用方可拿到 fencing_token 用于下游写操作的 token 校验。

：acquire / read_version / compare_and_swap 自动感知当前租户上下文，
对 resource_key 拼接 namespace 前缀；无租户上下文时保持原样（向后兼容）。

：`opt_out_namespace_scope()` 块内跳过拼接（运维跨租户场景）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from agentorchestra.governance.tenancy.tenant import namespace_resource
from agentorchestra.orchestration.state.records import LockRecord

if TYPE_CHECKING:
    from agentorchestra.orchestration.state.checkpoint import CheckpointStore


class OptimisticLock:
    """乐观锁（基于 locks 表；自动感知租户 namespace）。


    """

    def __init__(self, store: "CheckpointStore", ttl_seconds: float = 30.0):
        self.store = store
        self.ttl_seconds = ttl_seconds
        self._held: Dict[str, LockRecord] = {}

    async def acquire(self, resource_key: str, owner_tx: str) -> Optional[LockRecord]:
        """获取锁（自动 namespace 隔离）。

        Returns:
            `LockRecord`（成功）或 `None`（失败）。调用方可读取
            `LockRecord.fencing_token` 用于下游 CAS 验证。
        """
        namespaced_key = namespace_resource(resource_key)
        if namespaced_key in self._held:
            return self._held[namespaced_key]  # 已持有
        record = await self.store.acquire_lock(
            namespaced_key, owner_tx, ttl_seconds=self.ttl_seconds
        )
        if record is None:
            return None
        self._held[namespaced_key] = record
        return record

    async def read_version(self, resource_key: str) -> Optional[int]:
        """读版本（CAS 基准；自动 namespace 隔离）。"""
        return await self.store.read_version(namespace_resource(resource_key))

    async def read_fencing_token(self, resource_key: str) -> Optional[int]:
        """读 fencing_token（防僵尸事务；自动 namespace 隔离）。"""
        return await self.store.read_fencing_token(namespace_resource(resource_key))

    async def compare_and_swap(
        self,
        resource_key: str,
        expected_version: int,
        owner_tx: str,
        expected_fencing_token: Optional[int] = None,
    ) -> bool:
        """CAS：版本匹配则 +1（自动 namespace 隔离）。


        """
        ok = await self.store.compare_and_swap(
            namespace_resource(resource_key),
            expected_version,
            owner_tx,
            expected_fencing_token=expected_fencing_token,
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