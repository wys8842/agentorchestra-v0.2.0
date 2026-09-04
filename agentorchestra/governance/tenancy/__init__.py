"""agentorchestra.tenancy - 多租户隔离（M6 / P6）。

路线图 §8。设计见 docs/superpowers/specs/2026-09-04-m6-multitenancy-design.md

公共 API：
- TenantManager / TenantContext
- QuotaManager / TokenQuota / QuotaExceeded
- UsageRecorder / UsageRecord
"""

from .billing import UsageRecord, UsageRecorder
from .quota import QuotaExceeded, QuotaManager, TokenQuota
from .tenant import TenantContext, TenantManager

__all__ = [
    "TenantManager",
    "TenantContext",
    "QuotaManager",
    "TokenQuota",
    "QuotaExceeded",
    "UsageRecorder",
    "UsageRecord",
]
