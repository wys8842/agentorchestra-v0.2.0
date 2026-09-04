"""MemoryManager - 跨会话持久记忆的统一入口

职责：
- remember / remember_batch / forget / stats
- 调用：基于 Embedder 做相似度去重
- recall 走 HybridRetriever
- 与存储/索引/embedder 解耦

设计：M4 去重逻辑在此实现：相同内容高相似度 → 更新 updated_at 而非新增。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .embedder import Embedder, EmbeddingUnavailable
from .index import HybridRetriever, KeywordIndex, _cosine
from .models import MemoryEntry, MemoryType
from .storage import (
    BaseMemoryBackend,
    InMemoryBackend,
    JsonlBackend,
    MemoryStore,
    SqliteBackend,
)

logger = logging.getLogger("agentorchestra.memory.manager")


class MemoryManager:
    """记忆系统统一入口。

    用法：
        mgr = MemoryManager.from_config(config, llm=llm)
        eid = mgr.remember(content="用户偏好 X", type=MemoryType.PREFERENCE)
        hits = mgr.recall("用户偏好", top_k=5)
    """

    def __init__(
        self,
        store: MemoryStore,
        embedder: Embedder,
        keyword_index: KeywordIndex,
        retriever: HybridRetriever,
        dedup_threshold: float = 0.92,
        default_namespace: str = "default",
        decay_enabled: bool = False,
        tau_min_days: float = 7.0,
        tau_max_days: float = 180.0,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.keyword_index = keyword_index
        self.retriever = retriever
        self.dedup_threshold = dedup_threshold
        self.default_namespace = default_namespace or "default"
        self.decay_enabled = bool(decay_enabled)
        self.tau_min_days = float(tau_min_days)
        self.tau_max_days = float(tau_max_days)

    @classmethod
    def from_config(
        cls,
        config: Any,
        llm: Optional[Any] = None,
        default_namespace: Optional[str] = None,
    ) -> "MemoryManager":
        """从 Config 与（可选）LLM 实例构造。

        Args:
            default_namespace: 覆盖 config.memory_namespace 的显式 namespace

        Raises:
            FileNotFoundError / PermissionError: 目录不可写
            ValueError: 配置非法
        """
        backend_name = (getattr(config, "memory_backend", "sqlite") or "sqlite").lower()

        if backend_name == "sqlite":
            db_path = getattr(config, "memory_db_path", "memory/memories.db")
            parent = Path(db_path).parent
            if str(parent) and str(parent) != ".":
                parent.mkdir(parents=True, exist_ok=True)
            backend: BaseMemoryBackend = SqliteBackend(db_path)
        elif backend_name == "jsonl":
            jsonl_path = getattr(config, "memory_jsonl_path", "memory/memories.jsonl")
            parent = Path(jsonl_path).parent
            if str(parent) and str(parent) != ".":
                parent.mkdir(parents=True, exist_ok=True)
            backend = JsonlBackend(jsonl_path)
        elif backend_name == "memory":
            backend = InMemoryBackend()
        else:
            raise ValueError(f"未知的 memory_backend: {backend_name}")

        store = MemoryStore(backend)
        embed_enabled = bool(getattr(config, "memory_embedding_enabled", True))
        embedder = Embedder(llm=llm, enabled=embed_enabled)
        keyword_index = KeywordIndex()
        keyword_index.build(store.iter_all())
        retriever = HybridRetriever(store, keyword_index, embedder=embedder)
        dedup = float(getattr(config, "memory_dedup_threshold", 0.92))

        ns = default_namespace or getattr(config, "memory_namespace", "default") or "default"
        decay_enabled = bool(getattr(config, "memory_decay_enabled", False))
        tau_min = float(getattr(config, "memory_decay_tau_min_days", 7.0))
        tau_max = float(getattr(config, "memory_decay_tau_max_days", 180.0))
        return cls(
            store=store,
            embedder=embedder,
            keyword_index=keyword_index,
            retriever=retriever,
            dedup_threshold=dedup,
            default_namespace=ns,
            decay_enabled=decay_enabled,
            tau_min_days=tau_min,
            tau_max_days=tau_max,
        )

    @staticmethod
    def _resolve_namespace(namespace: Optional[str]) -> str:
        """namespace 解析（M6 多租户）。

        - 显式 namespace 且带 tenant 前缀 → 原样返回
        - tenant 上下文存在 → 前缀 tenant.namespace（隔离）
        - 否则 → 显式 namespace 或 default
        """
        try:
            from agentorchestra.governance.tenancy.tenant import TenantManager
            tenant = TenantManager.current()
        except Exception:
            tenant = None

        ns = namespace or "default"
        if tenant is None:
            return ns
        # 已带 tenant 前缀则不再叠加
        if ns.startswith(tenant.tenant_id + ":"):
            return ns
        return f"{tenant.namespace}:{ns}" if ns != "default" else tenant.namespace

    # ==================== 写入 ====================

    def remember(
        self,
        content: str,
        type: MemoryType = MemoryType.FACT,
        tags: Optional[List[str]] = None,
        importance: float = 0.5,
        source_session: str = "",
        source_agent: str = "",
        namespace: Optional[str] = None,
    ) -> str:
        """写入一条记忆（含去重）。

        Args:
            content: 文本内容
            type: 记忆类型
            tags: 标签列表
            importance: 重要性 0~1
            source_session / source_agent: 元数据
            namespace: 命名空间（None → 使用 default_namespace）

        Returns:
            条目 ID（新写入或已更新）
        """
        if not content or not content.strip():
            raise ValueError("content 不能为空")

        ns = self._resolve_namespace(namespace)
        entry = MemoryEntry(
            type=type,
            content=content.strip(),
            tags=list(tags or []),
            importance=float(importance),
            source_session=source_session,
            source_agent=source_agent,
            namespace=ns,
        )

        # 去重（仅在 embedding 可用时启用；同 namespace 内）
        existing_id = self._find_similar_existing(entry)
        if existing_id is not None:
            old = self.store.get(existing_id)
            if old is not None:
                # 合并：保留旧 id，更新内容/tags/touch；importance 取 max
                old.content = entry.content
                if entry.tags:
                    old.tags = sorted(set(old.tags) | set(entry.tags))
                old.importance = max(old.importance, entry.importance)
                old.touch()
                self._save_with_embedding(old)
                return old.id

        # 新增
        self._save_with_embedding(entry)
        return entry.id

    def remember_batch(self, candidates: List[Dict[str, Any]]) -> List[str]:
        """批量写入候选记忆（自动总结使用）。

        Args:
            candidates: [{"content": ..., "type": ..., "tags": [...], "importance": ...}, ...]

        Returns:
            写入成功的 entry id 列表
        """
        ids: List[str] = []
        for c in candidates:
            try:
                type_val = c.get("type", MemoryType.FACT)
                if isinstance(type_val, str):
                    try:
                        type_val = MemoryType(type_val)
                    except ValueError:
                        type_val = MemoryType.FACT
                eid = self.remember(
                    content=c.get("content", ""),
                    type=type_val,
                    tags=c.get("tags", []),
                    importance=float(c.get("importance", 0.5)),
                )
                ids.append(eid)
            except Exception as e:
                logger.warning(f"remember_batch 单条失败: {e}")
        return ids

    def _find_similar_existing(self, candidate: MemoryEntry) -> Optional[str]:
        """若新条目与已有条目（同 namespace + 同 type）相似度 ≥ 阈值，返回已有 id。"""
        if not (self.embedder.available):
            return None
        try:
            vec = self.embedder.embed(candidate.content)
        except EmbeddingUnavailable:
            return None
        except Exception as e:
            logger.debug(f"去重 embedding 失败，跳过: {e}")
            return None
        if vec is None:
            return None
        best_id: Optional[str] = None
        best_score = 0.0
        # 去重仅在同 namespace 内做
        for existing in self.store.iter_all(namespace=candidate.namespace or "default"):
            if existing.type != candidate.type:
                continue
            ev = existing.embedding
            if ev is None:
                continue
            score = _cosine(vec, ev)
            if score >= self.dedup_threshold and score > best_score:
                best_score = score
                best_id = existing.id
        return best_id

    def _save_with_embedding(self, entry: MemoryEntry) -> None:
        """写入 entry + 若 embedder 可用则算 embedding 同步落盘 + 索引更新。"""
        if self.embedder.available and entry.embedding is None:
            try:
                vec = self.embedder.embed(entry.content)
                if vec is not None:
                    entry.embedding = vec
                    try:
                        self.store.backend.save_embedding(entry.id, vec)
                    except Exception as e:
                        logger.debug(f"embedding 落盘失败: {e}")
            except EmbeddingUnavailable:
                pass
            except Exception as e:
                logger.debug(f"去重 embedding 失败，跳过: {e}")
        self.store.upsert(entry)
        self.keyword_index.update(entry)

    # ==================== 检索 ====================

    def recall(
        self,
        query: str,
        top_k: int = 5,
        types: Optional[List[MemoryType]] = None,
        namespace: Optional[str] = None,
    ) -> List[MemoryEntry]:
        """按 query 在 namespace 内召回。

        Args:
            query: 查询文本
            top_k: 返回数量
            types: 类型过滤
            namespace: 命名空间（None → 使用 default_namespace）
        """
        ns = self._resolve_namespace(namespace)
        return self.retriever.recall(
            query,
            top_k=top_k,
            types=types,
            namespace=ns,
            decay_enabled=self.decay_enabled,
            tau_min_days=self.tau_min_days,
            tau_max_days=self.tau_max_days,
        )

    def list(
        self,
        types: Optional[List[MemoryType]] = None,
        limit: int = 50,
        namespace: Optional[str] = None,
    ) -> List[MemoryEntry]:
        """列出记忆条目（按 updated_at 倒序，限定 namespace）。"""
        types_set = None
        if types:
            types_set = {t.value if isinstance(t, MemoryType) else str(t) for t in types}
        ns = self._resolve_namespace(namespace)
        items: List[MemoryEntry] = []
        for entry in self.store.iter_all(namespace=ns):
            if types_set is not None:
                t_val = entry.type.value if isinstance(entry.type, MemoryType) else str(entry.type)
                if t_val not in types_set:
                    continue
            items.append(entry)
        items.sort(key=lambda e: e.updated_at, reverse=True)
        return items[:limit]

    # ==================== 删除/统计 ====================

    def forget(self, entry_id: str) -> bool:
        """删除一条记忆（同步清理索引），返回是否存在。"""
        ok = self.store.delete(entry_id)
        if ok:
            self.keyword_index.delete(entry_id)
        return ok

    def stats(self) -> Dict[str, Any]:
        """返回记忆系统统计信息。"""
        return {
            "store": self.store.stats(),
            "dedup_threshold": self.dedup_threshold,
            "embedder_available": self.embedder.available,
            "default_namespace": self.default_namespace,
            "decay_enabled": self.decay_enabled,
        }

    def close(self) -> None:
        """关闭底层存储。"""
        try:
            self.store.close()
        except Exception:
            pass
