"""Snapshot - 周期快照与 WAL 压缩。

策略：双阈值（默认 1000 条 WAL OR 60s）。SnapshotWorker 是后台 asyncio 任务，
    到达阈值时压缩 WAL（生成快照 + 截断旧 WAL）。

"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .checkpoint import CheckpointStore

logger = logging.getLogger("agentorchestra.state.snapshot")


@dataclass
class Snapshot:
    """一个周期快照。

    Attributes:
        thread_id: 所属 thread
        snapshot_id: 全局唯一
        up_to_seq: 该快照代表 WAL 中前 up_to_seq 条已应用
        state: 完整状态（与最新 checkpoint 等价）
        metadata: 任意元数据（如 step/token_count）
        created_at: 创建时间
    """

    thread_id: str
    snapshot_id: str
    up_to_seq: int
    state: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（created_at 用 ISO8601 字符串）。"""
        return {
            "thread_id": self.thread_id,
            "snapshot_id": self.snapshot_id,
            "up_to_seq": self.up_to_seq,
            "state": self.state,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class SnapshotPolicy:
    """快照策略。

    Attributes:
        wal_threshold: WAL 条目数阈值（默认 1000）
        interval_seconds: 时间阈值（默认 60s）
        enabled: 是否启用后台 worker
    """

    wal_threshold: int = 1000
    interval_seconds: float = 60.0
    enabled: bool = False  # 默认关闭（避免无后台任务时噪声）


class SnapshotWorker:
    """后台快照 worker。

    启动后定期检查所有 thread，到达阈值则拍快照。
    可与 Agent.arun() 协同：在事件循环中常驻。


    - _stop 事件在 __init__ 中直接创建，不依赖 loop.is_running()（消除竞态）
    - start() 路径也独立处理 stop 事件
    - 启动时自动从 store 发现活跃 threads（不依赖外部注入）
    """

    def __init__(
        self,
        store: CheckpointStore,
        policy: Optional[SnapshotPolicy] = None,
        thread_ids_provider: Optional[Callable[[], List[str]]] = None,
    ):
        self.store = store
        self.policy = policy or SnapshotPolicy()
        self._task: Optional[asyncio.Task[Any]] = None
        #直接创建 Event（不依赖 get_event_loop；asyncio.Event() 无 loop 也能构造）
        self._stop: asyncio.Event = asyncio.Event()
        # 测试便捷：注入需要监控的 thread_ids；默认从 store 自动发现
        self._thread_ids_provider: Callable[[], List[str]] = thread_ids_provider or self._default_thread_ids

    async def _default_thread_ids(self) -> List[str]:
        """默认 thread_ids 提供器：从 store 列举所有活跃 thread。"""
        #避免在无 loop 时调用 store.list_threads()
        try:
            rows = await self.store.list_threads()  # type: ignore[attr-defined]
            return [r["thread_id"] for r in rows if r.get("status") != "closed"]
        except AttributeError:
            # store 不支持 list_threads 时回退空列表（保留向后兼容）
            return []
        except Exception as e:
            logger.warning(f"snapshot thread discovery failed: {e}")
            return []

    async def maybe_snapshot(self, thread_id: str) -> Optional[Snapshot]:
        """根据策略决定是否给指定 thread 拍快照。"""
        if not self.policy.enabled:
            return None

        max_seq = await self.store.max_wal_seq(thread_id)
        latest_cp = await self.store.latest_checkpoint(thread_id)
        if not latest_cp:
            return None

        latest_snap = await self.store.latest_snapshot(thread_id)
        last_snap_seq = latest_snap.up_to_seq if latest_snap else 0
        delta = max_seq - last_snap_seq

        should_by_count = delta >= self.policy.wal_threshold
        should_by_time = False
        if latest_snap:
            age = (datetime.now() - latest_snap.created_at).total_seconds()
            should_by_time = age >= self.policy.interval_seconds

        if not (should_by_count or should_by_time):
            return None

        snap = Snapshot(
            thread_id=thread_id,
            snapshot_id=f"snap-{max_seq}-{int(datetime.now().timestamp())}",
            up_to_seq=max_seq,
            state=latest_cp.state,
            metadata={"triggered_by": "count" if should_by_count else "time"},
        )
        await self.store.save_snapshot(snap)
        logger.info(f"snapshot saved: {snap.snapshot_id} up_to_seq={max_seq}")
        return snap

    async def run_once(self) -> int:
        """执行一轮检查；返回本轮拍下的快照数。"""
        count = 0
        thread_ids = await _maybe_await(self._thread_ids_provider)
        for tid in thread_ids:
            try:
                snap = await self.maybe_snapshot(tid)
                if snap is not None:
                    count += 1
            except Exception as e:
                logger.warning(f"snapshot failed for {tid}: {e}")
        return count

    async def _loop(self) -> None:
        #始终使用 _stop.wait()；不再有 `_stop is None → return` 短路
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception as e:
                logger.warning(f"snapshot loop error: {e}")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.policy.interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    def start(self) -> asyncio.Task:
        """启动后台任务（返回 Task）。"""
        if self._task is not None and not self._task.done():
            return self._task
        if not self.policy.enabled:
            raise RuntimeError("SnapshotPolicy.enabled=False；先启用再 start")

        #不重置 _stop，避免丢失已 set 的信号
        loop = asyncio.get_event_loop()
        self._task = loop.create_task(self._loop())
        return self._task

    async def stop(self) -> None:
        """停止后台任务。"""
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        #保留 _stop 实例供下次 start() 复用（避免 Event 重建引入 bug）


async def _maybe_await(value: Any) -> Any:
    """如果 value 是 awaitable 则 await；否则直接返回。"""
    if asyncio.iscoroutine(value):
        return await value
    return value
