"""agentorchestra.governance - 对象身份与权限（M3 / P3）。

路线图 §5。设计见 docs/superpowers/specs/2026-09-04-m3-object-identity-acl-design.md

公共 API：
- IdentityService / IdentityContext / current_principal / current_roles
- ACLManager / ACLRule
- PermissionChecker / PermissionDenied
- ObjectCAS
"""

from .acl import ACLManager, ACLRule
from .cas import ObjectCAS
from .identity import (
    IdentityContext,
    IdentityService,
    current_principal,
    current_roles,
    get_identity_service,
)
from .permission import PermissionChecker, PermissionDenied

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
