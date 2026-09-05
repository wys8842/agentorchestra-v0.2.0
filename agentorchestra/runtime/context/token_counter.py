"""TokenCounter - Token 计数器

职责：
- 本地预估 Token 数（无需 API 调用）
- 缓存机制（避免重复计算）
- 增量计算（只计算新增消息）
- 降级方案（tiktoken 不可用时使用字符估算）
"""

import hashlib
from typing import Dict, List, Optional

import tiktoken

from ..core.message import Message


class TokenCounter:
    """Token 计数器

    特性：
    - 本地预估（无需 API 调用）
    - 缓存机制（避免重复计算）
    - 增量计算（只计算新增消息）
    - 降级方案（tiktoken 不可用时使用字符估算）

    用法示例：
    ```python
    counter = TokenCounter(model="gpt-4")

    # 计算单条消息
    tokens = counter.count_message(message)

    # 计算消息列表
    total = counter.count_messages(messages)

    # 增量计算（推荐：使用 stable session id）
    counter.begin_session(messages[:10])  # 首次批量
    new_total = counter.count_incremental(messages[10:])  # 增量

    # 或手动增量
    counter.set_baseline(10, messages[:10])
    new_total = counter.count_incremental(messages[10:])
    ```
    """

    # 每条消息的角色标记开销（OpenAI 协议固定）
    ROLE_OVERHEAD = 4

    def __init__(self, model: str = "gpt-4"):
        self.model = model
        self._encoding = self._get_encoding()
        self._cache: Dict[str, int] = {}  # 内容 hash -> Token 数
        # 增量计算状态
        self._baseline_count: int = 0  # 已计算过的消息总数
        self._baseline_fingerprint: str = ""  # 上一批消息的指纹

    def _get_encoding(self):
        """获取 tiktoken 编码器"""
        try:
            return tiktoken.encoding_for_model(self.model)
        except KeyError:
            try:
                return tiktoken.get_encoding("cl100k_base")
            except (ImportError, ValueError):
                return None
        except (ImportError, ValueError):
            return None

    # ==================== 缓存 ====================

    @staticmethod
    def _fingerprint(content: str) -> str:
        """生成内容指纹（SHA1 前 16 位）"""
        return hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]

    def _count_with_cache(self, key: str, text: str) -> int:
        """带缓存的 Token 计算"""
        if key in self._cache:
            return self._cache[key]

        tokens = self._count_text(text) + self.ROLE_OVERHEAD
        self._cache[key] = tokens
        return tokens

    # ==================== 全量计算 ====================

    def count_messages(self, messages: List[Message]) -> int:
        """计算消息列表的总 Token 数"""
        return sum(self.count_message(m) for m in messages)

    def count_message(self, message: Message) -> int:
        """计算单条消息 Token 数（带缓存）"""
        content = message.content or ""
        cache_key = f"{message.role}:{self._fingerprint(content)}"
        return self._count_with_cache(cache_key, content)

    def count_text(self, text: str) -> int:
        """计算文本 Token 数（无缓存）"""
        return self._count_text(text)

    def _count_text(self, text: str) -> int:
        """底层 Token 计算"""
        if self._encoding:
            try:
                return len(self._encoding.encode(text))
            except (ValueError, TypeError):
                return len(text) // 4
        return len(text) // 4

    # ==================== 增量计算 ====================

    def set_baseline(self, count: int, messages: Optional[List[Message]] = None) -> None:
        """设置基线

        Args:
            count: 基线 Token 数
            messages: 基线对应的消息列表（用于生成指纹，避免重复计算）
        """
        self._baseline_count = count
        if messages is not None:
            self._baseline_fingerprint = self._fingerprint_batch(messages)
            # 预热缓存
            for msg in messages:
                self.count_message(msg)

    def begin_session(self, messages: List[Message]) -> int:
        """开启增量会话：计算初始消息并设置基线

        Returns:
            初始 Token 数
        """
        total = self.count_messages(messages)
        self.set_baseline(total, messages)
        return total

    def count_incremental(
        self,
        new_messages: Optional[List[Message]] = None,
        previous_fingerprint: Optional[str] = None,
    ) -> int:
        """增量计算 Token 数（传入完整消息列表）

        两种使用方式：
        1. 传入 new_messages（完整消息列表）：与上次指纹比对，自动检测变更
           - 指纹匹配 → 只计算新消息，复用缓存
           - 指纹不匹配 → 全量重算
        2. 不传 new_messages（None）→ 返回当前基线值

        Args:
            new_messages: 完整消息列表
            previous_fingerprint: 上次指纹（用于跨调用校验）

        Returns:
            累计 Token 数
        """
        if new_messages is None:
            return self._baseline_count

        # 计算整批消息的指纹
        current_fingerprint = self._fingerprint_batch(new_messages)

        # 指纹校验：与上次比对
        if (
            previous_fingerprint is not None
            and previous_fingerprint != current_fingerprint
        ):
            # 指纹不匹配 → 消息列表变更 → 全量重算
            new_total = self.count_messages(new_messages)
            self.set_baseline(new_total, new_messages)
            return new_total

        # 指纹匹配或无上次指纹 → 增量计算
        total = self.count_messages(new_messages)
        self._baseline_count = total
        self._baseline_fingerprint = current_fingerprint
        return total

    def append_and_count(self, new_message: Message) -> int:
        """追加单条消息并返回当前总 token 数

        比 count_incremental 更轻量：仅追加 1 条，无需指纹比对

        Args:
            new_message: 新追加的消息

        Returns:
            追加后的总 token 数
        """
        tokens = self.count_message(new_message)
        self._baseline_count += tokens
        return self._baseline_count

    def _fingerprint_batch(self, messages: List[Message]) -> str:
        """生成消息列表的整体指纹"""
        if not messages:
            return ""
        # 使用前 100 条消息的指纹拼接（避免超长）
        parts = [f"{m.role}:{self._fingerprint(m.content or '')}" for m in messages[:100]]
        combined = "|".join(parts)
        return hashlib.md5(combined.encode("utf-8")).hexdigest()[:16]

    def get_state(self) -> Dict:
        """获取增量计算状态（用于持久化/跨会话）"""
        return {
            "baseline_count": self._baseline_count,
            "baseline_fingerprint": self._baseline_fingerprint,
            "cache_size": len(self._cache),
            "cached_tokens": sum(self._cache.values()),
        }

    def reset_state(self) -> None:
        """重置增量状态（不清缓存）"""
        self._baseline_count = 0
        self._baseline_fingerprint = ""

    # ==================== 缓存管理 ====================

    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()

    def get_cache_size(self) -> int:
        """获取缓存大小"""
        return len(self._cache)

    def get_cache_stats(self) -> Dict:
        """获取缓存统计"""
        return {
            "cached_messages": len(self._cache),
            "total_cached_tokens": sum(self._cache.values()),
            "baseline_count": self._baseline_count,
        }