"""State 模块单元测试"""

import pytest
import asyncio
from agentorchestra.orchestration.state.checkpoint import Checkpoint
from agentorchestra.orchestration.state.records import (
    LockRecord,
    IdempotencyRecord,
    DLQEntry,
)
from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore
from datetime import datetime, timedelta


class TestCheckpoint:
    """检查点测试"""

    def test_checkpoint_creation(self):
        """测试检查点创建"""
        cp = Checkpoint(
            thread_id="test-thread",
            checkpoint_id="cp-1",
            state={"key": "value"}
        )
        assert cp.thread_id == "test-thread"
        assert cp.checkpoint_id == "cp-1"
        assert cp.state["key"] == "value"

    def test_checkpoint_to_dict(self):
        """测试检查点序列化"""
        cp = Checkpoint(
            thread_id="test-thread",
            checkpoint_id="cp-1",
            state={"data": "test"}
        )
        d = cp.to_dict()
        assert d["thread_id"] == "test-thread"
        assert d["state"]["data"] == "test"

    def test_checkpoint_from_dict(self):
        """测试检查点反序列化"""
        d = {
            "thread_id": "test",
            "checkpoint_id": "cp-1",
            "state": {"key": "value"},
            "created_at": datetime.now().isoformat()
        }
        cp = Checkpoint.from_dict(d)
        assert cp.thread_id == "test"
        assert cp.state["key"] == "value"


class TestLockRecord:
    """锁记录测试"""

    def test_lock_record_creation(self):
        """测试锁记录创建"""
        record = LockRecord(
            resource_key="resource-1",
            version=1,
            fencing_token=1,
            owner_tx="tx-1",
            held_since=datetime.now()
        )
        assert record.resource_key == "resource-1"
        assert record.version == 1
        assert record.fencing_token == 1


class TestIdempotencyRecord:
    """幂等记录测试"""

    def test_idempotency_record_creation(self):
        """测试幂等记录创建"""
        record = IdempotencyRecord(
            idempotency_key="key-1",
            request_hash="hash-1",
            tx_id="tx-1"
        )
        assert record.idempotency_key == "key-1"
        assert record.request_hash == "hash-1"


class TestDLQEntry:
    """死信队列条目测试"""

    def test_dlq_entry_creation(self):
        """测试 DLQ 条目创建"""
        entry = DLQEntry(
            tx_id="tx-1",
            action_name="action-1",
            error="Test error"
        )
        assert entry.tx_id == "tx-1"
        assert entry.action_name == "action-1"
        assert entry.status == "open"


@pytest.mark.asyncio
class TestInMemoryCheckpointStore:
    """内存检查点存储测试"""

    async def test_store_creation(self):
        """测试存储创建"""
        store = InMemoryCheckpointStore()
        await store.init()
        assert store is not None

    async def test_create_thread(self):
        """测试创建线程"""
        store = InMemoryCheckpointStore()
        await store.init()
        await store.create_thread("thread-1", {"name": "test"})
        thread = await store.get_thread("thread-1")
        assert thread is not None

    async def test_save_checkpoint(self):
        """测试保存检查点"""
        store = InMemoryCheckpointStore()
        await store.init()
        cp = Checkpoint(
            thread_id="thread-1",
            checkpoint_id="cp-1",
            state={"test": "data"}
        )
        await store.save_checkpoint(cp)
        loaded = await store.load_checkpoint("thread-1", "cp-1")
        assert loaded is not None
        assert loaded.state["test"] == "data"

    async def test_lock_operations(self):
        """测试锁操作"""
        store = InMemoryCheckpointStore()
        await store.init()

        # 获取锁
        lock = await store.acquire_lock(
            resource_key="resource-1",
            owner_tx="tx-1",
            ttl_seconds=30.0
        )
        assert lock is not None
        assert lock.owner_tx == "tx-1"

        # 尝试获取同一资源的锁（应该失败）
        lock2 = await store.acquire_lock(
            resource_key="resource-1",
            owner_tx="tx-2",
            ttl_seconds=30.0
        )
        assert lock2 is None

        # 释放锁
        released = await store.release_lock("resource-1", "tx-1")
        assert released is True

    async def test_idempotency_operations(self):
        """测试幂等操作"""
        store = InMemoryCheckpointStore()
        await store.init()

        record = IdempotencyRecord(
            idempotency_key="key-1",
            request_hash="hash-1",
            tx_id="tx-1"
        )
        await store.put_idempotency(record)
        loaded = await store.get_idempotency("key-1")
        assert loaded is not None
        assert loaded.idempotency_key == "key-1"
