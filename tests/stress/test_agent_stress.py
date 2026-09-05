"""Agent 压力测试"""

import asyncio
import time
import pytest
from concurrent.futures import ThreadPoolExecutor
from agentorchestra.state.backends.memory_backend import InMemoryCheckpointStore


class TestConcurrency:
    """并发测试"""

    @pytest.mark.asyncio
    async def test_concurrent_lock_acquisition(self):
        """测试并发锁获取"""
        store = InMemoryCheckpointStore()
        await store.init()

        async def acquire_lock(i):
            lock = await store.acquire_lock(f"resource-{i % 5}", f"tx-{i}", 30.0)
            return lock

        # 10个并发请求
        tasks = [acquire_lock(i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        successful = [r for r in results if r is not None]
        assert len(successful) > 0

    @pytest.mark.asyncio
    async def test_concurrent_checkpoint_save(self):
        """测试并发检查点保存"""
        store = InMemoryCheckpointStore()
        await store.init()

        async def save_checkpoint(i):
            from agentorchestra.state.checkpoint import Checkpoint
            cp = Checkpoint(
                thread_id=f"thread-{i % 10}",
                checkpoint_id=f"cp-{i}",
                state={"data": f"test-{i}"}
            )
            await store.save_checkpoint(cp)
            return True

        tasks = [save_checkpoint(i) for i in range(100)]
        results = await asyncio.gather(*tasks)
        assert all(results)

    @pytest.mark.asyncio
    async def test_rapid_lock_release_acquire(self):
        """测试快速释放和获取锁"""
        store = InMemoryCheckpointStore()
        await store.init()

        resource = "test-resource"

        for _ in range(20):
            lock = await store.acquire_lock(resource, "tx-stress", 1.0)
            assert lock is not None
            await store.release_lock(resource, "tx-stress")

        final_lock = await store.acquire_lock(resource, "tx-final", 1.0)
        assert final_lock is not None


class TestHighLoad:
    """高负载测试"""

    @pytest.mark.asyncio
    async def test_high_volume_operations(self):
        """测试大量操作"""
        store = InMemoryCheckpointStore()
        await store.init()

        start_time = time.time()

        for i in range(1000):
            await store.create_thread(f"thread-{i}", {"index": i})

        elapsed = time.time() - start_time
        print(f"\n1000 thread creations took {elapsed:.2f}s")

        threads = await store.list_threads()
        assert len(threads) >= 1000

    @pytest.mark.asyncio
    async def test_mixed_operations(self):
        """测试混合操作"""
        store = InMemoryCheckpointStore()
        await store.init()

        async def mixed_operations(i):
            await store.create_thread(f"thread-{i}", {"i": i})
            from agentorchestra.state.checkpoint import Checkpoint
            cp = Checkpoint(
                thread_id=f"thread-{i}",
                checkpoint_id=f"cp-{i}",
                state={"index": i}
            )
            await store.save_checkpoint(cp)
            loaded = await store.load_checkpoint(f"thread-{i}", f"cp-{i}")
            return loaded is not None

        tasks = [mixed_operations(i) for i in range(50)]
        results = await asyncio.gather(*tasks)
        assert all(results)


class TestMemoryBackendStress:
    """内存后端压力测试"""

    @pytest.mark.asyncio
    async def test_large_checkpoints(self):
        """测试大量检查点"""
        store = InMemoryCheckpointStore()
        await store.init()

        num_checkpoints = 500

        async def create_checkpoint(i):
            from agentorchestra.state.checkpoint import Checkpoint
            cp = Checkpoint(
                thread_id=f"thread-{i % 50}",
                checkpoint_id=f"cp-{i}",
                state={"data": "x" * 1000}
            )
            await store.save_checkpoint(cp)

        start = time.time()
        tasks = [create_checkpoint(i) for i in range(num_checkpoints)]
        await asyncio.gather(*tasks)
        elapsed = time.time() - start

        print(f"\n{num_checkpoints} checkpoints in {elapsed:.2f}s")

    @pytest.mark.asyncio
    async def test_idempotency_stress(self):
        """测试幂等性压力"""
        store = InMemoryCheckpointStore()
        await store.init()

        num_records = 200

        async def create_record(i):
            from agentorchestra.state.records import IdempotencyRecord
            record = IdempotencyRecord(
                idempotency_key=f"key-{i % 50}",
                request_hash=f"hash-{i}",
                tx_id=f"tx-{i}"
            )
            await store.put_idempotency(record)

        start = time.time()
        tasks = [create_record(i) for i in range(num_records)]
        await asyncio.gather(*tasks)
        elapsed = time.time() - start

        print(f"\n{num_records} idempotency records in {elapsed:.2f}s")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
