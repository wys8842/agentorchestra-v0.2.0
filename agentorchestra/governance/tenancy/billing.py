"""billing - UsageRecorder（M6 多租户用量记录与导出）。

roadmap §8.5『用量统计可导出 CSV/JSON 给计费系统』。
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class UsageRecord:
    """单条用量记录。"""

    tenant_id: str
    model: str
    tokens: int
    latency_ms: float = 0.0
    ts: str = field(default_factory=lambda: datetime.now().isoformat())


class UsageRecorder:
    """用量记录器（内存 + 可选导出）。"""

    def __init__(self, max_records: int = 100_000):
        self._records: List[UsageRecord] = []
        self._max_records = max_records

    def record(
        self,
        tenant_id: str,
        model: str,
        tokens: int,
        latency_ms: float = 0.0,
        ts: str = "",
    ) -> None:
        """记录一次用量。超 max_records 丢弃最旧（滚动）。"""
        rec = UsageRecord(
            tenant_id=tenant_id, model=model, tokens=tokens,
            latency_ms=latency_ms, ts=ts or datetime.now().isoformat(),
        )
        self._records.append(rec)
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records:]

    # ---------------- 查询 ----------------

    def total(self, tenant_id: Optional[str] = None) -> int:
        """租户（或全部）累计 tokens。"""
        if tenant_id is None:
            return sum(r.tokens for r in self._records)
        return sum(r.tokens for r in self._records if r.tenant_id == tenant_id)

    def by_tenant(self) -> Dict[str, int]:
        """每个租户累计 tokens。"""
        agg: Dict[str, int] = {}
        for r in self._records:
            agg[r.tenant_id] = agg.get(r.tenant_id, 0) + r.tokens
        return agg

    def snapshot(self) -> List[Dict[str, Any]]:
        """返回全部记录副本（供导出/观测）。"""
        return [asdict(r) for r in self._records]

    # ---------------- 导出 ----------------

    def export_csv(self, path: str) -> None:
        """导出 CSV。"""
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["ts", "tenant_id", "model", "tokens", "latency_ms"])
            writer.writeheader()
            for r in self._records:
                writer.writerow(asdict(r))

    def export_json(self, path: str) -> None:
        """导出 JSON（列表）。"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.snapshot(), f, ensure_ascii=False, indent=2)


__all__ = ["UsageRecord", "UsageRecorder"]
