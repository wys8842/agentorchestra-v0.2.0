"""agentorchestra.governance - 治理域。

收纳治理相关子包：

- ``govern``   对象身份与权限：Identity / ACL / Permission / CAS
- ``tx``       事务运行时：Coordinator / 幂等 / 补偿 / DLQ / 乐观锁
- ``tenancy``  多租户：Tenant / 配额 / 用量

经典治理公共 API（``from agentorchestra.governance import ACLManager``）经由
``govern`` 子包再导出，保持向后兼容。
"""

from .govern import (  # noqa: F401
    ACLManager,
    ACLRule,
    IdentityContext,
    IdentityService,
    ObjectCAS,
    PermissionChecker,
    PermissionDenied,
    acl,
    cas,
    current_principal,
    current_roles,
    get_identity_service,
    identity,
    permission,
)

__all__ = [
    "IdentityContext",
    "IdentityService",
    "get_identity_service",
    "current_principal",
    "current_roles",
    "ACLRule",
    "ACLManager",
    "PermissionChecker",
    "PermissionDenied",
    "ObjectCAS",
]
