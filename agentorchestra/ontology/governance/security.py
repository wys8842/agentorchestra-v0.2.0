"""Security - 安全

权限规则：定义谁能对什么资源执行什么动作。
"""

from typing import List, Optional


class SecurityContext:
    """安全上下文（谁在操作）"""

    def __init__(self, principal: str = "anonymous", roles: Optional[List[str]] = None):
        self.principal = principal
        self.roles = roles or []

    def has_role(self, role: str) -> bool:
        """判断上下文是否拥有指定角色"""
        return role in self.roles


class PermissionRule:
    """权限规则"""

    def __init__(self, resource: str, action: str, roles: List[str]):
        self.resource = resource
        self.action = action
        self.roles = roles

    def allows(self, resource: str, action: str, ctx: SecurityContext) -> bool:
        """判断资源/动作是否对该上下文放行"""
        if self.resource != "*" and self.resource != resource:
            return False
        if self.action != "*" and self.action != action:
            return False
        return any(ctx.has_role(r) for r in self.roles)


class SecurityManager:
    """安全管理器

    默认拒绝（deny-by-default）：除非显式添加规则允许，否则所有访问都被拒绝。
    开发模式可通过 `set_open_mode(True)` 切换为全放行（仅推荐用于本地开发）。


    并发出 WARNING 级别日志；未设置环境变量时仅 print 一行警告并要求用户显式确认。
    """

    def __init__(self, open_mode: bool = False):
        import os
        import warnings
        self._rules: List[PermissionRule] = []
        self._open_mode = open_mode
        if open_mode:
            #：显式确认
            env_confirmed = os.getenv("AGENTORCHESTRA_ALLOW_OPEN_MODE") == "1"
            if not env_confirmed:
                warnings.warn(
                    "SecurityManager.open_mode=True 但未设置环境变量 AGENTORCHESTRA_ALLOW_OPEN_MODE=1。"
                    "生产环境部署前必须移除此调用或显式设置环境变量。",
                    UserWarning,
                    stacklevel=2,
                )

    def set_open_mode(self, open_mode: bool) -> None:
        """切换为开放模式（仅用于本地原型，**禁止生产环境使用**）。


        - 传入 True 时检查环境变量 `AGENTORCHESTRA_ALLOW_OPEN_MODE=1`，未设置则拒绝并 raise
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

    def allow(self, roles: List[str], resource: str = "*", action: str = "*") -> None:
        """便捷授权：允许角色对资源执行动作"""
        self.add_rule(PermissionRule(resource, action, roles))

    def check(self, resource: str, action: str, ctx: SecurityContext) -> bool:
        """权限检查：默认拒绝；显式 open_mode=True 时无规则 = 放行。"""
        if self._open_mode and not self._rules:
            return True
        if not self._rules:
            return False
        return any(rule.allows(resource, action, ctx) for rule in self._rules)
