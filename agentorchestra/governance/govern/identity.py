"""identity - principal + roles 上下文（M3）。

principal/roles 经 ContextVar 承载，供事务、ACL 决策、审计自动携带。
roadmap §5.2『Principal 携带：事务上下文（ContextVar）』
"""

from __future__ import annotations

import contextvars
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterator, List, Optional


@dataclass
class IdentityContext:
    """当前身份上下文。"""

    principal: str = "anonymous"
    roles: List[str] = field(default_factory=list)

    def has_role(self, role: str) -> bool:
        """是否拥有指定角色。"""
        return role in self.roles


_current_identity: "contextvars.ContextVar[Optional[IdentityContext]]" = (
    contextvars.ContextVar("agentorchestra_identity", default=None)
)


class IdentityService:
    """身份服务：设置/读取当前 principal + roles（ContextVar）。

    用法：
        svc = IdentityService()
        async with svc.run_as("alice", ["admin"]):
            ...
    """

    def __init__(self, default_principal: str = "anonymous"):
        self.default_principal = default_principal

    # ---------------- 读取 ----------------

    def current(self) -> IdentityContext:
        """读取当前身份（无则默认 anonymous）。"""
        ctx = _current_identity.get()
        if ctx is not None:
            return ctx
        return IdentityContext(principal=self.default_principal)

    @property
    def principal(self) -> str:
        """当前 principal。"""
        return self.current().principal

    @property
    def roles(self) -> List[str]:
        """当前角色列表。"""
        return self.current().roles

    # ---------------- 上下文 ----------------

    @asynccontextmanager
    async def run_as(
        self, principal: str, roles: Optional[List[str]] = None
    ) -> "AsyncIterator[IdentityContext]":
        """async 上下文：进入带身份，退出还原。"""
        token = _current_identity.set(
            IdentityContext(principal=principal, roles=roles or [])
        )
        try:
            yield self.current()
        finally:
            _current_identity.reset(token)

    @contextmanager
    def sync_run_as(
        self, principal: str, roles: Optional[List[str]] = None
    ) -> "Iterator[IdentityContext]":
        """同步上下文版本。"""
        token = _current_identity.set(
            IdentityContext(principal=principal, roles=roles or [])
        )
        try:
            yield self.current()
        finally:
            _current_identity.reset(token)

    # ---------------- 便捷 ----------------

    def set(self, principal: str, roles: Optional[List[str]] = None) -> None:
        """直接设置当前上下文身份（无生命周期管理）。"""
        _current_identity.set(IdentityContext(principal=principal, roles=roles or []))

    def clear(self) -> None:
        """清除当前上下文身份（回退默认 anonymous）。"""
        _current_identity.set(None)


# 全局单例（供 coordinator/审计默认读取）
_global_service: Optional[IdentityService] = None


def get_identity_service() -> IdentityService:
    """获取全局 IdentityService（懒加载）。"""
    global _global_service
    if _global_service is None:
        _global_service = IdentityService()
    return _global_service


def current_principal() -> str:
    """读取当前 principal（无则 anonymous）。"""
    return get_identity_service().principal


def current_roles() -> List[str]:
    """读取当前角色列表（无则空列表）。"""
    return get_identity_service().roles


__all__ = [
    "IdentityContext",
    "IdentityService",
    "get_identity_service",
    "current_principal",
    "current_roles",
]
