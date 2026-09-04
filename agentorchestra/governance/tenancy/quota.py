"""quota - TokenQuota / QuotaManager / QuotaExceeded（M6 多租户）。

roadmap §8.2『配额维度：token LLM / 单价 / 并发 Agent 数 / 存储』——本期 token 配额最小集。
配额耗尽抛 QuotaExceeded（优雅失败，不崩进程）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict


class QuotaExceeded(Exception):
    """租户配额耗尽。调用方应优雅处理（降级/提示）。"""

    def __init__(self, tenant_id: str, limit: int, used: int, attempted: int = 0):
        super().__init__(
            f"配额耗尽: tenant={tenant_id} 已用 {used}/{limit} tokens"
        )
        self.tenant_id = tenant_id
        self.limit = limit
        self.used = used
        self.attempted = attempted


@dataclass
class TokenQuota:
    """单个租户的 token 配额。

    Attributes:
        tenant_id: 租户
        limit: 总 token 上限（-1 = 不限）
        used: 已用 tokens
    """

    tenant_id: str
    limit: int = -1
    used: int = 0

    @property
    def unlimited(self) -> bool:
        """是否不限量（limit < 0）。"""
        return self.limit < 0

    def can_charge(self, tokens: int) -> bool:
        """本次扣减后是否会超限。"""
        if self.unlimited:
            return True
        return (self.used + tokens) <= self.limit

    def remaining(self) -> int:
        """剩余可用 tokens（不限返回 -1）。"""
        return -1 if self.unlimited else max(0, self.limit - self.used)


class QuotaManager:
    """配额管理器（单实例内存计数，线程安全）。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._quotas: Dict[str, TokenQuota] = {}

    def set_limit(self, tenant_id: str, limit: int) -> None:
        """设置/更新租户 token 上限（-1 = 不限）。"""
        with self._lock:
            q = self._quotas.get(tenant_id)
            if q:
                q.limit = limit
            else:
                self._quotas[tenant_id] = TokenQuota(tenant_id=tenant_id, limit=limit)

    def get(self, tenant_id: str) -> TokenQuota:
        """获取配额（不存在则创建：不限）。"""
        with self._lock:
            q = self._quotas.get(tenant_id)
            if q is None:
                q = TokenQuota(tenant_id=tenant_id)
                self._quotas[tenant_id] = q
            return q

    def charge(self, tenant_id: str, tokens: int) -> None:
        """扣减配额。超限抛 QuotaExceeded（不崩进程，优雅）。

        注意：扣减是尝试性的——先检查能否容纳，能则累加。
        """
        if tokens <= 0:
            return
        with self._lock:
            q = self.get(tenant_id)
            if not q.can_charge(tokens):
                raise QuotaExceeded(tenant_id, q.limit, q.used, tokens)
            q.used += tokens

    def reset(self, tenant_id: str) -> None:
        """重置租户用量。"""
        with self._lock:
            q = self._quotas.get(tenant_id)
            if q:
                q.used = 0

    def snapshot(self) -> Dict[str, dict]:
        """所有租户用量快照（供计费/观测）。"""
        with self._lock:
            return {
                tid: {"used": q.used, "limit": q.limit,
                       "remaining": q.remaining()}
                for tid, q in self._quotas.items()
            }


__all__ = ["QuotaExceeded", "TokenQuota", "QuotaManager"]
