"""PostgreSQL backend - 生产用。

需要安装 asyncpg（optional-dependencies: postgres）。
"""

from __future__ import annotations

from .sqlalchemy_base import SQLAlchemyCheckpointStore


class PostgresCheckpointStore(SQLAlchemyCheckpointStore):
    """PostgreSQL CheckpointStore。"""

    _dialect = "postgres"

    def __init__(self, db_url: str):
        if not db_url.startswith("postgresql+asyncpg"):
            # 兼容 postgresql:// → 自动转换
            if db_url.startswith("postgresql://"):
                db_url = "postgresql+asyncpg://" + db_url[len("postgresql://") :]
            else:
                raise ValueError(
                    f"Postgres backend 需要 postgresql+asyncpg:// URL；got: {db_url}"
                )
        super().__init__(db_url)
