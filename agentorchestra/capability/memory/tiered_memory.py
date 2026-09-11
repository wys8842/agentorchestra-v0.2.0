"""记忆分级缓存（三级缓存引擎）

三级缓存架构：
- L1: 工作记忆（in-process LRU，微秒级访问）
- L2: 短期记忆（StorageAdapter，默认有界内存实现）
- L3: 长期记忆（StorageAdapter，默认无界内存实现）

特性：
- 淘汰级联：L1 淘汰回写 L2，L2 容量淘汰下沉 L3（任何层级都不静默丢数据）
- 逐级读取 L1→L2→L3，命中上提 L1；访问计数达阈值沉淀 L3
- 每层统一存储 CacheEntry，元数据（importance/access_count/时间）跨层保留
- 线程安全（单个 RLock 包裹复合操作）
- flush() 强制级联落 L3

设计见 docs/memory/tiered-memory-design.md
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger("agentorchestra.memory.tiered")


class MemoryTier(Enum):
    """记忆层级"""
    L1_WORKING = "l1_working"   # 工作记忆（进程内）
    L2_SHORT = "l2_short"       # 短期记忆（有界二级存储）
    L3_LONG = "l3_long"         # 长期记忆（持久化 / 无界）


@dataclass
class CacheEntry:
    """缓存条目（三层统一存储单元）。

    Attributes:
        key: 键
        value: 值
        tier: 当前所在层
        created_at: 创建时间（time.time()）
        last_accessed: 最近访问时间
        access_count: 访问次数
        importance: 重要性 0~1
    """

    key: str
    value: Any
    tier: MemoryTier = MemoryTier.L1_WORKING
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    importance: float = 0.5

    def touch(self) -> None:
        """记录一次访问。"""
        self.last_accessed = time.time()
        self.access_count += 1

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（供持久化适配器使用）。"""
        return {
            "key": self.key,
            "value": self.value,
            "tier": self.tier.value if isinstance(self.tier, MemoryTier) else str(self.tier),
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "importance": self.importance,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CacheEntry":
        """从字典反序列化。"""
        tier_val = data.get("tier", MemoryTier.L1_WORKING.value)
        try:
            tier = MemoryTier(tier_val)
        except (ValueError, TypeError):
            tier = MemoryTier.L1_WORKING
        return cls(
            key=data.get("key", ""),
            value=data.get("value"),
            tier=tier,
            created_at=float(data.get("created_at", time.time())),
            last_accessed=float(data.get("last_accessed", time.time())),
            access_count=int(data.get("access_count", 0) or 0),
            importance=float(data.get("importance", 0.5) or 0.0),
        )


# 向后兼容别名：该文件历史版本使用 MemoryEntry（与 models.MemoryEntry 同名易混淆）
MemoryEntry = CacheEntry


@runtime_checkable
class StorageAdapter(Protocol):
    """L2/L3 存储适配器协议。

    put 返回被淘汰条目（有界实现）或 None（无界实现）。
    """

    def get(self, key: str) -> Optional[CacheEntry]:
        """按键读取条目，不存在返回 None。"""
        ...

    def put(self, key: str, entry: CacheEntry) -> Optional[CacheEntry]:
        """写入条目，返回被淘汰者（如有）。"""
        ...

    def delete(self, key: str) -> bool:
        """删除条目，返回是否存在。"""
        ...

    def keys(self) -> List[str]:
        """返回全部键。"""
        ...

    def __contains__(self, key: str) -> bool:
        """是否包含键。"""
        ...

    def __len__(self) -> int:
        """条目数。"""
        ...


class InMemoryTierStorage:
    """有界/无界内存存储适配器（LRU）。

    capacity <= 0 → 不限量；否则超出容量时按 LRU 驱逐并返回被逐条目。
    既作为 L2/L3 默认实现，也作为外部持久化适配器的参考实现。
    """

    def __init__(self, capacity: int = 0) -> None:
        self.capacity = capacity
        self._data: "OrderedDict[str, CacheEntry]" = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[CacheEntry]:
        """按键读取并标记最近使用。"""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            self._data.move_to_end(key)
            return entry

    def put(self, key: str, entry: CacheEntry) -> Optional[CacheEntry]:
        """写入；有界时超出容量则驱逐最久未用并返回之。"""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = entry
                return None
            self._data[key] = entry
            if self.capacity > 0 and len(self._data) > self.capacity:
                _, evicted = self._data.popitem(last=False)
                return evicted
        return None

    def delete(self, key: str) -> bool:
        """删除条目，返回是否存在。"""
        with self._lock:
            return self._data.pop(key, None) is not None

    def keys(self) -> List[str]:
        """返回全部键（副本）。"""
        with self._lock:
            return list(self._data.keys())

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class LRUCache:
    """LRU 缓存（L1；线程安全）。

    put 返回被驱逐条目（无则 None），行为与原实现一致。
    """

    def __init__(self, capacity: int = 256):
        self.capacity = capacity
        self._cache: "OrderedDict[str, CacheEntry]" = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[CacheEntry]:
        """获取条目（标记最近使用 + 记录访问）。"""
        with self._lock:
            if key not in self._cache:
                return None
            self._cache.move_to_end(key)
            entry = self._cache[key]
            entry.touch()
            return entry

    def put(self, entry: CacheEntry) -> Optional[CacheEntry]:
        """放入条目，返回被驱逐的条目。"""
        with self._lock:
            evicted: Optional[CacheEntry] = None
            if entry.key in self._cache:
                self._cache.pop(entry.key)
            elif self.capacity > 0 and len(self._cache) >= self.capacity:
                _, evicted = self._cache.popitem(last=False)
            self._cache[entry.key] = entry
            return evicted

    def pop(self, key: str) -> Optional[CacheEntry]:
        """移除并返回条目（不记录访问）。"""
        with self._lock:
            return self._cache.pop(key, None)

    def delete(self, key: str) -> bool:
        """删除条目，返回是否存在。"""
        with self._lock:
            return self._cache.pop(key, None) is not None

    def clear(self) -> None:
        """清空缓存。"""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """当前条目数。"""
        with self._lock:
            return len(self._cache)

    def keys(self) -> List[str]:
        """返回全部键（副本）。"""
        with self._lock:
            return list(self._cache.keys())


class TieredMemory:
    """三级分层记忆缓存引擎。

    读取：L1 → L2 → L3（命中即上提 L1）
    写入：L1；高 importance 或 write_through 时落 L3
    淘汰：L1 淘汰回写 L2，L2 容量淘汰下沉 L3
    """

    def __init__(
        self,
        l1_capacity: int = 256,
        l2_storage: Optional[StorageAdapter] = None,
        l3_storage: Optional[StorageAdapter] = None,
        promotion_threshold: int = 3,
        importance_l3_threshold: float = 0.8,
        write_through: bool = False,
    ):
        """初始化三级缓存。

        Args:
            l1_capacity: L1 容量（<=0 不限）。
            l2_storage: L2 适配器；None → InMemoryTierStorage(capacity=4096)。
            l3_storage: L3 适配器；None → InMemoryTierStorage(capacity=0 不限)。
            promotion_threshold: 访问次数达此阈值则沉淀 L3。
            importance_l3_threshold: importance 达此值则写入时旁路直写 L3。
            write_through: True 时每次 put 同时落 L3（写穿，立即持久）。
        """
        self._l1 = LRUCache(l1_capacity)
        self._l2: StorageAdapter = (
            l2_storage if l2_storage is not None
            else InMemoryTierStorage(capacity=4096)
        )
        self._l3: StorageAdapter = (
            l3_storage if l3_storage is not None
            else InMemoryTierStorage(capacity=0)
        )
        self._promotion_threshold = max(1, int(promotion_threshold))
        self.importance_l3_threshold = float(importance_l3_threshold)
        self.write_through = bool(write_through)
        self._access_counts: Dict[str, int] = {}
        self._lock = threading.RLock()
        self._counters: Dict[str, int] = {
            "hits_l1": 0,
            "hits_l2": 0,
            "hits_l3": 0,
            "misses": 0,
            "promotions": 0,
            "evictions": 0,
            "demotions": 0,
            "writebacks": 0,
            "errors": 0,
        }

    # ==================== 公开 API ====================

    def get(self, key: str) -> Optional[Any]:
        """按 key 读取（逐级查找，命中上提 L1）。"""
        with self._lock:
            entry = self._l1.get(key)
            if entry is not None:
                self._counters["hits_l1"] += 1
                self._record_access(entry)
                return entry.value

            entry = self._l2_get(key)
            if entry is not None:
                self._counters["hits_l2"] += 1
                entry.touch()
                self._insert_l1(entry)
                self._record_access(entry)
                return entry.value

            entry = self._l3_get(key)
            if entry is not None:
                self._counters["hits_l3"] += 1
                entry.touch()
                self._insert_l1(entry)
                self._record_access(entry)
                return entry.value

            self._counters["misses"] += 1
            return None

    def put(
        self,
        key: str,
        value: Any,
        importance: float = 0.5,
        tier: MemoryTier = MemoryTier.L1_WORKING,
    ) -> None:
        """写入记忆。

        Args:
            key: 键
            value: 值
            importance: 重要性 0~1
            tier: 初始层级标记（写入后由各级归一）
        """
        with self._lock:
            entry = CacheEntry(
                key=key,
                value=value,
                tier=tier,
                importance=float(importance),
            )
            self._insert_l1(entry)
            if self.write_through or entry.importance >= self.importance_l3_threshold:
                self._l3_put(self._clone(entry, MemoryTier.L3_LONG))

    def delete(self, key: str) -> bool:
        """从三层删除条目，返回任一层是否存在。"""
        with self._lock:
            existed = self._l1.delete(key)
            existed = self._l2_delete(key) or existed
            existed = self._l3_delete(key) or existed
            self._access_counts.pop(key, None)
            return existed

    def flush(self) -> None:
        """强制级联下沉：L1 → L2 → L3（移动语义）。"""
        with self._lock:
            for key in self._l1.keys():
                entry = self._l1.pop(key)
                if entry is not None:
                    self._sink(entry)
            for key in self._l2.keys():
                entry = self._l2_get(key)
                if entry is not None:
                    self._l3_put(self._clone(entry, MemoryTier.L3_LONG))
                    self._l2_delete(key)

    def clear(self) -> None:
        """清空三层与访问计数。"""
        with self._lock:
            self._l1.clear()
            for key in self._l2.keys():
                self._l2_delete(key)
            for key in self._l3.keys():
                self._l3_delete(key)
            self._access_counts.clear()

    def promote_to_l3(self, key: str) -> bool:
        """手动把 L1 中的条目晋升到 L3。"""
        with self._lock:
            entry = self._l1.get(key)
            if entry is None:
                return False
            self._l3_put(self._clone(entry, MemoryTier.L3_LONG))
            return True

    def stats(self) -> Dict[str, Any]:
        """返回各层规模与运行计数。"""
        with self._lock:
            result: Dict[str, Any] = {
                "l1_size": self._l1.size(),
                "l1_capacity": self._l1.capacity,
                "l2_size": self._l2_len(),
                "l3_size": self._l3_len(),
                "l2_enabled": self._l2 is not None,
                "l3_enabled": self._l3 is not None,
                "tracked_keys": len(self._access_counts),
            }
            result.update(self._counters)
            return result

    # ==================== 内部：迁移 ====================

    @staticmethod
    def _clone(entry: CacheEntry, tier: MemoryTier) -> CacheEntry:
        """浅拷贝条目并设置层级（避免跨层共享 tier 字段）。"""
        cloned = copy.copy(entry)
        cloned.tier = tier
        return cloned

    def _insert_l1(self, entry: CacheEntry) -> None:
        """写入 L1；若驱逐则回写 L2（淘汰级联）。"""
        evicted = self._l1.put(self._clone(entry, MemoryTier.L1_WORKING))
        if evicted is not None:
            self._counters["evictions"] += 1
            self._sink(evicted)

    def _sink(self, entry: CacheEntry) -> None:
        """L1 淘汰 → 回写 L2；L2 容量淘汰 → 下沉 L3。"""
        evicted = self._l2_put(self._clone(entry, MemoryTier.L2_SHORT))
        if evicted is None:
            self._counters["writebacks"] += 1
            return
        self._counters["demotions"] += 1
        self._l3_put(self._clone(evicted, MemoryTier.L3_LONG))

    def _record_access(self, entry: CacheEntry) -> None:
        """累计访问次数，达阈值沉淀 L3。"""
        key = entry.key
        count = self._access_counts.get(key, 0) + 1
        if count >= self._promotion_threshold:
            self._access_counts[key] = 0
            self._l3_put(self._clone(entry, MemoryTier.L3_LONG))
            self._counters["promotions"] += 1
        else:
            self._access_counts[key] = count

    # ==================== 内部：适配器包装（优雅降级） ====================

    def _l2_get(self, key: str) -> Optional[CacheEntry]:
        try:
            return self._l2.get(key) if self._l2 is not None else None
        except Exception as e:  # noqa: BLE001
            self._degrade("l2.get", e)
            return None

    def _l2_put(self, entry: CacheEntry) -> Optional[CacheEntry]:
        try:
            return self._l2.put(entry.key, entry) if self._l2 is not None else None
        except Exception as e:  # noqa: BLE001
            self._degrade("l2.put", e)
            return None

    def _l2_delete(self, key: str) -> bool:
        try:
            return bool(self._l2.delete(key)) if self._l2 is not None else False
        except Exception as e:  # noqa: BLE001
            self._degrade("l2.delete", e)
            return False

    def _l2_len(self) -> int:
        try:
            return len(self._l2) if self._l2 is not None else 0
        except Exception as e:  # noqa: BLE001
            self._degrade("l2.__len__", e)
            return 0

    def _l3_get(self, key: str) -> Optional[CacheEntry]:
        try:
            return self._l3.get(key) if self._l3 is not None else None
        except Exception as e:  # noqa: BLE001
            self._degrade("l3.get", e)
            return None

    def _l3_put(self, entry: CacheEntry) -> Optional[CacheEntry]:
        try:
            return self._l3.put(entry.key, entry) if self._l3 is not None else None
        except Exception as e:  # noqa: BLE001
            self._degrade("l3.put", e)
            return None

    def _l3_delete(self, key: str) -> bool:
        try:
            return bool(self._l3.delete(key)) if self._l3 is not None else False
        except Exception as e:  # noqa: BLE001
            self._degrade("l3.delete", e)
            return False

    def _l3_len(self) -> int:
        try:
            return len(self._l3) if self._l3 is not None else 0
        except Exception as e:  # noqa: BLE001
            self._degrade("l3.__len__", e)
            return 0

    def _degrade(self, op: str, error: Exception) -> None:
        """适配器故障时计数 + 记日志（不阻断调用方）。"""
        self._counters["errors"] += 1
        logger.warning("TieredMemory 适配器 %s 失败，已降级: %s", op, error)


__all__ = [
    "MemoryTier",
    "CacheEntry",
    "MemoryEntry",
    "StorageAdapter",
    "InMemoryTierStorage",
    "LRUCache",
    "TieredMemory",
]
