"""SQLite backend - 默认零配置。

使用 aiosqlite（async）。
"""

from __future__ import annotations

from .sqlalchemy_base import SQLAlchemyCheckpointStore


class SQLiteCheckpointStore(SQLAlchemyCheckpointStore):
    """SQLite CheckpointStore（默认）。"""

    _dialect = "sqlite"

    def __init__(self, db_url: str = "sqlite+aiosqlite:///./agent_state.db"):
        # 默认路径
        if db_url == "memory://":
            db_url = "sqlite+aiosqlite:///:memory:"
        super().__init__(db_url)

    @classmethod
    def in_memory(cls) -> "SQLiteCheckpointStore":
        """测试用：内存 SQLite（每个连接独立，AsyncEngine 复用连接池）。"""
        return cls("sqlite+aiosqlite:///:memory:")
