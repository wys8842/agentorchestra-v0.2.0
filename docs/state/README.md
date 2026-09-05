# State 模块

## 概述

State 模块提供持久化与恢复能力：Checkpoint/WAL/Snapshot/Interrupt。

## 核心功能

### Checkpoint

检查点存储：

```python
from agentorchestration.state import Checkpoint, CheckpointStore

store = InMemoryCheckpointStore()
await store.init()

# 保存
cp = Checkpoint(thread_id="t1", checkpoint_id="cp1", state={})
await store.save_checkpoint(cp)

# 加载
cp = await store.load_checkpoint("t1", "cp1")
```

### WAL

预写日志：

```python
from agentorchestration.state import WALEntry

# 追加
entry = WALEntry(thread_id="t1", action_type="action", payload={})
seq = await store.append_wal(entry)

# 读取
entries = await store.read_wal("t1", after_seq=0)
```

### Thread

线程管理：

```python
# 创建线程
await store.create_thread("thread-1", {"name": "task"})

# 列表
threads = await store.list_threads()

# 状态更新
await store.update_thread_status("thread-1", "running")
```

### Lock

分布式锁：

```python
# 获取锁
lock = await store.acquire_lock("resource-1", "tx-1", ttl=30)

# 释放
await store.release_lock("resource-1", "tx-1")

# CAS
ok = await store.compare_and_swap("resource-1", version=1, owner_tx="tx-1")
```

### Idempotency

幂等性：

```python
# 写入
await store.put_idempotency(record)

# 读取
record = await store.get_idempotency("key-1")
```

### DLQ

死信队列：

```python
# 入队
await store.enqueue_dlq(entry)

# 列表
entries = await store.list_dlq()
```

## 细粒度接口

```python
from agentorchestration.state.interfaces import (
    ThreadStore,
    CheckpointStore,
    WALStore,
    LockStore,
    IdempotencyStore,
    DLQStore,
    InboxStore,
    AuditStore
)
```
