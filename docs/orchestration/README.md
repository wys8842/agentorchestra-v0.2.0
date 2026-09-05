# Orchestration 模块

## 概述

Orchestration 模块提供 Agent 图/DAG 通信能力：Graph/Scheduler/Inbox/状态管理。

## 组件

### GraphScheduler

图调度器：

```python
from agentorchestra.orchestration.orch import GraphScheduler, Graph, Node

scheduler = GraphScheduler(max_iterations=3)

# 执行图
result = await scheduler.execute(
    graph=graph,
    initial_message={"task": "任务"},
    thread_id="thread-1"
)
```

### Graph

DAG 图：

```python
from agentorchestra.orch.orch import Graph, Node

graph = Graph()
graph.add_node(Node(id="node1", run=func))
graph.add_edge("node1", "node2")
```

### Inbox

消息收件箱：

```python
from agentorchestra.orch.orch import Inbox

inbox = Inbox(store)
await inbox.send(graph_id, thread_id, "node", content)
messages = await inbox.poll(thread_id)
```

## State 状态管理

### Checkpoint

检查点：

```python
from agentorchestra.orchestration.state import Checkpoint

cp = Checkpoint(
    thread_id="thread-1",
    checkpoint_id="cp-1",
    state={"key": "value"}
)
await store.save_checkpoint(cp)
```

### WAL

预写日志：

```python
from agentorchestra.orchestration.state import WALEntry

entry = WALEntry(
    thread_id="thread-1",
    action_type="action",
    payload={"data": "x"}
)
seq = await store.append_wal(entry)
```

### Snapshot

快照：

```python
from agentorchestra.orchestration.state import Snapshot

snap = Snapshot(thread_id="t1", snapshot_id="s1", state={})
await store.save_snapshot(snap)
```

### Interrupt

中断管理：

```python
from agentorchestra.orchestration.state import Interrupt

intr = Interrupt(token="token", thread_id="t1", reason="等待确认")
await store.create_interrupt(intr)
```

## Backends

### 存储后端

| 后端 | 说明 |
|------|------|
| InMemoryCheckpointStore | 内存存储 |
| SQLiteCheckpointStore | SQLite 持久化 |
| PostgresCheckpointStore | PostgreSQL 持久化 |
