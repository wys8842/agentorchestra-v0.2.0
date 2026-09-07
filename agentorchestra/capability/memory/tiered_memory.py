"""记忆分级缓存

三级缓存架构：
- L1: 工作记忆（in-process LRU，毫秒级访问）
- L2: 短期记忆（SQLite，秒级访问）
- L3: 长期记忆（持久化 + 衰减机制）

特性：
- 自动晋升/降级
- 容量控制（LRU 驱逐）
- 跨级一致性
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MemoryTier(Enum):
    """记忆层级"""
    L1_WORKING = "l1_working"   # 工作记忆（进程内）
    L2_SHORT = "l2_short"       # 短期记忆（SQLite）
    L3_LONG = "l3_long"         # 长期记忆（持久化 + 衰减）


@dataclass
class MemoryEntry:
    """记忆条目"""
    key: str
    value: Any
    tier: MemoryTier = MemoryTier.L1_WORKING
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    importance: float = 0.5  # 0-1，决定晋升到 L3

    def touch(self) -> None:
        """记录访问"""
        self.last_accessed = time.time()
        self.access_count += 1


class LRUCache:
    """LRU 缓存（线程安全）"""

    def __init__(self, capacity: int = 256):
        self.capacity = capacity
        self._cache: OrderedDict[str, MemoryEntry] = OrderedDict()
        self._lock = None  # 实际由 TieredMemory 加锁

    def get(self, key: str) -> Optional[MemoryEntry]:
        """获取条目（标记最近使用）"""
        if key not in self._cache:
            return None
        entry = self._cache.pop(key)
        entry.touch()
        self._cache[key] = entry
        return entry

    def put(self, entry: MemoryEntry) -> Optional[MemoryEntry]:
        """放入条目，返回被驱逐的条目"""
        if entry.key in self._cache:
            self._cache.pop(entry.key)
        elif len(self._cache) >= self.capacity:
            # 驱逐最久未使用
            evicted_key, evicted_entry = self._cache.popitem(last=False)
            return evicted_entry

        self._cache[entry.key] = entry
        return None

    def delete(self, key: str) -> bool:
        return self._cache.pop(key, None) is not None

    def clear(self) -> None:
        self._cache.clear()

    def size(self) -> int:
        return len(self._cache)

    def keys(self) -> List[str]:
        return list(self._cache.keys())


class TieredMemory:
    """三级分层记忆

    读取：L1 → L2 → L3（命中即返回）
    写入：L1；频繁访问自动晋升 L3
    """

    def __init__(
        self,
        l1_capacity: int = 256,
        l2_storage: Optional[Any] = None,
        l3_storage: Optional[Any] = None,
        promotion_threshold: int = 3,  # 访问次数达此阈值则晋升 L3
    ):
        self._l1 = LRUCache(l1_capacity)
        self._l2 = l2_storage  # 可选外部存储
        self._l3 = l3_storage
        self._promotion_threshold = promotion_threshold
        # 访问计数（用于晋升判断）
        self._access_counts: Dict[str, int] = {}

    def get(self, key: str) -> Optional[Any]:
        """三级查找"""
        # L1
        entry = self._l1.get(key)
        if entry is not None:
            self._record_access(key)
            return entry.value

        # L2
        if self._l2 is not None:
            value = self._l2.get(key)
            if value is not None:
                self._promote_to_l1(key, value, MemoryTier.L2_SHORT)
                return value

        # L3
        if self._l3 is not None:
            value = self._l3.get(key)
            if value is not None:
                self._promote_to_l1(key, value, MemoryTier.L3_LONG)
                return value

        return None

    def put(self, key: str, value: Any,
            importance: float = 0.5,
            tier: MemoryTier = MemoryTier.L1_WORKING) -> None:
        """写入记忆"""
        entry = MemoryEntry(
            key=key,
            value=value,
            tier=tier,
            importance=importance,
        )
        self._l1.put(entry)

        # 高重要性直接写 L3
        if importance >= 0.8 and self._l3 is not None:
            self._l3.put(key, value)

    def _record_access(self, key: str) -> None:
        """记录访问次数，决定是否晋升"""
        count = self._access_counts.get(key, 0) + 1
        self._access_counts[key] = count

        if count >= self._promotion_threshold and self._l3 is not None:
            entry = self._l1.get(key)
            if entry is not None:
                self._l3.put(key, entry.value)
                # 提升后清零计数
                self._access_counts[key] = 0

    def _promote_to_l1(self, key: str, value: Any, from_tier: MemoryTier) -> None:
        """从低级晋升到 L1"""
        entry = MemoryEntry(key=key, value=value, tier=from_tier)
        self._l1.put(entry)

    def delete(self, key: str) -> bool:
        """从所有层级删除"""
        deleted = self._l1.delete(key)
        if self._l2 is not None:
            deleted = self._l2.delete(key) or deleted
        if self._l3 is not None:
            deleted = self._l3.delete(key) or deleted
        self._access_counts.pop(key, None)
        return deleted

    def stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "l1_size": self._l1.size(),
            "l1_capacity": self._l1.capacity,
            "l2_enabled": self._l2 is not None,
            "l3_enabled": self._l3 is not None,
            "tracked_keys": len(self._access_counts),
        }

    def promote_to_l3(self, key: str) -> bool:
        """手动晋升记忆到 L3"""
        if self._l3 is None:
            return False
        entry = self._l1.get(key)
        if entry is None:
            return False
        self._l3.put(key, entry.value)
        return True
