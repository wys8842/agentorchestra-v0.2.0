# TX（事务运行时）

> agentorchestra 的事务引擎（`governance/tx/`）：幂等 + WAL + 逆序补偿 + DLQ + 乐观锁的 async 事务运行时，构建在 [state](../state/README.md) 的 `CheckpointStore` 之上。

## 设计动机与原则

- **幂等是重试的前提**：同一次业务请求（同一 `idempotency_key`）重复提交时直接返回首次结果（`TxReplay`），而不是重复执行副作用；未显式给键时用“动作签名 + `request_payload` + resources”自动生成哈希键，避免不同参数因同名动作互相去重（coordinator.py 的生成逻辑与注释）。注意 v0.2.0 的自动生成分支有缺陷（见“使用说明”注意事项），**建议总是显式传 `idempotency_key`**。
- **“先写意图、后执行、可回滚”**：所有动作按正序执行并记录到 `TxContext.completed`，任何失败即对已完成动作**逆序补偿**（Saga 语义），成功退出提交、异常退出回滚，事务状态机收敛到 `committed / aborted / compensation_failed`。
- **WAL 复用 state 持久层**：`TxActionLog` 直接复用 state 的 `WALEntry`/`WALActionType`（同一张 wal 表，`tx_id` 关联），事务首尾写 `TX_BEGIN` / `TX_COMMIT`，动作执行写 `STATE_UPDATE`；只对非内存后端写 begin/commit 标记，避免内存模式无谓 IO。
- **补偿不无限重试**：每个补偿动作最多 `max_attempts`（默认 3）次、带退避；耗尽后进入 DLQ（`status=open`），等人工 `resolve()`，而不是在运行中死循环。
- **乐观锁代替悲观锁，锁即版本**：锁记录（locks 表）内含单调 `version` 与 `fencing_token`，`compare_and_swap` 是“版本匹配才 +1”的 CAS，可携带 `expected_fencing_token` 防止 TTL 过期后的僵尸事务误写。
- **身份/权限/租户随事务流动**：`transaction()` 进入时把 `principal/roles` 注入 identity ContextVar，退出还原；`TxContext.authorize()` 两段式授权；`OptimisticLock` 自动感知租户 namespace，跨租户键自动前缀隔离。
- **指标零侵入**：提交/回滚/补偿触发均向 `observability.metrics` 默认收集器发送 SLO 指标（如 `SLO_TX_DURATION_SECONDS`），收集器未配置（NoOp）时静默跳过。
- **SSI 留接口不实现**：隔离级别（Serializable Snapshot Isolation）在 M1 明确排除（YAGNI），`isolation.py` 只保留抽象占位，供后续并发模型定型时扩展。

## 设计优势

- 调用方只用 `async with coordinator.transaction(...)` + `tx.execute(...)` 即可获得幂等/补偿/锁/DLQ 全套行为，业务代码零事务知识。
- 幂等键显式/隐式双轨：外部幂等键（如客户端请求号）与自动语义键并行，且自动键把 `request_payload` 纳入哈希，设计上规避了“同名动作不同参数被错误去重”的经典陷阱（该自动键分支 v0.2.0 存在缺陷，建议显式传键，见“使用说明”注意事项）。
- 锁/幂等/DLQ/WAL 全部落在同一套 `CheckpointStore` 表上，不引入独立中间件，单 store 即可支撑完整运行时；内存实现让测试无需数据库。
- 补偿结果结构化返回（`compensated / failed / dlq`），状态与可观测信息（`TxStatus`、SLO 指标）齐备，便于排障与人工介入。
- 与租户/身份/权限子包以“上下文”方式解耦协作，治理能力可按需启用而不侵入 tx 核心路径。

## 模块构成

| 路径（相对 `agentorchestra/governance/tx/`） | 职责 | 主要公开导出（真实，见 `__init__.py`） |
|---|---|---|
| `context.py` | 事务状态/异常/动作/上下文定义 | `TxStatus`、`TxAbort`、`TxConflict`、`TxReplay`、`TxAction`、`TxContext` |
| `coordinator.py` | async 事务协调器（主入口） | `TransactionCoordinator` |
| `compensation.py` | 逆序补偿编排 + 重试 + DLQ | `CompensationExecutor` |
| `idempotency.py` | 幂等键存储包装 | `IdempotencyStore` |
| `lock.py` | 乐观锁（自动租户 namespace） | `OptimisticLock` |
| `dlq.py` | 死信队列包装 | `DeadLetterQueue` |
| `wal.py` | 事务动作日志（state.wal 薄包装） | `TxActionLog` |
| `sync.py` | 同步桥接 | `run_sync` |
| `isolation.py` | 快照隔离抽象占位（未在 `__all__` 中导出） | `IsolationSnapshot`（深度导入） |

所有类都以 `store: CheckpointStore` 为底座，直接消费 state 提供的表：`locks`、`idempotency_keys`、`dead_letter`、`wal`。

## 功能清单

### TransactionCoordinator —— 协调器

- **是什么 / 解决什么**：事务的唯一入口，负责：幂等查重与登记 → 资源锁获取 → 身份注入 → 动作执行 → 提交/回滚调度 → 锁释放 → 指标上报。
- **构造参数**：`store=None`（缺省自动建 `InMemoryCheckpointStore`，不落盘；SQL store 需调用方先 `init()`）、`compensation_retries=3`、`compensation_backoff=0.1`、`idempotency_ttl=86400`（秒）、`lock_ttl=30.0`、`thread_id="default"`、`permission_checker=None`。
- **动作注册**：`register(action: TxAction)`；`register_action(name, execute_fn, compensate_fn=None, idempotent=True) -> TxAction`；`get_action(name)`；`list_actions() -> list[str]`。
- **事务主流程**：`async transaction(idempotency_key=None, resources=None, timeout=30.0, principal=None, roles=None, permission_checker=None, request_payload=None) -> AsyncIterator[TxContext]`：
  - 无显式幂等键时自动生成（**v0.2.0 建议显式传键**，见注意事项）；命中已 `completed` 记录 → 抛 `TxReplay(result=首次结果)`；
  - `resources` 逐个 `lock.acquire`，任一失败先释放已持有的锁并 `mark_failed`，随后抛 `TxConflict`；
  - 块内 `asyncio.timeout(timeout)` 包住业务代码；正常退出 → `_commit`（`complete` 幂等记录 + `TX_COMMIT` + 释放锁）；异常/超时/`TxAbort` → `_compensate_and_fail`（补偿 + `mark_failed` + 释放锁 + SLO 指标，`TxConflict` 原样上抛不再补偿）。
- **行为边界**：`store is None` 时自动内存 store，且跳过 TX_BEGIN/TX_COMMIT 的 WAL 写入（内存无审计意义）；对非内存 store 才写事务标记。注意内存模式仍会把每次动作的 `STATE_UPDATE` 写入 WAL（`ctx.execute` 中对 `store is not None` 统一写）。补偿失败（含无补偿函数）→ 事务状态 `COMPENSATION_FAILED`。

### TxAction / TxContext / 状态与异常

- `TxAction(name, execute_fn, compensate_fn=None, idempotent=True)`：`execute_fn(params, tx_ctx) -> result`；`compensate_fn is None` 表示该动作**不可补偿**（回滚时会记入 failed）。
- `TxContext`：字段含 `tx_id`、`status`、`completed`（正序成功动作名）、`completed_params`、`resources`、`result`、`failure`、`principal/roles/permission_checker`、`started`（monotonic 起始时间）。方法：
  - `authorize(resource, permission, obj_id=None)`：装配了 checker 则强制 RBAC+ACL；未装配静默放行。
  - `pre_condition(resource_key, expected_version=None, owner_tx=None) -> bool`：事务前条件。给出 `expected_version` 时与当前版本 CAS 比对（失败返回 `False` 不抛异常）；仅声明资源时尝试取锁。返回 `False` 时业务方自行决定（通常 `raise TxAbort`）。
  - `execute(action_name, params) -> dict`：执行已注册动作；成功记录进 `completed` 并写 `STATE_UPDATE` WAL；失败记录 `failure` 后上抛（由 coordinator 触发补偿）。
- 状态：`TxStatus` 为 `running / committed / aborted / compensation_failed`。
- 异常：
  - `TxAbort`：用户主动中止（pre-condition 失败/规则拒绝），触发逆序补偿，最终 `aborted`。
  - `TxConflict`：乐观锁/资源锁冲突；**由调用方决定重试**，不会自动无限重试。
  - `TxReplay(result=...)`：幂等命中，携带首次返回结果。

### CompensationExecutor —— 逆序补偿

- `compensate(ctx, completed, completed_params) -> {"compensated": [...], "failed": [...], "dlq": [...]}`。
- 对 `completed` **逆序**逐动作补偿；单个动作失败退避重试（`backoff * attempt` 递增）至 `max_attempts`，仍失败则 `dlq.enqueue` 并入 `dlq`/`failed`。
- 边界：动作未注册或 `compensate_fn is None` 直接记为 failed（不重试）；补偿函数可为同步或 async（`asyncio.iscoroutine` 判定后 await）。

### IdempotencyStore —— 幂等存储包装

- 基于 store `idempotency_keys` 表；`ttl_seconds` 默认 86400。
- `generate_key(*parts) -> str`：sha256 签名（JSON 稳定序列化，`\x00` 分隔）。`request_hash(key, steps)`。
- `begin(key, request_hash, tx_id) -> bool`：写入 `running` 记录；已 `completed` 返回 `False`（命中重放）。
- `complete(key, result, tx_id)`（写 `completed` + 首次结果）、`mark_failed(key, tx_id)`（失败后可重试）、`get(key) -> Optional[IdempotencyRecord]`、`cleanup() -> int`（清过期）。
- 边界：记录的 `expires_at` 由 begin 写入；`get` 对过期记录返回 `None`（惰性失效），需 `cleanup()` 物理清理。

### OptimisticLock —— 乐观锁

- 基于 store `locks` 表；`ttl_seconds` 默认 30。
- `acquire(resource_key, owner_tx) -> Optional[LockRecord]`：成功返回记录（含 `version` 与 `fencing_token`，可用于下游校验）；冲突/被他人持有返回 `None`；**同 key 已由本实例持有时直接返回持有记录**（可重入）。
- `read_version(resource_key)`、`read_fencing_token(resource_key)`、`compare_and_swap(resource_key, expected_version, owner_tx, expected_fencing_token=None) -> bool`、`release_all(owner_tx) -> list[str]`、`clear()`。
- 租户联动：所有键先经 `namespace_resource()` 拼当前租户前缀；无租户上下文保持原样（向后兼容）。
- 边界：锁记录过期后新 acquire 可抢占，**version 使用独立单调计数器（不归零）**，因此旧事务的旧 version CAS 必然失败——这是防僵尸写的基础；`release_lock` 仅 owner 可释放（store 层约束）。

### DeadLetterQueue —— 死信队列

- 基于 store `dead_letter` 表；`DLQEntry` 自带自增 `id`（内存后端自增 / SQL 后端 DB autoincrement）。
- `enqueue(tx_id, action_name, error, attempts, extra=None)`；`list(limit=100, status="open") -> list[DLQEntry]`；`count(status="open")`。
- 人工介入：`resolve(dlq_id, note=None) -> bool` 标记 `resolved`（store 无 `resolve_dlq` 时返回 `False` 并告警）；`replay(dlq_id, coordinator, note=None) -> bool` 重新触发该动作的补偿（依赖 action 仍有补偿函数；属 best-effort，失败返回 `False`）。

### TxActionLog —— 事务 WAL 日志

- `log_begin(tx_id, meta)`（`TX_BEGIN`）、`log_action(tx_id, action_name, params, result)`（`STATE_UPDATE`，payload 含 `tx_id/op/action/params/result`）、`log_commit(tx_id, status)`（`TX_COMMIT`）。
- 复用 state `WALEntry`；`thread_id` 构造时给定（默认 `"default"`）。

### run_sync —— 同步桥接

- `run_sync(coro_factory)`：无运行中事件循环 → `asyncio.run(...)`；已在循环内 → 抛 `RuntimeError`，提示改用 `async with coordinator.transaction(...)`。
- 面向旧同步调用方（如 `ontology/process/transaction.py` 的 `TransactionManager.execute` 委托路径）。

## 使用说明

导入路径：经典 `from agentorchestra.tx import ...`；规范 `from agentorchestra.governance.tx import ...`。二者解析到同一模块对象。以下示例默认使用内存 store（`TransactionCoordinator()` 自动创建），零外部依赖、可直接 `asyncio.run`。

### 场景一：提交、幂等重放、失败逆序补偿

```python
import asyncio
from agentorchestra.tx import TransactionCoordinator, TxReplay, TxAbort, TxStatus

executed: list[str] = []
compensated: list[str] = []

def reserve(params, tx):
    executed.append(f"reserve:{params['sku']}")

def release(params, tx):
    compensated.append(f"release:{params['sku']}")

async def main():
    coordinator = TransactionCoordinator()          # store=None → InMemoryCheckpointStore

    coordinator.register_action("reserve", execute_fn=reserve, compensate_fn=release)
    coordinator.register_action("charge", execute_fn=lambda p, tx: (_ for _ in ()).throw(RuntimeError("扣款失败")),
                                compensate_fn=None)

    # ① 成功提交（显式幂等键）
    async with coordinator.transaction(idempotency_key="k-o1") as tx:
        await tx.execute("reserve", {"sku": "A1"})
    assert tx.status == TxStatus.COMMITTED
    print("committed:", tx.result)

    # ② 同一幂等键再次提交 → 幂等命中，直接返回首次结果
    try:
        async with coordinator.transaction(idempotency_key="k-o1") as tx:
            await tx.execute("reserve", {"sku": "A1"})
    except TxReplay as e:
        print("replay result:", e.result)           # {"action": "reserve", "result": ...}

    # ③ reserve 成功后 charge 失败 → 逆序补偿 reserve
    try:
        async with coordinator.transaction(idempotency_key="k-o2") as tx:
            await tx.execute("reserve", {"sku": "A1"})
            await tx.execute("charge", {"amount": 100})
    except RuntimeError:
        pass
    assert "release:A1" in compensated
    assert tx.status == TxStatus.ABORTED
    print("compensated:", compensated)

asyncio.run(main())
```

### 场景二：乐观锁与 CAS

```python
import asyncio
from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore
from agentorchestra.tx import OptimisticLock, TxConflict

async def main():
    store = InMemoryCheckpointStore()
    await store.init()

    lock_a = OptimisticLock(store, ttl_seconds=30.0)
    rec = await lock_a.acquire("order:o1", "tx-1")      # rec.version=V1（注意 rec 与 store 中为同一对象）
    assert rec is not None
    expected = rec.version                             # 先把期望版本拷出来

    lock_b = OptimisticLock(store)
    assert await lock_b.acquire("order:o1", "tx-2") is None   # 他人持有 → 冲突

    # 版本匹配才递增：成功后版本变为 expected+1
    assert await lock_a.compare_and_swap("order:o1", expected, "tx-1") is True
    assert await lock_a.compare_and_swap("order:o1", expected, "tx-1") is False  # 期望版本已过期

    released = await lock_a.release_all("tx-1")         # 释放全部
    assert released == ["order:o1"]

asyncio.run(main())
```

### 场景三：资源锁冲突 → TxConflict

```python
import asyncio
from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore
from agentorchestra.tx import TransactionCoordinator, TxConflict

async def main():
    store_shared = InMemoryCheckpointStore()
    await store_shared.init()

    c1 = TransactionCoordinator(store=store_shared)
    c2 = TransactionCoordinator(store=store_shared)
    c1.register_action("reserve", execute_fn=lambda p, tx: {"ok": True})   # 让 c1 能正常执行

    async with c1.transaction(idempotency_key="c1-o9", resources=["order:o9"]) as tx:
        try:
            async with c2.transaction(idempotency_key="c2-o9", resources=["order:o9"]):
                pass  # 不会到达：order:o9 已被 c1 持有
        except TxConflict as e:
            print("conflict:", e)
        await tx.execute("reserve", {"sku": "A1"})
    # c1 正常退出 → 提交并释放 order:o9

asyncio.run(main())
```

### 场景四：补偿耗尽 → DLQ，人工 resolve

```python
import asyncio
from agentorchestra.tx import TransactionCoordinator, TxStatus

async def main():
    coordinator = TransactionCoordinator(compensation_retries=1, compensation_backoff=0.01)

    def bad_execute(params, tx):
        return {"ok": True}

    def failing_compensate(params, tx):
        raise RuntimeError("补偿对端不可用")

    coordinator.register_action("reserve", execute_fn=bad_execute, compensate_fn=failing_compensate)

    try:
        async with coordinator.transaction(idempotency_key="dlq-k1") as tx:
            await tx.execute("reserve", {"sku": "A1"})
            raise RuntimeError("触发回滚")
    except RuntimeError:
        pass

    assert tx.status == TxStatus.COMPENSATION_FAILED
    entries = await coordinator.dlq.list()          # status="open"
    print("DLQ:", [(e.id, e.action_name, e.error) for e in entries])
    if entries:
        await coordinator.dlq.resolve(entries[0].id, note="已人工处理")   # 标记 resolved

asyncio.run(main())
```

注意事项：

- `transaction()` 内**必须**用 `tx.execute()` 走注册动作；不要直接改 `ctx.completed`。
- **请显式传 `idempotency_key`**：自动生成幂等键的分支（`coordinator.transaction` 内签名拼接处）在 v0.2.0 引用了一个未导入的 `json` 名字（该分支写了 `import json as _json` 却调用全局 `json.dumps`），会抛 `NameError`；显式传键可绕过，等待仓库修复。
- 同一幂等键两次成功提交会被判重（抛 `TxReplay`）；需要“相同内容但应再次执行”时，换一个新键。
- `TxConflict` 不会被自动补偿/自动重试：协调器只负责把冲突抛给调用方。
- SQL/SQLite store 需先 `await store.init()`；补偿期间如 `store` 未初始化，WAL/DLQ 写入会失败。
- 事务内抛出的非 `TxAbort/TxConflict/TimeoutError` 业务异常会被补偿后**原样重抛**，调用方需捕获处理。
- `pre_condition` 的 CAS 需要目标 key 已存在锁记录（通常配合 `resources` 预取锁使用）；否则请直接使用 `OptimisticLock` 显式管理。

## 与其他模块的关系

真实依赖（源码 import 方向）：

- 向下依赖 state：`coordinator.py` → `orchestration/state/backends/memory_backend.InMemoryCheckpointStore`（缺省 store）；`idempotency.py`/`dlq.py`/`lock.py` → `orchestration/state/records`（`IdempotencyRecord`/`DLQEntry`/`LockRecord`）；`wal.py` → `orchestration/state/wal`（`WALEntry`/`WALActionType`）；全部 store 型接口经 `state.checkpoint.CheckpointStore` 提供（详见 [state](../state/README.md)）。
- 与十亿级同域协作：`lock.py` → `governance/tenancy/tenant.namespace_resource`（锁键租户前缀）；`coordinator.py` → `governance/govern/identity`（`_current_identity` 注入/还原）与 `observability/metrics`（SLO 指标）。
- 上游消费方：`ontology/process/transaction.py`（`TransactionManager.set_coordinator` 后委托执行）、`ontology/storage/object_store.py`（提交前 CAS 失败抛本域 `TxConflict`）。Agent/编排侧如需“消息级投递重试 + DLQ”，由 [state](../state/README.md) 的 Inbox/DLQ 表 + [orchestration](../orchestration/README.md) 承接。
- 组件装配：`agentorchestra/components.py` 的 `Components.state_store()` 返回的默认 store 可直接作为 `TransactionCoordinator(store=...)` 的底座，实现“一套 store 贯通事务与编排状态”。

## 测试

仓库目前没有独立的 `tests/unit/test_tx.py`；tx 行为主要被 `tests/unit/test_state.py`（锁/幂等记录的 store 层原语）、`tests/unit/test_governance.py` 与 `tests/integration/test_integration.py`（store 生命周期）间接覆盖。协调器级用例建议参考上方“使用说明”示例编写并补入 `tests/unit/test_tx.py`。

```bash
python -m pytest tests/unit/test_state.py -v          # store 层：锁/幂等/DLQ/checkpoint 原语
python -m pytest tests/integration/test_integration.py -v
python -m pytest tests/unit -v                         # 全部单元
```

`pytest.ini` 已开启 `asyncio_mode = auto`；异步用例无需额外装饰器。
