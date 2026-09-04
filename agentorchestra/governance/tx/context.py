"""context - TxAction / TxContext / 异常 / 状态（M1 事务运行时）。

设计见 docs/superpowers/specs/2026-09-03-m1-transaction-runtime-design.md §5.2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from .coordinator import TransactionCoordinator


class TxStatus(str, Enum):
    """事务状态。"""

    RUNNING = "running"
    COMMITTED = "committed"
    ABORTED = "aborted"
    COMPENSATION_FAILED = "compensation_failed"


class TxAbort(Exception):
    """用户主动中断事务（pre-condition 失败 / 规则拒绝）。

    触发逆序补偿，事务状态为 aborted。
    """


class TxConflict(Exception):
    """乐观锁 CAS 冲突。

    调用方决定重试（不自动无限重试）。
    """


class TxReplay(Exception):
    """幂等命中：同 idempotency_key 已成功执行过。

    result 携带首次返回结果。
    """

    def __init__(self, result: Optional[Dict[str, Any]] = None):
        super().__init__("幂等命中：事务已执行过")
        self.result = result or {}


@dataclass
class TxAction:
    """一个可执行/可补偿的动作。

    Attributes:
        name: 动作名（事务内唯一）
        execute_fn: 执行函数 fn(params, tx_ctx) -> result
        compensate_fn: 补偿函数 fn(params, tx_ctx)；None 表示不可补偿
        idempotent: 是否重放安全（记录动作级幂等标记）
    """

    name: str
    execute_fn: Callable[..., Any]
    compensate_fn: Optional[Callable[..., Any]] = None
    idempotent: bool = True


@dataclass
class TxContext:
    """事务上下文（用户代码在 async with coordinator.transaction() 中操作）。"""

    tx_id: str
    coordinator: "TransactionCoordinator" = field(repr=False)
    status: TxStatus = TxStatus.RUNNING
    completed: List[str] = field(default_factory=list)  # 已成功执行的动作名（正序）
    completed_params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    resources: List[str] = field(default_factory=list)
    result: Dict[str, Any] = field(default_factory=dict)
    failure: Optional[Exception] = None
    # M3：事务身份与权限
    principal: str = "anonymous"
    roles: List[str] = field(default_factory=list)
    permission_checker: Optional[Any] = None  # PermissionChecker
    # M5：事务开始单调时钟（指标耗时）
    started: float = field(default_factory=lambda: __import__("time").monotonic())

    def authorize(
        self,
        resource: str,
        permission: str,
        obj_id: Optional[str] = None,
    ) -> None:
        """权限检查（RBAC + ACL）。拒绝 → 抛 PermissionDenied。

        未装配 permission_checker → 放行（最小可用：权限可选）。
        """
        if self.permission_checker is None:
            return
        self.permission_checker.check(
            resource, permission,
            principal=self.principal, roles=self.roles, obj_id=obj_id,
            raise_on_deny=True,
        )

    async def pre_condition(
        self,
        resource_key: str,
        expected_version: Optional[int] = None,
        owner_tx: Optional[str] = None,
    ) -> bool:
        """事务前条件检查。

        若 expected_version 给定 → 检查当前版本是否匹配（CAS 冲突返回 False，不抛异常）。
        若仅声明资源 → 尝试获取锁，失败返回 False。
        """
        store = self.coordinator.store
        owner = owner_tx or self.tx_id

        if expected_version is not None:
            current = await store.read_version(resource_key)
            if current is None or current != expected_version:
                return False
            # 版本匹配 → 用 CAS 提升（占位锁需先持有）
            if resource_key not in self.resources:
                got = await store.acquire_lock(resource_key, owner,
                                               self.coordinator.lock_ttl)
                if got is None:
                    return False
                self.resources.append(resource_key)
            # CAS 从 expected_version → +1
            return await store.compare_and_swap(resource_key, expected_version, owner)

        # 无 version 要求：仅获取锁（如果尚未持有）
        if resource_key not in self.resources:
            got = await store.acquire_lock(resource_key, owner,
                                           self.coordinator.lock_ttl)
            if got is None:
                return False
            self.resources.append(resource_key)
        return True

    async def execute(self, action_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """执行一个已注册动作。

        成功 → 记录到 completed 列表（供逆序补偿），写 WAL STATE_UPDATE。
        失败 → 抛异常（由 coordinator 捕获触发补偿）。
        """
        if self.status != TxStatus.RUNNING:
            raise RuntimeError(f"事务已不在 running 态（{self.status.value}）")

        action = self.coordinator.get_action(action_name)
        if action is None:
            raise ValueError(f"动作未注册: {action_name}")

        try:
            result = action.execute_fn(params, self)
        except Exception as e:
            self.failure = e
            raise

        # 记录已完成（正序），供逆序补偿
        self.completed.append(action_name)
        self.completed_params[action_name] = dict(params)

        # WAL: STATE_UPDATE with tx_id
        if self.coordinator.store is not None:
            await self.coordinator._append_action_wal(self.tx_id, action_name,
                                                      params, result)

        # 记录整体事务结果（最后一个 execute 的结果为事务结果）
        self.result = {"action": action_name, "result": result}
        return result


__all__ = [
    "TxStatus",
    "TxAbort",
    "TxConflict",
    "TxReplay",
    "TxAction",
    "TxContext",
]
