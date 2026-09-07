"""tenant - TenantContext / TenantManager（M6 多租户）。

roadmap §8.2『tenant_id（粗粒度）+ namespace（细粒度）』。
租户上下文经 ContextVar 承载；与 IdentityService（governance）可同时激活，互不覆盖。

： enforce() 装饰器与 namespace_resource() 辅助函数，
便于业务代码显式校验/拼接 tenant namespace，避免跨租户数据泄漏。
"""

from __future__ import annotations

import contextvars
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Iterator, Optional


@dataclass(frozen=True)
class TenantContext:
    """当前租户上下文。

    Attributes:
        tenant_id: 租户标识（粗粒度顶层边界）
        user_id: 用户标识（细粒度；可选）
    """

    tenant_id: str
    user_id: str = ""

    @property
    def namespace(self) -> str:
        """隔离命名空间：tenant_id 或 tenant_id:user_id。"""
        return f"{self.tenant_id}:{self.user_id}" if self.user_id else self.tenant_id


_current_tenant: "contextvars.ContextVar[Optional[TenantContext]]" = (
    contextvars.ContextVar("agentorchestra_tenant", default=None)
)


class TenantIsolationError(Exception):
    """跨租户访问被拒绝时抛出。"""

    def __init__(self, current_tenant: Optional[str], target_resource: str):
        self.current_tenant = current_tenant
        self.target_resource = target_resource
        super().__init__(
            f"跨租户访问被拒绝：current_tenant={current_tenant!r}, target={target_resource!r}"
        )


#：跨租户逃生口（仅限运维/调试使用，需显式 import 并加注释）
_opt_out_token: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "_opt_out_namespace", default=False
)


@contextmanager
def opt_out_namespace_scope() -> Iterator[None]:
    """跨租户访问逃生口（仅限运维/调试；生产代码禁止使用）。


    用于管理员跨租户接管、跨租户数据迁移等场景。

    用法：
        from agentorchestra.governance.tenancy.tenant import opt_out_namespace_scope

        # ⚠️ 仅运维场景
        with opt_out_namespace_scope():
            lock.acquire("global:admin_resource", "admin_tx")
    """
    token = _opt_out_token.set(True)
    try:
        yield
    finally:
        _opt_out_token.reset(token)


def namespace_resource(resource_key: str) -> str:
    """在 resource_key 前拼接当前租户 namespace（强制隔离）。



    示例：
        namespace_resource("orders") → "acme:orders"
        无租户上下文时直接返回原 resource_key（向后兼容）
    """
    if _opt_out_token.get():
        return resource_key
    ctx = _current_tenant.get()
    if ctx is None:
        return resource_key
    return f"{ctx.namespace}:{resource_key}"


def get_current_or_default(default_tenant: str = "default") -> str:
    """读取当前租户；无则返回 default（**仅用于非敏感场景**）。

    对于跨租户数据敏感的代码，应使用 namespace_resource() 显式拼接。
    """
    ctx = _current_tenant.get()
    return ctx.tenant_id if ctx else default_tenant


def enforce_tenant_access(resource_namespace: str) -> None:
    """强制要求当前租户 namespace 与资源 namespace 前缀一致；不一致则抛 TenantIsolationError。

    用法（业务层）：
        from agentorchestra.governance.tenancy.tenant import enforce_tenant_access
        enforce_tenant_access(obj["namespace"])  # obj["namespace"] 必须以 "acme:" 开头
    """
    ctx = _current_tenant.get()
    if ctx is None:
        return  # 无租户上下文：放行（向后兼容）
    if not resource_namespace.startswith(f"{ctx.namespace}:"):
        raise TenantIsolationError(ctx.namespace, resource_namespace)


class TenantManager:
    """租户上下文管理（ContextVar）。

    用法：
        tm = TenantManager()
        async with tm.run_as("acme", "alice"):
            TenantManager.current().namespace  # "acme:alice"
    """

    @staticmethod
    def current() -> Optional[TenantContext]:
        """读取当前租户；无则 None。"""
        return _current_tenant.get()

    @staticmethod
    def tenant_id() -> Optional[str]:
        """当前租户 id；无则 None。"""
        ctx = _current_tenant.get()
        return ctx.tenant_id if ctx else None

    @staticmethod
    def namespace() -> str:
        """当前隔离 namespace（无租户 → "default"）。"""
        ctx = _current_tenant.get()
        return ctx.namespace if ctx else "default"

    @asynccontextmanager
    async def run_as(
        self, tenant_id: str, user_id: str = ""
    ) -> AsyncIterator[TenantContext]:
        """async 上下文。"""
        token = _current_tenant.set(TenantContext(tenant_id=tenant_id, user_id=user_id))
        try:
            yield TenantContext(tenant_id=tenant_id, user_id=user_id)
        finally:
            _current_tenant.reset(token)

    @contextmanager
    def sync_run_as(
        self, tenant_id: str, user_id: str = ""
    ) -> Iterator[TenantContext]:
        """同步上下文版本。"""
        token = _current_tenant.set(TenantContext(tenant_id=tenant_id, user_id=user_id))
        try:
            yield TenantContext(tenant_id=tenant_id, user_id=user_id)
        finally:
            _current_tenant.reset(token)


__all__ = [
    "TenantContext",
    "TenantManager",
    "TenantIsolationError",
    "namespace_resource",
    "get_current_or_default",
    "enforce_tenant_access",
    "opt_out_namespace_scope",
]
