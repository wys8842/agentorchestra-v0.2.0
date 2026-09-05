# TX 模块

## 概述

TX 模块提供事务运行时能力：协调器/幂等/补偿/DLQ/锁。

## 组件

### Coordinator

事务协调器：

```python
from agentorchestration.governance.tx import Coordinator

coordinator = Coordinator(store)

# 执行动作
result = await coordinator.execute(action, ctx)

# 补偿
await coordinator.compensate(tx_id)
```

### Compensation

补偿动作：

```python
from agentorchestration.governance.tx import Compensable

@Compensable(compensate_fn=my_compensate)
async def action():
    pass
```

### DLQ

死信队列：

```python
from agentorchestration.governance.tx import DLQManager

manager = DLQManager(store)
await manager.enqueue(tx_id, action_name, error)
entries = await manager.list()
```

### Lock

分布式锁：

```python
from agentorchestration.governance.tx import LockManager

manager = LockManager(store)
lock = await manager.acquire("resource", "tx-1", ttl=30)
await manager.release("resource", "tx-1")
```

### Idempotency

幂等性：

```python
from agentorchestration.governance.tx import IdempotencyManager

manager = IdempotencyManager(store)
await manager.put("key", "hash", "tx-1")
record = await manager.get("key")
```

## 事务模式

### Saga

```python
# 定义补偿动作
@Compensable(compensate_fn=compensate_fn)
async def step1():
    pass
```

### 2PC

两阶段提交：
1. Prepare
2. Commit/Rollback
