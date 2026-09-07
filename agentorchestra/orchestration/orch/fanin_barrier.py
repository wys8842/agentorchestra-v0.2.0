"""Fan-in Barrier - 多上游汇聚机制

特性：
- 等待所有上游到达才激活
- 超时控制（避免无限等待）
- 强制激活模式（timeout_mode=force）
- 缺失上游感知
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Set


class BarrierTimeoutMode(Enum):
    """Barrier 超时模式"""
    DROP = "drop"      # 超时后丢弃下游消息
    FORCE = "force"    # 超时后强制激活（即使上游未到齐）
    RAISE = "raise"    # 超时后抛异常


@dataclass
class FanInBarrier:
    """Fan-in barrier 状态

    Attributes:
        target: 目标节点
        expected_sources: 期望到达的来源集合
        received_sources: 已到达的来源集合
        created_at: 创建时间戳
        timeout_seconds: 超时时间
        timeout_mode: 超时后行为
        activated: 是否已激活
    """

    target: str
    expected_sources: Set[str] = field(default_factory=set)
    received_sources: Set[str] = field(default_factory=set)
    created_at: float = 0.0
    timeout_seconds: float = 60.0
    timeout_mode: BarrierTimeoutMode = BarrierTimeoutMode.FORCE
    activated: bool = False
    activated_at: Optional[float] = None
    _timer: Optional[asyncio.TimerHandle] = field(default=None, init=False, repr=False)

    def add_source(self, source: str) -> bool:
        """记录一个上游到达

        Returns:
            是否所有上游已到达（True 时可以激活）
        """
        if self.activated:
            return True

        self.received_sources.add(source)

        # 检查是否所有上游都到了
        if self.expected_sources and self.received_sources >= self.expected_sources:
            self.activate()
            return True
        return False

    def activate(self) -> None:
        """激活 barrier（下游可继续执行）"""
        if self.activated:
            return
        self.activated = True
        import time
        self.activated_at = time.time()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def get_missing_sources(self) -> Set[str]:
        """获取未到达的上游集合"""
        return self.expected_sources - self.received_sources

    def is_ready(self) -> bool:
        """是否就绪"""
        return self.activated

    def is_timed_out(self) -> bool:
        """是否超时"""
        if self.activated:
            return False
        import time
        return time.time() - self.created_at > self.timeout_seconds

    def get_progress(self) -> float:
        """获取进度（0-1）"""
        if not self.expected_sources:
            return 1.0
        return len(self.received_sources) / len(self.expected_sources)


class BarrierManager:
    """Barrier 管理器"""

    def __init__(self):
        self._barriers: Dict[str, FanInBarrier] = {}

    def create_barrier(
        self,
        target: str,
        expected_sources: Set[str],
        timeout_seconds: float = 60.0,
        timeout_mode: BarrierTimeoutMode = BarrierTimeoutMode.FORCE,
    ) -> FanInBarrier:
        """创建 barrier"""
        import time
        barrier = FanInBarrier(
            target=target,
            expected_sources=set(expected_sources),
            created_at=time.time(),
            timeout_seconds=timeout_seconds,
            timeout_mode=timeout_mode,
        )
        self._barriers[target] = barrier
        return barrier

    def get_barrier(self, target: str) -> Optional[FanInBarrier]:
        return self._barriers.get(target)

    def remove_barrier(self, target: str) -> Optional[FanInBarrier]:
        return self._barriers.pop(target, None)

    def add_source(self, target: str, source: str) -> Optional[FanInBarrier]:
        """记录上游到达"""
        barrier = self._barriers.get(target)
        if barrier is None:
            return None
        barrier.add_source(source)
        return barrier

    def cleanup_expired(self) -> int:
        """清理超时的 barrier"""
        expired = [t for t, b in self._barriers.items() if b.is_timed_out()]
        for t in expired:
            barrier = self._barriers.pop(t)
            if barrier.timeout_mode == BarrierTimeoutMode.RAISE:
                raise asyncio.TimeoutError(
                    f"fan-in barrier '{t}' timed out, missing: {barrier.get_missing_sources()}"
                )
        return len(expired)

    def all_barriers(self) -> Dict[str, FanInBarrier]:
        return dict(self._barriers)
