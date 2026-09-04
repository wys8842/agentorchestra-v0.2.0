"""acl - 对象级 ACL（M3）。

RBAC 负责"角色能做什么资源/动作"，ACL 负责"谁能操作具体对象行"。
roadmap §5.2『RBAC + 对象 ACL（行级）』
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    pass


@dataclass
class ACLRule:
    """对象级规则。

    resource 支持通配：精确 "order:o1" 或前缀 "order:*"。
    principal 与 role 至少一个非空：
        - principal 指定 → 仅该用户匹配
        - role 指定 → 拥有该角色的用户匹配
        - 都指定 → 任一路径匹配即可
    """

    resource: str
    permission: str
    principal: Optional[str] = None
    role: Optional[str] = None


class ACLManager:
    """对象级 ACL 管理器（内存；可扩展持久化 backend）。

    用法：
        acl = ACLManager()
        acl.grant("order:o1", "write", principal="alice")
        acl.grant("order:*", "read", role="finance")
        acl.check("order:o1", "write", principal="alice", roles=[])  # True
    """

    def __init__(self):
        self._rules: List[ACLRule] = []

    # ---------------- 管理 ----------------

    def grant(
        self,
        resource: str,
        permission: str,
        principal: Optional[str] = None,
        role: Optional[str] = None,
    ) -> ACLRule:
        """授予对象级权限。"""
        rule = ACLRule(resource=resource, permission=permission,
                       principal=principal, role=role)
        self._rules.append(rule)
        return rule

    def revoke(
        self,
        resource: str,
        permission: str,
        principal: Optional[str] = None,
        role: Optional[str] = None,
    ) -> int:
        """撤销匹配规则，返回撤销条数。"""
        before = len(self._rules)
        self._rules = [
            r for r in self._rules
            if not (
                r.resource == resource
                and r.permission == permission
                and r.principal == principal
                and r.role == role
            )
        ]
        return before - len(self._rules)

    def clear(self) -> None:
        """清空全部规则。"""
        self._rules.clear()

    def list_rules(self) -> List[ACLRule]:
        """返回全部规则副本。"""
        return list(self._rules)

    # ---------------- 决策 ----------------

    def check(
        self,
        resource: str,
        permission: str,
        principal: Optional[str] = None,
        roles: Optional[List[str]] = None,
    ) -> bool:
        """检查指定用户/角色是否有对象级权限。

        无任何规则 → False（ACL 是白名单；与 RBAC"无规则开放"不同）。
        """
        if not self._rules:
            return False

        roles = roles or []
        principal = principal or ""

        # 顺序无关：任何匹配规则即放行。资源匹配：精确 或 resource:* 通配。
        for rule in self._rules:
            if rule.permission != "*" and rule.permission != permission:
                continue
            if not self._resource_match(rule.resource, resource):
                continue
            # principal/role 匹配（至少一个维度）
            if rule.principal is not None and rule.principal == principal:
                return True
            if rule.role is not None and rule.role in roles:
                return True
        return False

    @staticmethod
    def _resource_match(pattern: str, actual: str) -> bool:
        if pattern == actual:
            return True
        if pattern.endswith(":*"):
            return actual.startswith(pattern[:-2] + ":") or actual == pattern[:-2]
        # 支持 fnmatch 通配（如 order:*）
        return fnmatch.fnmatch(actual, pattern)


__all__ = ["ACLRule", "ACLManager"]
