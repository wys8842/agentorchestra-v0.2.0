"""存储层 - 三种后端 + MemoryStore 包装

- BaseMemoryBackend: 抽象接口
- InMemoryBackend: 进程内
- JsonlBackend: JSONL 文件（追加式，可读）
- SqliteBackend: SQLite 文件（跨进程安全 + WAL）
- MemoryStore: 业务层统一入口
"""

from __future__ import annotations

import json
import logging
import os
import struct
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import MemoryEntry

logger = logging.getLogger("agentorchestra.memory.storage")


def _pack_vector(vec: List[float]) -> bytes:
    """将向量打包为二进制（little-endian float32 数组）。"""
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_vector(buf: bytes) -> List[float]:
    """解包二进制向量。"""
    n = len(buf) // 4
    return list(struct.unpack(f"<{n}f", buf))


class BaseMemoryBackend(ABC):
    """存储后端抽象接口。"""

    @abstractmethod
    def upsert(self, entry: MemoryEntry) -> None:
        """写入或更新一条记忆。"""
        raise NotImplementedError

    @abstractmethod
    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        """按 id 读取记忆，不存在时返回 None。"""
        raise NotImplementedError

    @abstractmethod
    def delete(self, entry_id: str) -> bool:
        """按 id 删除记忆，返回是否存在。"""
        raise NotImplementedError

    @abstractmethod
    def all(self, namespace: str = "default") -> List[MemoryEntry]:
        """返回命名空间下的全部记忆。"""
        raise NotImplementedError

    @abstractmethod
    def save_embedding(self, entry_id: str, vec: List[float]) -> None:
        """保存记忆的向量。"""
        raise NotImplementedError

    @abstractmethod
    def get_embedding(self, entry_id: str) -> Optional[List[float]]:
        """读取记忆的向量，不存在时返回 None。"""
        raise NotImplementedError

    @abstractmethod
    def stats(self) -> Dict[str, Any]:
        """返回后端统计信息。"""
        raise NotImplementedError

    def close(self) -> None:
        """可选：关闭后端（文件句柄、连接等）。"""
        pass


class InMemoryBackend(BaseMemoryBackend):
    """内存后端 - 适合测试与轻量场景。"""

    def __init__(self) -> None:
        self._entries: Dict[str, MemoryEntry] = {}
        self._embeddings: Dict[str, List[float]] = {}

    def upsert(self, entry: MemoryEntry) -> None:
        """写入或更新一条记忆。"""
        self._entries[entry.id] = entry

    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        """按 id 读取记忆。"""
        return self._entries.get(entry_id)

    def delete(self, entry_id: str) -> bool:
        """按 id 删除记忆，返回是否存在。"""
        existed = entry_id in self._entries
        self._entries.pop(entry_id, None)
        self._embeddings.pop(entry_id, None)
        return existed

    def all(self, namespace: str = "default") -> List[MemoryEntry]:
        """返回命名空间下的全部记忆。"""
        return [e for e in self._entries.values() if (e.namespace or "default") == namespace]

    def save_embedding(self, entry_id: str, vec: List[float]) -> None:
        """保存记忆的向量。"""
        self._embeddings[entry_id] = list(vec)

    def get_embedding(self, entry_id: str) -> Optional[List[float]]:
        """读取记忆的向量。"""
        vec = self._embeddings.get(entry_id)
        return list(vec) if vec is not None else None

    def stats(self) -> Dict[str, Any]:
        """返回后端统计信息。"""
        return {
            "backend": "memory",
            "entries": len(self._entries),
            "embeddings": len(self._embeddings),
        }


class JsonlBackend(BaseMemoryBackend):
    """JSONL 文件后端 - 追加式、人类可读。

    文件结构：
        {filepath}.jsonl     - 每行一条 entry 的 JSON（不含 embedding）
        {filepath}.emb.jsonl - 每行 {id, dim, vec: [...]} 的 JSON
    """

    def __init__(self, filepath: str = "memory/memories.jsonl") -> None:
        self.filepath = Path(filepath)
        if self.filepath.parent and str(self.filepath.parent) not in ("", "."):
            self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.emb_filepath = self.filepath.with_suffix(".emb.jsonl")
        self._lock = threading.Lock()
        self._cache: Dict[str, MemoryEntry] = {}
        self._emb_cache: Dict[str, List[float]] = {}
        self._loaded = False
        self._load()

    def _load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self.filepath.exists():
                with open(self.filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            entry = MemoryEntry.from_dict(data)
                            self._cache[entry.id] = entry
                        except Exception as e:
                            logger.warning(f"JsonlBackend: 无法解析行: {e}")
            if self.emb_filepath.exists():
                with open(self.emb_filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            self._emb_cache[data["id"]] = list(data.get("vec", []))
                        except Exception as e:
                            logger.warning(f"JsonlBackend: 无法解析 embedding 行: {e}")
            self._loaded = True

    def upsert(self, entry: MemoryEntry) -> None:
        """写入或更新一条记忆（追加到 JSONL 文件）。"""
        with self._lock:
            self._cache[entry.id] = entry
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        """按 id 读取记忆。"""
        entry = self._cache.get(entry_id)
        if entry is None:
            return None
        emb = self._emb_cache.get(entry_id)
        if emb is not None:
            entry.embedding = emb
        return entry

    def delete(self, entry_id: str) -> bool:
        """按 id 删除记忆（重写文件去掉该行），返回是否存在。"""
        with self._lock:
            existed = entry_id in self._cache
            self._cache.pop(entry_id, None)
            self._emb_cache.pop(entry_id, None)
            if existed:
                if self.filepath.exists():
                    tmp = self.filepath.with_suffix(".tmp")
                    with open(self.filepath, "r", encoding="utf-8") as f, open(tmp, "w", encoding="utf-8") as out:
                        for line in f:
                            try:
                                data = json.loads(line)
                                if data.get("id") != entry_id:
                                    out.write(line)
                            except Exception:
                                out.write(line)
                    os.replace(tmp, self.filepath)
                if self.emb_filepath.exists():
                    tmp = self.emb_filepath.with_suffix(".tmp")
                    with open(self.emb_filepath, "r", encoding="utf-8") as f, open(tmp, "w", encoding="utf-8") as out:
                        for line in f:
                            try:
                                data = json.loads(line)
                                if data.get("id") != entry_id:
                                    out.write(line)
                            except Exception:
                                out.write(line)
                    os.replace(tmp, self.emb_filepath)
            return existed

    def all(self, namespace: str = "default") -> List[MemoryEntry]:
        """返回命名空间下的全部记忆。"""
        return [e for e in self._cache.values() if (e.namespace or "default") == namespace]

    def save_embedding(self, entry_id: str, vec: List[float]) -> None:
        """保存记忆的向量（追加到 emb 文件）。"""
        with self._lock:
            self._emb_cache[entry_id] = list(vec)
            with open(self.emb_filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps({"id": entry_id, "vec": list(vec)}, ensure_ascii=False) + "\n")

    def get_embedding(self, entry_id: str) -> Optional[List[float]]:
        """读取记忆的向量。"""
        vec = self._emb_cache.get(entry_id)
        return list(vec) if vec is not None else None

    def stats(self) -> Dict[str, Any]:
        """返回后端统计信息。"""
        return {
            "backend": "jsonl",
            "filepath": str(self.filepath),
            "entries": len(self._cache),
            "embeddings": len(self._emb_cache),
        }


class SqliteBackend(BaseMemoryBackend):
    """SQLite 后端 - 跨进程安全，WAL 日志模式。

    表结构：
        memories(id PK, type, content, tags CSV, importance REAL,
                 source_session, source_agent, created_at, updated_at,
                 access_count INTEGER, last_accessed_at)
        memory_embeddings(memory_id PK, vec BLOB)
    """

    def __init__(self, db_path: str = "memory/memories.db") -> None:
        import sqlite3

        if os.path.dirname(db_path):
            os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self.db_path = db_path
        self._lock = threading.Lock()
        self._closed = False
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                importance REAL NOT NULL DEFAULT 0.5,
                source_session TEXT NOT NULL DEFAULT '',
                source_agent TEXT NOT NULL DEFAULT '',
                namespace TEXT NOT NULL DEFAULT 'default',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed_at TEXT
            )"""
        )
        # v1.1 迁移：先补齐老库 namespace 列，再建索引（顺序很关键）
        self._migrate_namespace_column()

        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS memory_embeddings (
                memory_id TEXT PRIMARY KEY,
                vec BLOB NOT NULL,
                FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_namespace ON memories(namespace)"
        )
        self._conn.commit()

    def _migrate_namespace_column(self) -> None:
        """若 memories 表缺 namespace 列，则 ALTER TABLE 添加（v1 兼容）。"""
        try:
            cols = [
                row[1] for row in self._conn.execute("PRAGMA table_info(memories)").fetchall()
            ]
            if "namespace" not in cols:
                with self._lock:
                    self._conn.execute(
                        "ALTER TABLE memories ADD COLUMN namespace TEXT NOT NULL DEFAULT 'default'"
                    )
                    self._conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_memories_namespace ON memories(namespace)"
                    )
                    self._conn.commit()
                    logger.info("SqliteBackend: 已迁移 schema，添加 namespace 列")
        except Exception as e:
            logger.warning(f"SqliteBackend: namespace 列迁移失败（{e}），继续运行")

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"SqliteBackend 已关闭: {self.db_path}")

    def upsert(self, entry: MemoryEntry) -> None:
        """写入或更新一条记忆（INSERT OR REPLACE）。"""
        tags_csv = ",".join(entry.tags)
        ns = entry.namespace or "default"
        with self._lock:
            self._check_open()
            self._conn.execute(
                """INSERT OR REPLACE INTO memories
                   (id, type, content, tags, importance, source_session, source_agent,
                    namespace, created_at, updated_at, access_count, last_accessed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.id,
                    entry.type.value if hasattr(entry.type, "value") else str(entry.type),
                    entry.content,
                    tags_csv,
                    float(entry.importance),
                    entry.source_session,
                    entry.source_agent,
                    ns,
                    entry.created_at,
                    entry.updated_at,
                    int(entry.access_count),
                    entry.last_accessed_at,
                ),
            )
            self._conn.commit()

    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        """按 id 读取记忆（SQLite 查询）。"""
        with self._lock:
            self._check_open()
            row = self._conn.execute(
                """SELECT id, type, content, tags, importance, source_session,
                          source_agent, namespace, created_at, updated_at, access_count,
                          last_accessed_at
                   FROM memories WHERE id = ?""",
                (entry_id,),
            ).fetchone()
        if row is None:
            return None
        data = {
            "id": row[0],
            "type": row[1],
            "content": row[2],
            "tags": row[3],
            "importance": row[4],
            "source_session": row[5],
            "source_agent": row[6],
            "namespace": row[7] or "default",
            "created_at": row[8],
            "updated_at": row[9],
            "access_count": row[10],
            "last_accessed_at": row[11],
        }
        entry = MemoryEntry.from_dict(data)
        emb = self.get_embedding(entry_id)
        if emb is not None:
            entry.embedding = emb
        return entry

    def delete(self, entry_id: str) -> bool:
        """按 id 删除记忆（级联删向量），返回是否存在。"""
        with self._lock:
            self._check_open()
            cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (entry_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def all(self, namespace: str = "default") -> List[MemoryEntry]:
        """返回命名空间下的全部记忆（按更新时间倒序）。"""
        ns = namespace or "default"
        with self._lock:
            self._check_open()
            rows = self._conn.execute(
                """SELECT id, type, content, tags, importance, source_session,
                          source_agent, namespace, created_at, updated_at, access_count,
                          last_accessed_at
                   FROM memories WHERE namespace = ? ORDER BY updated_at DESC""",
                (ns,),
            ).fetchall()
        entries = []
        for row in rows:
            data = {
                "id": row[0],
                "type": row[1],
                "content": row[2],
                "tags": row[3],
                "importance": row[4],
                "source_session": row[5],
                "source_agent": row[6],
                "namespace": row[7] or "default",
                "created_at": row[8],
                "updated_at": row[9],
                "access_count": row[10],
                "last_accessed_at": row[11],
            }
            entries.append(MemoryEntry.from_dict(data))
        return entries

    def save_embedding(self, entry_id: str, vec: List[float]) -> None:
        """保存记忆的向量（二进制打包后写入）。"""
        blob = _pack_vector(vec)
        with self._lock:
            self._check_open()
            self._conn.execute(
                """INSERT OR REPLACE INTO memory_embeddings (memory_id, vec) VALUES (?, ?)""",
                (entry_id, blob),
            )
            self._conn.commit()

    def get_embedding(self, entry_id: str) -> Optional[List[float]]:
        """读取记忆的向量。"""
        with self._lock:
            self._check_open()
            row = self._conn.execute(
                "SELECT vec FROM memory_embeddings WHERE memory_id = ?",
                (entry_id,),
            ).fetchone()
        if row is None:
            return None
        return _unpack_vector(row[0])

    def stats(self) -> Dict[str, Any]:
        """返回后端统计信息。"""
        with self._lock:
            self._check_open()
            count = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            emb_count = self._conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
        return {
            "backend": "sqlite",
            "db_path": self.db_path,
            "entries": count,
            "embeddings": emb_count,
        }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._conn.close()


class MemoryStore:
    """记忆存储上层接口 - 业务侧只用它。

    对外暴露：
    - upsert/get/delete/iter_all/stats
    - 与后端解耦
    """

    def __init__(self, backend: BaseMemoryBackend) -> None:
        self.backend = backend

    def upsert(self, entry: MemoryEntry) -> None:
        """写入或更新一条记忆。"""
        self.backend.upsert(entry)

    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        """按 id 读取记忆。"""
        return self.backend.get(entry_id)

    def delete(self, entry_id: str) -> bool:
        """按 id 删除记忆，返回是否存在。"""
        return self.backend.delete(entry_id)

    def iter_all(self, namespace: str = "default") -> Iterable[MemoryEntry]:
        """迭代命名空间下的全部记忆。"""
        return iter(self.backend.all(namespace=namespace))

    def stats(self) -> Dict[str, Any]:
        """返回后端统计信息。"""
        return self.backend.stats()

    def close(self) -> None:
        """关闭后端资源。"""
        self.backend.close()
