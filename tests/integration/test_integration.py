"""集成测试"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import asyncio
from agentorchestra.state.backends.memory_backend import InMemoryCheckpointStore
from agentorchestra.orchestration.orch.scheduler import GraphScheduler


@pytest.mark.asyncio
class TestSchedulerIntegration:
    """调度器集成测试"""

    async def test_scheduler_with_store(self):
        """测试调度器创建"""
        store = InMemoryCheckpointStore()
        await store.init()
        scheduler = GraphScheduler(store=store)
        assert scheduler is not None


@pytest.mark.asyncio
class TestStateIntegration:
    """状态管理集成测试"""

    async def test_full_workflow(self):
        """测试完整工作流"""
        store = InMemoryCheckpointStore()
        await store.init()

        # 创建线程
        await store.create_thread("workflow-1", {"name": "test-workflow"})

        # 保存检查点
        from agentorchestra.orchestration.state.checkpoint import Checkpoint
        cp = Checkpoint(
            thread_id="workflow-1",
            checkpoint_id="cp-1",
            state={"step": 1, "data": "test"}
        )
        await store.save_checkpoint(cp)

        # 获取检查点
        loaded = await store.load_checkpoint("workflow-1", "cp-1")
        assert loaded is not None
        assert loaded.state["step"] == 1

    async def test_lock_workflow(self):
        """测试锁工作流"""
        store = InMemoryCheckpointStore()
        await store.init()

        # 获取锁
        lock1 = await store.acquire_lock("resource-1", "tx-1", 30.0)
        assert lock1 is not None

        # 验证锁已占用
        lock2 = await store.acquire_lock("resource-1", "tx-2", 30.0)
        assert lock2 is None

        # 释放锁
        released = await store.release_lock("resource-1", "tx-1")
        assert released is True

        # 再次获取锁
        lock3 = await store.acquire_lock("resource-1", "tx-2", 30.0)
        assert lock3 is not None
