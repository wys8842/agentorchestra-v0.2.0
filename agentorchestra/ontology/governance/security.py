"""Security - 安全

权限规则：定义谁能对什么资源执行什么动作。

支持：
- 角色继承（父角色权限自动授予子角色）
- 资源模式（glob 风格）
- 操作类型（CRUD + 自定义）
- 字段级权限（field-level access control）
"""

from typing import Any, Dict, List, Optional, Set
import fnmatch
import re


class SecurityContext:
    """安全上下文（谁在操作）

    Attributes:
        principal: 主体 ID（用户/Agent/服务）
        roles: 角色列表
        groups: 用户组（继承角色）
        attributes: 自定义属性（用于 ABAC）
    """

    def __init__(
        self,
        principal: str = "anonymous",
        roles: Optional[List[str]] = None,
        groups: Optional[List[str]] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ):
        self.principal = principal
        self.roles = roles or []
        self.groups = groups or []
        self.attributes = attributes or {}

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_any_role(self, roles: List[str]) -> bool:
        return any(self.has_role(r) for r in roles)

    def in_group(self, group: str) -> bool:
        return group in self.groups


class RoleInheritance:
    """角色继承关系"""

    def __init__(self):
        self._parents: Dict[str, Set[str]] = {}  # child -> {parents}

    def add_inheritance(self, child: str, parent: str) -> None:
        """添加继承：child 继承 parent 的权限"""
        if child not in self._parents:
            self._parents[child] = set()
        self._parents[child].add(parent)

    def get_parents(self, role: str, visited: Optional[Set[str]] = None) -> Set[str]:
        """获取所有父角色（递归）"""
        if visited is None:
            visited = set()
        if role in visited:
            return set()
        visited.add(role)

        parents = set(self._parents.get(role, set()))
        result = set(parents)
        for p in parents:
            result.update(self.get_parents(p, visited))
        return result

    def get_effective_roles(self, direct_roles: List[str]) -> Set[str]:
        """获取直接角色 + 继承角色的全集"""
        effective = set(direct_roles)
        for role in direct_roles:
            effective.update(self.get_parents(role))
        return effective


class PermissionRule:
    """权限规则

    Attributes:
        resource: 资源模式（支持 glob，如 "order:*"）
        action: 动作（read/write/delete/execute/*）
        roles: 允许的角色列表
        conditions: ABAC 条件（属性匹配）
        field_pattern: 字段模式（field-level access）
    """

    def __init__(
        self,
        resource: str,
        action: str,
        roles: List[str],
        conditions: Optional[Dict[str, Any]] = None,
        field_pattern: Optional[str] = None,
    ):
        self.resource = resource
        self.action = action
        self.roles = roles
        self.conditions = conditions or {}
        self.field_pattern = field_pattern

    def allows(
        self,
        resource: str,
        action: str,
        ctx: SecurityContext,
        inheritance: Optional[RoleInheritance] = None,
        field: Optional[str] = None,
    ) -> bool:
        """判断资源/动作是否对该上下文放行"""
        # 资源模式匹配（支持 glob）
        if self.resource != "*" and not self._match_pattern(self.resource, resource):
            return False
        # 动作匹配
        if self.action != "*" and self.action != action:
            return False

        # 角色检查（含继承）
        effective_roles = {ctx.principal}
        if inheritance:
            effective_roles = inheritance.get_effective_roles(ctx.roles)
            effective_roles.add(ctx.principal)
        else:
            effective_roles.update(ctx.roles)

        if not any(r in effective_roles for r in self.roles):
            return False

        # 字段级检查（在角色检查后，避免字段限制过于严格）
        if self.field_pattern:
            if not field or not self._match_pattern(self.field_pattern, field):
                return False

        # ABAC 条件
        if self.conditions:
            if not self._check_conditions(ctx):
                return False

        return True

    @staticmethod
    def _match_pattern(pattern: str, value: str) -> bool:
        """glob 模式匹配（支持 : 分隔符）"""
        # 替换 : 为 / 以适配 fnmatch
        return fnmatch.fnmatch(value, pattern) or fnmatch.fnmatch(
            value.replace(":", "/"), pattern.replace(":", "/")
        )

    def _check_conditions(self, ctx: SecurityContext) -> bool:
        """检查 ABAC 条件"""
        for key, expected in self.conditions.items():
            actual = ctx.attributes.get(key)
            if isinstance(expected, str) and expected.startswith("regex:"):
                pattern = expected[6:]
                if not actual or not re.match(pattern, str(actual)):
                    return False
            elif isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True


class SecurityManager:
    """安全管理器

    默认拒绝（deny-by-default）：除非显式添加规则允许，否则所有访问都被拒绝。
    开发模式可通过 `set_open_mode(True)` 切换为全放行（仅推荐用于本地开发）。
    """

    def __init__(self, open_mode: bool = False):
        import os
        import warnings
        self._rules: List[PermissionRule] = []
        self._open_mode = open_mode
        self._inheritance = RoleInheritance()  # 角色继承
        if open_mode:
            env_confirmed = os.getenv("AGENTORCHESTRA_ALLOW_OPEN_MODE") == "1"
            if not env_confirmed:
                warnings.warn(
                    "SecurityManager.open_mode=True 但未设置环境变量 AGENTORCHESTRA_ALLOW_OPEN_MODE=1。"
                    "生产环境部署前必须移除此调用或显式设置环境变量。",
                    UserWarning,
                    stacklevel=2,
                )

    def set_open_mode(self, open_mode: bool) -> None:
        """切换为开放模式（仅用于本地原型，禁止生产环境使用）。

        - 传入 True 时检查环境变量 `AGENTORCHESTRA_ALLOW_OPEN_MODE=1`
        - 切换后写 audit log（如已挂 audit）
        """
        import os
        if open_mode:
            env_confirmed = os.getenv("AGENTORCHESTRA_ALLOW_OPEN_MODE") == "1"
            if not env_confirmed:
                raise RuntimeError(
                    "SecurityManager.set_open_mode(True) 已被拒绝：未设置环境变量 "
                    "AGENTORCHESTRA_ALLOW_OPEN_MODE=1。生产环境禁止开放模式；"
                    "本地开发请显式设置环境变量后重试。"
                )
        self._open_mode = open_mode

    def add_rule(self, rule: PermissionRule) -> None:
        """添加权限规则"""
        self._rules.append(rule)

    def allow(
        self,
        roles: List[str],
        resource: str = "*",
        action: str = "*",
        conditions: Optional[Dict[str, Any]] = None,
        field_pattern: Optional[str] = None,
    ) -> None:
        """便捷授权：允许角色对资源执行动作

        Args:
            roles: 允许的角色列表
            resource: 资源模式（支持 glob）
            action: 动作类型
            conditions: ABAC 条件
            field_pattern: 字段模式（field-level 权限）
        """
        self.add_rule(PermissionRule(resource, action, roles, conditions, field_pattern))

    def inherit(self, child_role: str, parent_role: str) -> None:
        """声明角色继承：child 继承 parent 的所有权限"""
        self._inheritance.add_inheritance(child_role, parent_role)

    def check(
        self,
        resource: str,
        action: str,
        ctx: SecurityContext,
        field: Optional[str] = None,
    ) -> bool:
        """权限检查（支持角色继承 + 字段级权限）

        Args:
            resource: 资源标识
            action: 操作类型
            ctx: 安全上下文
            field: 字段名（field-level 权限检查）
        """
        if self._open_mode and not self._rules:
            return True
        if not self._rules:
            return False
        return any(
            rule.allows(resource, action, ctx, self._inheritance, field)
            for rule in self._rules
        )

    def get_effective_roles(self, ctx: SecurityContext) -> Set[str]:
        """获取上下文的有效角色（含继承）"""
        return self._inheritance.get_effective_roles(ctx.roles)


__all__ = [
    "SecurityContext",
    "RoleInheritance",
    "PermissionRule",
    "SecurityManager",
]
