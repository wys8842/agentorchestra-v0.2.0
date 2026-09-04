"""coordinator - TransactionCoordinator（M1 事务运行时，async 核心）。

roadmap §3.1：把 TransactionManager.register/execute 从接口壳升级为事务运行时。
最小可用集：幂等 + WAL + 补偿 + DLQ + 乐观锁。SSI/悲观锁留接口。

API（roadmap §3.4）：
    async with coordinator.transaction(idempotency_key=..., timeout=30.0) as tx:
        if not await tx.pre_condition(order_key, expected_version=3):
            raise TxAbort("pre-condition failed")
        await tx.execute("扣库存", {"sku": "A1"})
    # 退出无异常 → commit；异常 → 逆序补偿
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore

from .compensation import CompensationExecutor
from .context import (
    TxAbort,
    TxAction,
    TxConflict,
    TxContext,
    TxReplay,
    TxStatus,
)
from .dlq import DeadLetterQueue
from .idempotency import IdempotencyStore
from .lock import OptimisticLock
from .wal import TxActionLog


class TransactionCoordinator:
    """事务协调器。

    Args:
        store: CheckpointStore。None → 自动建 in-memory（不落 DB，测试/旧调用兼容）。
        compensation_retries: 每个补偿动作最大重试次数（默认 3）。
        compensation_backoff: 补偿重试退避（秒，默认 0.1）。
        idempotency_ttl: 幂等键 TTL 秒（默认 86400 = 24h）。
        lock_ttl: 乐观锁 TTL 秒（默认 30.0）。
        thread_id: WAL 写入所属 thread（默认 "default"）。
    """

    def __init__(
        self,
        store: Any = None,
        compensation_retries: int = 3,
        compensation_backoff: float = 0.1,
        idempotency_ttl: int = 86400,
        lock_ttl: float = 30.0,
        thread_id: str = "default",
        permission_checker: Optional[Any] = None,
    ):
        if store is None:
            store = InMemoryCheckpointStore()
            # in-memory 无需 init；SQL store 需要调用方先 init
        self.store = store
        self.compensation_retries = compensation_retries
        self.compensation_backoff = compensation_backoff
        self.lock_ttl = lock_ttl
        self.thread_id = thread_id
        self.permission_checker = permission_checker  # M3：PermissionChecker

        self._actions: Dict[str, TxAction] = {}

        self.idempotency = IdempotencyStore(store, ttl_seconds=idempotency_ttl)
        self.lock = OptimisticLock(store, ttl_seconds=lock_ttl)
        self.dlq = DeadLetterQueue(store)
        self.tx_log = TxActionLog(store, thread_id=thread_id)
        self.compensation = CompensationExecutor(
            self, max_attempts=compensation_retries, backoff=compensation_backoff
        )

    # ---------------- 动作注册 ----------------

    def register(self, action: TxAction) -> None:
        """注册一个 TxAction。"""
        self._actions[action.name] = action

    def register_action(
        self,
        name: str,
        execute_fn: Any,
        compensate_fn: Optional[Any] = None,
        idempotent: bool = True,
    ) -> TxAction:
        """便捷注册：构造 TxAction 并注册。"""
        action = TxAction(
            name=name,
            execute_fn=execute_fn,
            compensate_fn=compensate_fn,
            idempotent=idempotent,
        )
        self._actions[name] = action
        return action

    def get_action(self, name: str) -> Optional[TxAction]:
        """按名取已注册动作，不存在返回 None。"""
        return self._actions.get(name)

    def list_actions(self) -> List[str]:
        """列出已注册动作名。"""
        return list(self._actions.keys())

    # ---------------- WAL 辅助（供 TxContext 调用） ----------------

    async def _append_action_wal(
        self, tx_id: str, action_name: str,
        params: Dict[str, Any], result: Any,
    ) -> None:
        await self.tx_log.log_action(tx_id, action_name, params, result)

    # ---------------- 事务主流程 ----------------

    @asynccontextmanager
    async def transaction(
        self,
        idempotency_key: Optional[str] = None,
        resources: Optional[List[str]] = None,
        timeout: float = 30.0,
        principal: Optional[str] = None,
        roles: Optional[List[str]] = None,
        permission_checker: Optional[Any] = None,
    ) -> AsyncIterator[TxContext]:
        """事务上下文管理器。

        幂等键自动生成：sha256(actions 签名)。
        进入即 begin（幂等查重 + 获取资源锁 + TX_BEGIN）。
        退出无异常 → commit；异常 → 逆序补偿 + DLQ。

        M3：principal/roles 注入当前身份（IdentityService + ctx）；
        permission_checker 可选（未提供回退 coordinator 装配的）。
        """
        resources = resources or []
        tx_id = f"tx-{uuid.uuid4().hex[:12]}"

        # 幂等 key：未显式传 → 自动生成（基于已注册动作签名）
        key = idempotency_key or IdempotencyStore.generate_key(
            "tx", sorted(self._actions.keys())
        )
        request_hash = IdempotencyStore.generate_key(key, resources)

        # 幂等查重
        existing = await self.idempotency.get(key)
        if existing is not None and existing.status == "completed":
            raise TxReplay(result=existing.result)

        # begin 登记
        proceed = await self.idempotency.begin(key, request_hash, tx_id)
        if not proceed:
            # 刚被并发完成 → 重放
            again = await self.idempotency.get(key)
            raise TxReplay(result=again.result if again else None)

        principal = principal or "anonymous"
        roles = roles or []
        ctx = TxContext(
            tx_id=tx_id, coordinator=self,
            principal=principal, roles=roles,
            permission_checker=permission_checker or self.permission_checker,
        )

        # 获取资源锁（乐观锁）
        acquired: List[str] = []
        identity_token = None
        try:
            # M3：注入身份到 ContextVar（IdentityService），供审计/ACL 读取
            from agentorchestra.governance.govern.identity import (  # type: ignore[attr-defined]
                IdentityContext,
                _current_identity,
            )

            identity_token = _current_identity.set(
                IdentityContext(principal=principal, roles=roles)
            )

            for rk in resources:
                if await self.lock.acquire(rk, tx_id):
                    acquired.append(rk)
                else:
                    # 锁冲突：清理已获取锁，抛 TxConflict
                    await self.lock.release_all(tx_id)
                    await self.idempotency.mark_failed(key, tx_id)
                    raise TxConflict(
                        f"资源锁冲突: {rk}（可能被其他事务持有）"
                    )
            ctx.resources = list(acquired)

            # TX_BEGIN
            if not isinstance(self.store, InMemoryCheckpointStore):
                await self.tx_log.log_begin(tx_id, {"idempotency_key": key})

            async with asyncio.timeout(timeout):
                yield ctx

            # ---- 正常退出：commit ----
            await self._commit(ctx, key)

        except asyncio.TimeoutError:
            await self._compensate_and_fail(ctx, key, "timeout")
            raise
        except TxAbort:
            await self._compensate_and_fail(ctx, key, "abort")
            raise
        except TxConflict:
            # 已在上方处理释放；直接上抛
            raise
        except Exception as e:
            # 动作执行失败 / 用户代码异常 → 补偿
            await self._compensate_and_fail(ctx, key, str(e))
            raise
        finally:
            if identity_token is not None:
                from agentorchestra.governance.govern.identity import (
                    _current_identity,  # type: ignore[attr-defined]
                )

                _current_identity.reset(identity_token)

    async def _commit(self, ctx: TxContext, key: str) -> None:
        """提交：幂等 completed + TX_COMMIT + 释放锁。"""
        ctx.status = TxStatus.COMMITTED
        await self.idempotency.complete(key, ctx.result, ctx.tx_id)
        if not isinstance(self.store, InMemoryCheckpointStore):
            await self.tx_log.log_commit(ctx.tx_id, TxStatus.COMMITTED.value)
        await self.lock.release_all(ctx.tx_id)
        self._emit_tx_metric(ctx, result="committed")

    async def _compensate_and_fail(
        self, ctx: TxContext, key: str, reason: str
    ) -> None:
        """逆序补偿已完成动作。补偿失败（含进 DLQ）→ compensation_failed。"""
        comp_result = await self.compensation.compensate(
            ctx.tx_id, ctx.completed, ctx.completed_params
        )

        if comp_result["failed"]:
            ctx.status = TxStatus.COMPENSATION_FAILED
        else:
            ctx.status = TxStatus.ABORTED

        if not isinstance(self.store, InMemoryCheckpointStore):
            await self.tx_log.log_commit(ctx.tx_id, ctx.status.value)
        await self.idempotency.mark_failed(key, ctx.tx_id)
        await self.lock.release_all(ctx.tx_id)

        # M5：SLO 指标（回滚/补偿触发）
        self._emit_tx_metric(ctx, result="aborted", reason=reason)
        for failed in comp_result["failed"]:
            self._emit_compensation_metric(failed["action"])

    # ---------------- M5 可观测性（SLO 指标） ----------------

    def _emit_tx_metric(
        self, ctx: TxContext, result: str, reason: str = ""
    ) -> None:
        """发事务 SLO 指标（NoOp 默认零影响）。"""
        import time

        from agentorchestra.observability.metrics import (
            SLO_TX_DURATION_SECONDS,
            get_default_collector,
        )

        try:
            col = get_default_collector()
            elapsed = time.monotonic() - ctx.started
            col.observe(SLO_TX_DURATION_SECONDS, max(elapsed, 0.0),
                        {"result": result})
            if result != "committed":
                col.increment("tx_rollback_total", 1, {"reason": reason or "abort"})
        except Exception:
            pass

    def _emit_compensation_metric(self, action_name: str) -> None:
        """发补偿触发指标。"""
        from agentorchestra.observability.metrics import (
            SLO_TX_COMPENSATION_TRIGGERED,
            get_default_collector,
        )

        try:
            col = get_default_collector()
            col.increment(SLO_TX_COMPENSATION_TRIGGERED, 1,
                          {"action": action_name})
        except Exception:
            pass
