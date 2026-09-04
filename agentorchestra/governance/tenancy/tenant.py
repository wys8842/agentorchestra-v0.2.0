"""tenant - TenantContext / TenantManager（M6 多租户）。

roadmap §8.2『tenant_id（粗粒度）+ namespace（细粒度）』。
租户上下文经 ContextVar 承载；与 IdentityService（governance）可同时激活，互不覆盖。
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


__all__ = ["TenantContext", "TenantManager"]
