"""agentorchestra.state - 持久化与恢复（durable checkpoint）

路线图 M0 / P0。Roadmap 见 docs/superpowers/specs/2026-09-03-enterprise-readiness-roadmap.md
设计见 docs/superpowers/specs/2026-09-03-m0-persistence-design.md

公共 API：
- get_default_store(db_url=None) -> CheckpointStore
- Checkpoint / CheckpointStore / ThreadManager / ThreadState
- InterruptPending / Interrupt
- WALEntry / Snapshot
"""

from .checkpoint import Checkpoint, CheckpointStore
from .interrupt import Interrupt, InterruptPending, InterruptStatus
from .snapshot import Snapshot, SnapshotPolicy, SnapshotWorker
from .thread import ThreadManager, ThreadState, ThreadStatus
from .wal import WALActionType, WALEntry

__all__ = [
    "get_default_store",
    "Checkpoint",
    "CheckpointStore",
    "ThreadManager",
    "ThreadState",
    "ThreadStatus",
    "Interrupt",
    "InterruptPending",
    "InterruptStatus",
    "WALEntry",
    "WALActionType",
    "Snapshot",
    "SnapshotPolicy",
    "SnapshotWorker",
]

_DEFAULT_STORE: "CheckpointStore" = None  # type: ignore[assignment]


def get_default_store(db_url: "str | None" = None) -> CheckpointStore:
    """获取默认 CheckpointStore（懒加载、单例）。

    Args:
        db_url: 可选 SQLAlchemy URL。
            - "sqlite+aiosqlite:///path/to.db"（默认）
            - "postgresql+asyncpg://user:pwd@host/db"
            - "memory://" 或 None → SQLite 本机文件 agent_state.db
            - "in_memory://" → InMemoryCheckpointStore（无 DB 依赖）

    Returns:
        CheckpointStore 实例（已 init）
    """
    global _DEFAULT_STORE
    if _DEFAULT_STORE is not None and db_url is None:
        return _DEFAULT_STORE

    if db_url is None or db_url == "memory://":
        # 默认零配置：本地 SQLite 文件
        db_url = "sqlite+aiosqlite:///./agent_state.db"

    if db_url == "in_memory://":
        from .backends.memory_backend import InMemoryCheckpointStore

        store: CheckpointStore = InMemoryCheckpointStore()
    elif db_url.startswith("postgresql"):
        from .backends.postgres_backend import PostgresCheckpointStore

        store = PostgresCheckpointStore(db_url)
    elif db_url.startswith("sqlite"):
        from .backends.sqlite_backend import SQLiteCheckpointStore

        store = SQLiteCheckpointStore(db_url)
    else:
        raise ValueError(f"不支持的 db_url: {db_url}")

    # 懒初始化
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # 已经在事件循环里：init 由 caller 负责
        pass
    else:
        asyncio.run(store.init())

    if db_url is None:
        _DEFAULT_STORE = store

    return store


def reset_default_store() -> None:
    """重置默认 store（测试用）。"""
    global _DEFAULT_STORE
    _DEFAULT_STORE = None  # type: ignore[assignment]
