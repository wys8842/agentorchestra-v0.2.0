"""压力测试 - 高并发场景"""

import asyncio
import time
import pytest
from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore
from agentorchestra.orchestration.state.checkpoint import Checkpoint


class TestConcurrency:
    """并发测试"""

    @pytest.mark.asyncio
    async def test_concurrent_lock_acquisition(self):
        """测试并发锁获取"""
        store = InMemoryCheckpointStore()
        await store.init()

        results = []

        async def acquire_lock(i):
            lock = await store.acquire_lock(f"resource-{i % 5}", f"tx-{i}", 30.0)
            return lock

        # 10个并发请求，5个不同资源
        tasks = [acquire_lock(i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        # 至少有部分成功
        successful = [r for r in results if r is not None]
        assert len(successful) > 0

    @pytest.mark.asyncio
    async def test_concurrent_checkpoint_save(self):
        """测试并发检查点保存"""
        store = InMemoryCheckpointStore()
        await store.init()

        async def save_checkpoint(i):
            cp = Checkpoint(
                thread_id=f"thread-{i % 10}",
                checkpoint_id=f"cp-{i}",
                state={"data": f"test-{i}"}
            )
            await store.save_checkpoint(cp)
            return True

        # 100个并发保存
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
            # 获取锁
            lock = await store.acquire_lock(resource, "tx-stress", 1.0)
            assert lock is not None

            # 释放锁
            await store.release_lock(resource, "tx-stress")

        # 最后获取一次
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

        # 1000次操作
        for i in range(1000):
            await store.create_thread(f"thread-{i}", {"index": i})

        elapsed = time.time() - start_time
        print(f"\n1000 thread creations took {elapsed:.2f}s")

        # 验证所有线程已创建
        threads = await store.list_threads()
        assert len(threads) >= 1000

    @pytest.mark.asyncio
    async def test_mixed_operations(self):
        """测试混合操作"""
        store = InMemoryCheckpointStore()
        await store.init()

        async def mixed_operations(i):
            # 创建线程
            await store.create_thread(f"thread-{i}", {"i": i})

            # 保存检查点
            cp = Checkpoint(
                thread_id=f"thread-{i}",
                checkpoint_id=f"cp-{i}",
                state={"index": i}
            )
            await store.save_checkpoint(cp)

            # 获取检查点
            loaded = await store.load_checkpoint(f"thread-{i}", f"cp-{i}")
            return loaded is not None

        # 并发执行
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
            cp = Checkpoint(
                thread_id=f"thread-{i % 50}",
                checkpoint_id=f"cp-{i}",
                state={"data": "x" * 1000}  # 1KB data
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

        from agentorchestra.orchestration.state.records import IdempotencyRecord

        num_records = 200

        async def create_record(i):
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
