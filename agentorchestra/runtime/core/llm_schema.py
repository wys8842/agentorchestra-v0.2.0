"""统一 LLM Schema + 错误分类 + 响应缓存"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any, Callable, Dict, List, Optional, Tuple


class LLMErrorType(Enum):
    """LLM 错误类型分类"""
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    AUTH = "auth"                       # 认证失败
    QUOTA_EXCEEDED = "quota_exceeded"
    INVALID_REQUEST = "invalid_request"   # 参数错误
    MODEL_UNAVAILABLE = "model_unavailable"
    CONTENT_FILTERED = "content_filtered"
    NETWORK = "network"
    UNKNOWN = "unknown"


@dataclass
class NormalizedToolCall:
    """统一工具调用格式"""
    id: str
    name: str
    arguments: Dict[str, Any]

    def to_openai(self) -> Dict[str, Any]:
        """转为 OpenAI 格式"""
        import json
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }

    def to_anthropic(self) -> Dict[str, Any]:
        """转为 Anthropic 格式"""
        return {
            "type": "tool_use",
            "id": self.id,
            "name": self.name,
            "input": self.arguments,
        }

    def to_gemini(self) -> Dict[str, Any]:
        """转为 Gemini 格式"""
        return {
            "function_call": {
                "name": self.name,
                "args": self.arguments,
            }
        }

    @classmethod
    def from_any(cls, raw: Dict[str, Any]) -> "NormalizedToolCall":
        """从任意 Provider 格式解析"""
        if "function" in raw:  # OpenAI
            import json
            fn = raw["function"]
            args = fn.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            return cls(id=raw["id"], name=fn["name"], arguments=args)
        elif raw.get("type") == "tool_use":  # Anthropic
            return cls(
                id=raw["id"],
                name=raw["name"],
                arguments=raw.get("input", {}),
            )
        elif "function_call" in raw:  # Gemini
            fc = raw["function_call"]
            return cls(
                id=raw.get("id", f"call_{int(time.time()*1000)}"),
                name=fc["name"],
                arguments=fc.get("args", {}),
            )
        raise ValueError(f"unknown tool call format: {raw}")


@dataclass
class NormalizedTool:
    """统一工具定义"""
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema

    def to_openai(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_gemini(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass
class NormalizedMessage:
    """统一消息格式"""
    role: str  # user / assistant / system / tool
    content: Optional[str] = None
    tool_calls: List[NormalizedToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None  # for tool role

    def to_openai(self) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"role": self.role}
        if self.content is not None:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = [tc.to_openai() for tc in self.tool_calls]
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        return msg

    def to_anthropic(self) -> Dict[str, Any]:
        if self.role == "tool":
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": self.tool_call_id,
                    "content": self.content or "",
                }],
            }
        msg: Dict[str, Any] = {"role": self.role}
        if self.content is not None:
            msg["content"] = self.content
        if self.tool_calls:
            msg["content"] = [
                {"type": "text", "text": self.content or ""},
                *[{"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                  for tc in self.tool_calls],
            ]
        return msg


class LLMCache:
    """LLM 响应缓存

    支持：
    - 基于 prompt 哈希的精确匹配
    - TTL 过期
    - LRU 驱逐
    """

    def __init__(self, max_size: int = 256, ttl_seconds: int = 300):
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._lock = RLock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _compute_key(messages: List, tools: Optional[List] = None,
                     model: str = "", **kwargs) -> str:
        """计算缓存 key"""
        import json
        content = json.dumps({
            "messages": [m.to_dict() if hasattr(m, "to_dict") else str(m) for m in messages],
            "tools": [t.to_dict() if hasattr(t, "to_dict") else str(t) for t in (tools or [])],
            "model": model,
            **kwargs,
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            value, ts = entry
            if time.time() - ts > self._ttl:
                del self._cache[key]
                self._misses += 1
                return None
            self._hits += 1
            # LRU: 移到最后
            self._cache.pop(key)
            self._cache[key] = entry
            return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.pop(key)
            elif len(self._cache) >= self._max_size:
                # LRU 驱逐
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (value, time.time())

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / total if total > 0 else 0,
            }


def classify_llm_error(error: Exception) -> LLMErrorType:
    """分类 LLM 错误类型

    Args:
        error: 原始异常

    Returns:
        错误类型
    """
    error_str = str(error).lower()
    error_type_name = type(error).__name__.lower()

    # Rate limit
    if any(s in error_str for s in ("rate_limit", "rate limit", "too many requests", "429")):
        return LLMErrorType.RATE_LIMIT

    # Timeout
    if any(s in error_str for s in ("timeout", "timed out")) or "timeout" in error_type_name:
        return LLMErrorType.TIMEOUT

    # Auth
    if any(s in error_str for s in ("401", "403", "unauthorized", "invalid api", "authentication")):
        return LLMErrorType.AUTH

    # Quota
    if any(s in error_str for s in ("quota", "insufficient", "billing", "credit")):
        return LLMErrorType.QUOTA_EXCEEDED

    # Invalid request
    if any(s in error_str for s in ("400", "invalid", "bad request", "malformed")):
        return LLMErrorType.INVALID_REQUEST

    # Model unavailable
    if any(s in error_str for s in ("model not found", "model unavailable", "404")):
        return LLMErrorType.MODEL_UNAVAILABLE

    # Content filter
    if any(s in error_str for s in ("content policy", "content_filter", "safety", "blocked")):
        return LLMErrorType.CONTENT_FILTERED

    # Network
    if any(s in error_str for s in ("connection", "network", "dns", "ssl")):
        return LLMErrorType.NETWORK

    return LLMErrorType.UNKNOWN


__all__ = [
    "LLMErrorType",
    "NormalizedToolCall",
    "NormalizedTool",
    "NormalizedMessage",
    "LLMCache",
    "classify_llm_error",
]