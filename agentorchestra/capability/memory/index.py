"""关键词倒排索引 + 混合检索器

- KeywordIndex: 倒排索引构建/更新/删除/查询
- HybridRetriever: 关键词预筛 + 余弦精排融合 + 衰减打分（v1.1）

分词策略（无外部依赖）：re.findall(r"[A-Za-z0-9_]+|[一-龥]", text.lower())
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .embedder import Embedder, EmbeddingUnavailable
from .models import MemoryEntry, MemoryType
from .storage import MemoryStore

logger = logging.getLogger("agentorchestra.memory.index")

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[一-龥]")
_TAG_WEIGHT = 3.0  # 标签命中权重（相对正文命中 1.0）


def _tokenize(text: str) -> List[str]:
    """简单中英混合分词（小写）。"""
    return _TOKEN_RE.findall((text or "").lower())


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度（两个非零向量）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """解析 ISO8601 字符串为 datetime。失败返回 None。"""
    if not ts:
        return None
    try:
        # 兼容 "+00:00" 与 "Z"
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def compute_decay(
    updated_at_iso: Optional[str],
    importance: float,
    now: Optional[datetime] = None,
    tau_min_days: float = 7.0,
    tau_max_days: float = 180.0,
) -> float:
    """Ebbinghaus 衰减因子。

    Args:
        updated_at_iso: 最后更新时间（ISO8601 UTC）
        importance: 重要性 0~1
        now: 当前时间（默认 datetime.now(UTC)）
        tau_min_days: importance=0 的半衰期
        tau_max_days: importance=1 的半衰期

    Returns:
        衰减因子 ∈ [0, 1]。解析失败/参数异常 → 1.0（不衰减）。
    """
    if tau_min_days <= 0 or tau_max_days <= 0 or tau_min_days > tau_max_days:
        return 1.0
    ts = _parse_iso(updated_at_iso)
    if ts is None:
        return 1.0
    # 统一 tzinfo：若 ts 有 tzinfo 但 now 没有，给 now 加上 ts 的 tzinfo
    if now is None:
        now = datetime.now()
    if ts.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=ts.tzinfo)
    elif ts.tzinfo is None and now.tzinfo is not None:
        ts = ts.replace(tzinfo=now.tzinfo)
    delta_seconds = (now - ts).total_seconds()
    if delta_seconds <= 0:
        return 1.0
    delta_days = delta_seconds / 86400.0
    # τ_days 按 importance 线性插值
    importance_clamped = max(0.0, min(1.0, importance))
    tau_days = tau_min_days + (tau_max_days - tau_min_days) * importance_clamped
    if tau_days <= 0:
        return 1.0
    return 2.0 ** (-delta_days / tau_days)


class KeywordIndex:
    """倒排索引（词 → 文档 id → 命中次数）。

    内存构建，由 MemoryManager 启动时调用 build() 初始化，upsert/delete 时增量更新。
    """

    def __init__(self) -> None:
        # token -> {doc_id -> count}
        self._index: Dict[str, Dict[str, int]] = {}
        # doc_id -> 总分（用于快速返回）
        self._doc_tokens: Dict[str, Counter] = {}

    def build(self, entries: Iterable[MemoryEntry]) -> None:
        """清空并重建倒排索引（startup 全量初始化）。"""
        self._index.clear()
        self._doc_tokens.clear()
        for entry in entries:
            self.update(entry)

    def update(self, entry: MemoryEntry) -> None:
        """增量更新单个 entry 的倒排记录（先删旧记录再写入）。"""
        doc_id = entry.id
        # 删除旧的 token 记录
        self.delete(doc_id)
        tokens = _tokenize(entry.content)
        tag_tokens = _tokenize(" ".join(entry.tags))
        counter: Counter = Counter()
        for t in tokens:
            counter[t] += 1
        for t in tag_tokens:
            counter[t] += int(_TAG_WEIGHT)  # 标签加权
        if not counter:
            return
        for t, c in counter.items():
            self._index.setdefault(t, {})[doc_id] = c
        self._doc_tokens[doc_id] = counter

    def delete(self, doc_id: str) -> None:
        """从倒排索引中移除 doc_id 的全部记录。"""
        counter = self._doc_tokens.pop(doc_id, None)
        if not counter:
            return
        for t in counter:
            bucket = self._index.get(t)
            if bucket and doc_id in bucket:
                del bucket[doc_id]
                if not bucket:
                    del self._index[t]

    def search(self, query: str, top_n: int = 200) -> List[Tuple[str, float]]:
        """返回 (doc_id, score) 列表，按 score 降序，长度 ≤ top_n。"""
        tokens = _tokenize(query)
        if not tokens:
            return []
        scores: Dict[str, float] = {}
        for t in tokens:
            bucket = self._index.get(t)
            if not bucket:
                continue
            for doc_id, count in bucket.items():
                scores[doc_id] = scores.get(doc_id, 0.0) + float(count)
        if not scores:
            return []
        sorted_items = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        return sorted_items[:top_n]


class HybridRetriever:
    """混合检索器：关键词预筛 + 向量精排 + 融合。

    流程：
    1. 关键词预筛 → 候选 doc_ids（≤ top_n）
    2. Embedder 可用：对候选算相似度，归一化融合 α * kw + (1-α) * cos
       不可用：返回关键词排序结果
    3. 类型过滤 + 取 top_k
    """

    def __init__(
        self,
        store: MemoryStore,
        keyword_index: KeywordIndex,
        embedder: Optional[Embedder] = None,
        alpha: float = 0.3,
    ) -> None:
        self.store = store
        self.keyword_index = keyword_index
        self.embedder = embedder
        self.alpha = alpha

    def recall(
        self,
        query: str,
        top_k: int = 5,
        types: Optional[List[MemoryType]] = None,
        namespace: str = "default",
        decay_enabled: bool = False,
        tau_min_days: float = 7.0,
        tau_max_days: float = 180.0,
    ) -> List[MemoryEntry]:
        """按 query 在 namespace 内召回。

        Args:
            query: 查询文本
            top_k: 返回数量
            types: 类型过滤
            namespace: 命名空间（默认 "default"）
            decay_enabled: 是否启用 Ebbinghaus 衰减打分
            tau_min_days / tau_max_days: 半衰期参数
        """
        if not query:
            return []

        ns = namespace or "default"

        # 1. 关键词预筛
        kw_candidates = self.keyword_index.search(query, top_n=200)
        if not kw_candidates:
            return []

        # 2. namespace + 类型过滤（合并）
        types_set = None
        if types:
            types_set = {t.value if isinstance(t, MemoryType) else str(t) for t in types}
        filtered: List[Tuple[str, float]] = []
        for doc_id, score in kw_candidates:
            entry = self.store.get(doc_id)
            if entry is None:
                continue
            entry_ns = entry.namespace or "default"
            if entry_ns != ns:
                continue
            if types_set is not None:
                t_val = entry.type.value if isinstance(entry.type, MemoryType) else str(entry.type)
                if t_val not in types_set:
                    continue
            filtered.append((doc_id, score))
        kw_candidates = filtered

        if not kw_candidates:
            return []

        # 3. 向量精排（如可用）
        use_embedder = self.embedder is not None and self.embedder.available
        cos_scores: Dict[str, float] = {}
        if use_embedder and self.embedder is not None:
            try:
                query_vec = self.embedder.embed(query)
            except EmbeddingUnavailable:
                use_embedder = False
                logger.debug("Embedding 不可用，降级为关键词检索")
            except Exception as e:
                use_embedder = False
                logger.warning(f"Embedding 失败，降级: {e}")

            if use_embedder and query_vec is not None:
                for doc_id, _ in kw_candidates:
                    entry = self.store.get(doc_id)
                    if entry is None or not entry.embedding:
                        continue
                    cos_scores[doc_id] = _cosine(query_vec, entry.embedding)

        # 4. 融合归一化
        if cos_scores:
            kw_scores_only = [s for _, s in kw_candidates]
            kw_max = max(kw_scores_only) if kw_scores_only else 1.0
            kw_min = min(kw_scores_only) if kw_scores_only else 0.0
            cos_vals = list(cos_scores.values())
            cos_max = max(cos_vals) if cos_vals else 1.0
            cos_min = min(cos_vals) if cos_vals else 0.0

            def normalize(v: float, lo: float, hi: float) -> float:
                """min-max 归一化到 [0,1]。"""
                if hi == lo:
                    return 1.0 if v == hi else 0.0
                return (v - lo) / (hi - lo)

            fused: List[Tuple[str, float]] = []
            for doc_id, kw_s in kw_candidates:
                cos_s = cos_scores.get(doc_id)
                if cos_s is None:
                    fused.append((doc_id, normalize(kw_s, kw_min, kw_max)))
                else:
                    score = self.alpha * normalize(kw_s, kw_min, kw_max) + \
                             (1.0 - self.alpha) * normalize(cos_s, cos_min, cos_max)
                    fused.append((doc_id, score))
        else:
            # 仅关键词：直接用分数
            fused = [(doc_id, float(s)) for doc_id, s in kw_candidates]

        # 5. 衰减打分（v1.1）—— 关闭时跳过
        if decay_enabled:
            with_decay: List[Tuple[str, float]] = []
            for doc_id, base_score in fused:
                entry = self.store.get(doc_id)
                if entry is None:
                    with_decay.append((doc_id, base_score))
                    continue
                d = compute_decay(
                    entry.updated_at,
                    entry.importance,
                    tau_min_days=tau_min_days,
                    tau_max_days=tau_max_days,
                )
                with_decay.append((doc_id, base_score * d * max(0.01, float(entry.importance))))
            fused = with_decay

        fused.sort(key=lambda kv: kv[1], reverse=True)

        # 6. 取 top_k，命中强化（touch 重置 updated_at）
        result: List[MemoryEntry] = []
        for doc_id, _score in fused[:top_k]:
            entry = self.store.get(doc_id)
            if entry is None:
                continue
            entry.touch()  # 强化：重置 updated_at，让衰减计时重新开始
            try:
                self.store.upsert(entry)
            except Exception as e:
                logger.debug(f"访问元数据更新失败: {e}")
            result.append(entry)
        return result
