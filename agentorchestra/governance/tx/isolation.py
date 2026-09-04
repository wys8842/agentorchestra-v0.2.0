"""isolation - IsolationSnapshot 抽象（可扩展点）。

M1 最小可用集不实现 SSI；此处仅保留接口与注释，供 M4 并发模型定型时扩展。
roadmap §3.2『隔离级别：Serializable Snapshot Isolation（SSI）』——M1 明确排除（YAGNI）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class IsolationSnapshot(ABC):
    """快照隔离抽象（占位，不实现）。"""

    @abstractmethod
    def snapshot(self) -> Dict[str, Any]:
        """返回当前隔离快照视图。"""
        raise NotImplementedError(
            "M1 最小可用集不实现 SSI；留作 M4 并发模型定型时的扩展点"
        )
