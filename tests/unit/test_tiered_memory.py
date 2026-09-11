"""TieredMemory 三级缓存引擎单元测试

覆盖 docs/memory/tiered-memory-design.md 的测试计划。
全部使用内存适配器，零文件副作用。
"""

import threading

from agentorchestra.capability.memory.tiered_memory import (
    CacheEntry,
    InMemoryTierStorage,
    LRUCache,
    MemoryEntry,
    MemoryTier,
    TieredMemory,
)


class TestDefaults:
    """默认构造为三层。"""

    def test_default_is_three_tier(self):
        tm = TieredMemory()
        stats = tm.stats()
        assert stats["l2_enabled"] is True
        assert stats["l3_enabled"] is True

    def test_default_storage_instances(self):
        tm = TieredMemory()
        assert isinstance(tm._l2, InMemoryTierStorage)
        assert isinstance(tm._l3, InMemoryTierStorage)


class TestEvictionCascade:
    """L1 淘汰回写 L2，L2 淘汰下沉 L3。"""

    def test_l1_eviction_writes_back_to_l2(self):
        tm = TieredMemory(
            l1_capacity=2,
            l2_storage=InMemoryTierStorage(capacity=0),
            l3_storage=InMemoryTierStorage(capacity=0),
        )
        tm.put("a", 1)
        tm.put("b", 2)
        tm.put("c", 3)  # 淘汰 a → L2

        assert tm._l1.pop("a") is None  # a 不在 L1
        assert tm._l2.get("a") is not None  # a 在 L2（不再丢失）
        assert tm.get("a") == 1  # 仍可读回

    def test_l2_eviction_sinks_to_l3(self):
        tm = TieredMemory(
            l1_capacity=1,
            l2_storage=InMemoryTierStorage(capacity=1),
            l3_storage=InMemoryTierStorage(capacity=0),
        )
        tm.put("a", 1)
        tm.put("b", 2)  # a: L1 → L2
        tm.put("c", 3)  # b: L1 → L2；a: L2 → L3

        assert tm._l3.get("a") is not None
        assert tm.get("a") == 1  # 从 L3 读回

    def test_no_data_loss_under_pressure(self):
        tm = TieredMemory(
            l1_capacity=2,
            l2_storage=InMemoryTierStorage(capacity=2),
            l3_storage=InMemoryTierStorage(capacity=0),
        )
        for i in range(10):
            tm.put(f"k{i}", i)
        tm.flush()
        for i in range(10):
            assert tm.get(f"k{i}") == i


class TestReadPath:
    """逐级读取与命中上提。"""

    def test_l2_hit_promotes_to_l1(self):
        tm = TieredMemory(
            l1_capacity=2,
            l2_storage=InMemoryTierStorage(capacity=0),
            l3_storage=InMemoryTierStorage(capacity=0),
        )
        tm.put("a", 1)
        tm.put("b", 2)
        tm.put("c", 3)  # a → L2, L1={b,c}

        assert tm.get("a") == 1  # 命中 L2 并上提
        stats = tm.stats()
        assert stats["hits_l2"] == 1
        assert tm.get("a") == 1  # 现在命中 L1
        assert tm.stats()["hits_l1"] == 1

    def test_l3_hit_promotes_to_l1(self):
        tm = TieredMemory(
            l1_capacity=1,
            l2_storage=InMemoryTierStorage(capacity=1),
            l3_storage=InMemoryTierStorage(capacity=0),
        )
        tm.put("a", 1)
        tm.put("b", 2)
        tm.put("c", 3)  # a → L3

        assert tm.get("a") == 1
        assert tm.stats()["hits_l3"] == 1
        assert tm.get("a") == 1
        assert tm.stats()["hits_l1"] == 1

    def test_miss_returns_none(self):
        tm = TieredMemory()
        assert tm.get("nope") is None
        assert tm.stats()["misses"] == 1


class TestPromotion:
    """访问阈值与显式晋升。"""

    def test_access_threshold_promotes_to_l3(self):
        tm = TieredMemory(
            l1_capacity=8,
            promotion_threshold=2,
            l2_storage=InMemoryTierStorage(capacity=0),
            l3_storage=InMemoryTierStorage(capacity=0),
        )
        tm.put("x", 42)
        tm.get("x")  # count 1
        assert tm._l3.get("x") is None
        tm.get("x")  # count 2 → 沉淀 L3
        assert tm._l3.get("x") is not None
        assert tm.stats()["promotions"] == 1

    def test_promote_to_l3_manual(self):
        tm = TieredMemory()
        assert tm.promote_to_l3("missing") is False
        tm.put("y", 7)
        assert tm.promote_to_l3("y") is True
        assert tm._l3.get("y") is not None


class TestWritePolicies:
    """write_through 与 importance 旁路。"""

    def test_write_through_writes_l3_immediately(self):
        tm = TieredMemory(write_through=True)
        tm.put("w", 1)
        assert tm._l3.get("w") is not None

    def test_default_is_write_back(self):
        tm = TieredMemory()
        tm.put("w", 1)
        assert tm._l3.get("w") is None

    def test_high_importance_bypasses_to_l3(self):
        tm = TieredMemory(importance_l3_threshold=0.8)
        tm.put("hi", 1, importance=0.9)
        tm.put("lo", 2, importance=0.1)
        assert tm._l3.get("hi") is not None
        assert tm._l3.get("lo") is None


class TestFlushClearDelete:
    """flush / clear / delete 语义。"""

    def test_flush_drains_to_l3(self):
        tm = TieredMemory(
            l1_capacity=2,
            l2_storage=InMemoryTierStorage(capacity=2),
            l3_storage=InMemoryTierStorage(capacity=0),
        )
        for i in range(6):
            tm.put(f"f{i}", i)
        tm.flush()
        assert tm.stats()["l1_size"] == 0
        assert tm.stats()["l2_size"] == 0
        for i in range(6):
            assert tm._l3.get(f"f{i}") is not None

    def test_delete_removes_all_tiers(self):
        tm = TieredMemory(write_through=True)
        tm.put("d", 1)
        assert tm.delete("d") is True
        assert tm.get("d") is None
        assert tm.delete("d") is False

    def test_clear_empties_all_tiers(self):
        tm = TieredMemory(write_through=True)
        tm.put("a", 1)
        tm.put("b", 2)
        tm.clear()
        assert tm.stats()["l1_size"] == 0
        assert tm.stats()["l2_size"] == 0
        assert tm.stats()["l3_size"] == 0


class TestMetadata:
    """元数据跨层保留。"""

    def test_importance_preserved_across_sink(self):
        tm = TieredMemory(
            l1_capacity=1,
            l2_storage=InMemoryTierStorage(capacity=0),
            l3_storage=InMemoryTierStorage(capacity=0),
        )
        tm.put("a", 1, importance=0.5)
        tm.put("b", 2)  # a 下沉 L2
        sunk = tm._l2.get("a")
        assert sunk is not None
        assert sunk.importance == 0.5
        assert sunk.tier == MemoryTier.L2_SHORT

    def test_entry_roundtrip(self):
        entry = CacheEntry(key="k", value={"v": 1}, importance=0.7)
        entry.touch()
        restored = CacheEntry.from_dict(entry.to_dict())
        assert restored.key == "k"
        assert restored.value == {"v": 1}
        assert restored.importance == 0.7
        assert restored.access_count == 1


class TestCounters:
    """统计计数。"""

    def test_counters(self):
        tm = TieredMemory(
            l1_capacity=1,
            l2_storage=InMemoryTierStorage(capacity=0),
            l3_storage=InMemoryTierStorage(capacity=0),
        )
        tm.put("a", 1)
        tm.put("b", 2)  # 淘汰 a → writeback
        tm.get("missing")  # miss
        tm.get("a")  # L2 hit，上提 L1 时又淘汰 b

        stats = tm.stats()
        assert stats["evictions"] == 2
        assert stats["writebacks"] == 2
        assert stats["misses"] == 1
        assert stats["hits_l2"] == 1


class TestDegradation:
    """适配器异常优雅降级。"""

    class BrokenStorage:
        def get(self, key):
            raise RuntimeError("boom")

        def put(self, key, entry):
            raise RuntimeError("boom")

        def delete(self, key):
            raise RuntimeError("boom")

        def keys(self):
            raise RuntimeError("boom")

        def __contains__(self, key):
            raise RuntimeError("boom")

        def __len__(self):
            raise RuntimeError("boom")

    def test_put_does_not_raise(self):
        tm = TieredMemory(l2_storage=self.BrokenStorage(), l3_storage=self.BrokenStorage())
        tm.put("a", 1)  # L2 put 抛异常 → 降级
        assert tm.get("a") == 1  # L1 仍命中

    def test_get_returns_none_on_error(self):
        tm = TieredMemory(l2_storage=self.BrokenStorage(), l3_storage=self.BrokenStorage())
        assert tm.get("missing") is None
        assert tm.stats()["errors"] >= 2


class TestConcurrency:
    """并发 get/put 不崩且可 flush。"""

    def test_concurrent_access(self):
        tm = TieredMemory(
            l1_capacity=16,
            l2_storage=InMemoryTierStorage(capacity=32),
            l3_storage=InMemoryTierStorage(capacity=0),
        )
        errors = []

        def worker(base):
            try:
                for i in range(200):
                    key = f"k{(base + i) % 40}"
                    tm.put(key, i)
                    tm.get(key)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n * 10,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        tm.flush()
        assert tm.stats()["l1_size"] == 0
        assert tm.stats()["l2_size"] == 0


class TestBackwardCompat:
    """兼容旧命名与 API。"""

    def test_memory_entry_alias(self):
        assert MemoryEntry is CacheEntry

    def test_lru_cache_put_returns_evicted(self):
        cache = LRUCache(capacity=1)
        cache.put(CacheEntry(key="a", value=1))
        evicted = cache.put(CacheEntry(key="b", value=2))
        assert evicted is not None
        assert evicted.key == "a"

    def test_legacy_stats_keys(self):
        tm = TieredMemory()
        stats = tm.stats()
        for key in ("l1_size", "l1_capacity", "l2_enabled", "l3_enabled", "tracked_keys"):
            assert key in stats
