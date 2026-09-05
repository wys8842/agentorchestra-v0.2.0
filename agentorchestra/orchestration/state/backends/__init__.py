"""agentorchestra.state.backends - 后端实现（懒加载，避免强制 SQLAlchemy 2.0）。

原实现在包导入时立即 import postgres_backend → postgres_backend
依赖 SQLAlchemy 2.0（`from sqlalchemy.orm import DeclarativeBase`）。在 SQLAlchemy 1.4
环境下导入整个 framework 会失败。

实现方式：
- 内存后端直接 import（零依赖）
- SQLite / Postgres 改为属性访问懒加载（仅当用户实际使用时才校验依赖）
"""

from .memory_backend import InMemoryCheckpointStore


def __getattr__(name: str):
    """懒加载 SQLite / Postgres 后端（首次访问时 import）。"""
    if name == "SQLiteCheckpointStore":
        from .sqlite_backend import SQLiteCheckpointStore
        return SQLiteCheckpointStore
    if name == "PostgresCheckpointStore":
        from .postgres_backend import PostgresCheckpointStore
        return PostgresCheckpointStore
    raise AttributeError(f"module 'agentorchestra.state.backends' has no attribute {name!r}")


__all__ = [
    "InMemoryCheckpointStore",
    "SQLiteCheckpointStore",
    "PostgresCheckpointStore",
]
