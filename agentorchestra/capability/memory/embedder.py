"""Embedding 封装

- EmbeddingUnavailable: Embedder 不可用信号
- Embedder: 复用 SymphonyLLM 提供 embedding（OpenAI 兼容协议）
  - 缓存：sha256(text) → vec
  - 失败/禁用 → EmbeddingUnavailable，HybridRetriever 自动降级关键词
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("agentorchestra.memory.embedder")


class EmbeddingUnavailable(Exception):
    """Embedding 不可用（禁用/失败/无可用 LLM）。"""


class Embedder:
    """Embedding 封装。

    使用方式：
        embedder = Embedder(llm=llm_instance, enabled=True)
        if embedder.available:
            vecs = embedder.embed_texts(["...", "..."])
        vec = embedder.embed("...")  # 不可用返回 None

    LLM 调用协议：复用 SymphonyLLM 的 base_url + model，调 OpenAI 兼容 /embeddings。
    若 LLM 提供商不支持 embedding 协议，调用失败 → 抛出 EmbeddingUnavailable。
    """

    DEFAULT_CACHE_SIZE = 10000

    def __init__(
        self,
        llm: Optional[Any] = None,
        enabled: bool = True,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        self.llm = llm
        self._enabled = enabled and llm is not None
        self._cache: Dict[str, List[float]] = {}
        self._cache_size = cache_size

    @property
    def available(self) -> bool:
        """是否启用且有可用 LLM。"""
        return self._enabled and self.llm is not None

    def embed(self, text: str) -> Optional[List[float]]:
        """单条 embedding。失败抛 EmbeddingUnavailable。"""
        result = self.embed_texts([text])
        if not result:
            return None
        return result[0]

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量 embedding。失败抛 EmbeddingUnavailable；缓存命中直接返回。

        注意：与 embed() 不同，本方法不会返回 None；失败抛异常。
        """
        if not self.available:
            raise EmbeddingUnavailable("Embedder 不可用（未启用或无 LLM）")
        if not texts:
            return []

        # 计算缓存键，分离命中与缺失
        hashes = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
        results: List[Optional[List[float]]] = [None] * len(texts)
        missing_idx: List[int] = []
        missing_texts: List[str] = []
        for i, h in enumerate(hashes):
            if h in self._cache:
                cached = self._cache[h]
                results[i] = cached
            else:
                missing_idx.append(i)
                missing_texts.append(texts[i])

        if not missing_texts:
            typed: List[List[float]] = [r for r in results if r is not None]
            return typed

        # 调 LLM
        try:
            vecs = self._call_embedding(missing_texts)
        except EmbeddingUnavailable:
            raise
        except Exception as e:
            logger.warning(f"Embedding 调用失败: {e}")
            raise EmbeddingUnavailable(str(e)) from e

        # 回填缓存
        for i, v in zip(missing_idx, vecs):
            results[i] = v
            h = hashes[i]
            self._cache[h] = list(v)
        self._evict_if_needed()

        typed_all: List[List[float]] = [r for r in results if r is not None]
        return typed_all

    def _call_embedding(self, texts: List[str]) -> List[List[float]]:
        """通过 LLM 的 base_url 调 /embeddings。

        兼容 SymphonyLLM：使用 llm.model 与 llm.base_url，HTTP POST 请求体为 OpenAI 风格。
        """
        import urllib.error
        import urllib.request

        llm = self.llm
        base_url = (getattr(llm, "base_url", None) or "").rstrip("/")
        model = getattr(llm, "model", None)
        api_key = getattr(llm, "api_key", None)
        if not base_url or not model or not api_key:
            raise EmbeddingUnavailable("LLM 缺少 base_url/model/api_key")

        url = f"{base_url}/embeddings"
        payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=getattr(llm, "timeout", 30)) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            raise EmbeddingUnavailable(f"HTTP 调用失败: {e}")

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise EmbeddingUnavailable(f"响应非 JSON: {e}")

        items = data.get("data") or data.get("embeddings") or []
        vecs: List[List[float]] = []
        for item in items:
            v = item.get("embedding") if isinstance(item, dict) else None
            if v is None:
                raise EmbeddingUnavailable("响应中缺少 embedding 字段")
            vecs.append([float(x) for x in v])
        if len(vecs) != len(texts):
            raise EmbeddingUnavailable(f"返回向量数 {len(vecs)} 与请求 {len(texts)} 不一致")
        return vecs

    def _evict_if_needed(self) -> None:
        """简单 LRU 截断：超过容量时清空一半（保留热数据策略简化版）。"""
        if len(self._cache) <= self._cache_size:
            return
        # 简单策略：截断到 50%（保留一半；足够避免 OOM 即可）
        target = self._cache_size // 2
        keys = list(self._cache.keys())
        for k in keys[: len(keys) - target]:
            self._cache.pop(k, None)
