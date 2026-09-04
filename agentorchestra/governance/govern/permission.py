"""permission - PermissionDenied / PermissionChecker（M3）。

两段式决策：RBAC（SecurityManager，角色→资源/动作）→ ACL（对象行级）。
roadmap §5.2『权限决策在事务 pre-condition 阶段生效』
"""

from __future__ import annotations

from typing import Any, List, Optional


class PermissionDenied(Exception):
    """权限不足。

    在事务 pre-condition 抛出 → coordinator 捕获 → 自动回滚。
    """

    def __init__(self, resource: str, permission: str, principal: str):
        super().__init__(
            f"权限不足: {principal} 无权执行 {permission} on {resource}"
        )
        self.resource = resource
        self.permission = permission
        self.principal = principal


class PermissionChecker:
    """权限决策器。

    兼容：security=None（无 RBAC）时跳过 RBAC 段（视为允许）；acl=None（无 ACL）
    且 obj_id 为空时放行。有 obj_id 且配置了 acl → 必须通过 ACL。
    """

    def __init__(self, security: Any = None, acl: Any = None,
                 default_roles: Optional[List[str]] = None):
        self.security = security
        self.acl = acl
        self.default_roles = default_roles or []

    def check(
        self,
        resource: str,
        permission: str,
        principal: Optional[str] = None,
        roles: Optional[List[str]] = None,
        obj_id: Optional[str] = None,
        raise_on_deny: bool = True,
    ) -> bool:
        """执行权限决策。

        Returns:
            bool（raise_on_deny=False 时返回；否则拒绝抛 PermissionDenied）
        """
        principal = principal or "anonymous"
        roles = roles if roles is not None else self.default_roles

        # ① RBAC（无规则默认开放：SecurityManager.check 返回 True）
        if self.security is not None:
            from agentorchestra.ontology.governance import SecurityContext

            ctx = SecurityContext(principal=principal, roles=roles)
            if not self.security.check(resource, permission, ctx):
                return self._deny_or_raise(
                    resource, permission, principal, raise_on_deny
                )

        # ② 对象级 ACL（仅当有 obj_id 时要求）
        if obj_id is not None:
            if self.acl is not None:
                # ACL 未命中 → 拒绝（白名单）
                if not self.acl.check(
                    f"{resource}:{obj_id}", permission,
                    principal=principal, roles=roles,
                ):
                    # 额外尝试通配 resource 本身（如 ACL 配在对象类型而非行）
                    if not self.acl.check(
                        resource, permission, principal=principal, roles=roles
                    ):
                        return self._deny_or_raise(
                            f"{resource}:{obj_id}", permission,
                            principal, raise_on_deny,
                        )
            # acl 为 None 且要求行级 → 若 RBAC 已允许则放行（无 ACL 视为开放行）
            # （最小可用：ACL 可选组件；未装配 = 不强制行级）

        return True

    def _deny_or_raise(
        self, resource: str, permission: str, principal: str, raise_on_deny: bool
    ) -> bool:
        if raise_on_deny:
            raise PermissionDenied(resource, permission, principal)
        return False


__all__ = ["PermissionDenied", "PermissionChecker"]
