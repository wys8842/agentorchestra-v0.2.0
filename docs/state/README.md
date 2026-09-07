# State（持久化与恢复）

> `orchestration/state/`：一套 async 的 durable 存储抽象与后端，提供 Checkpoint / WAL / Snapshot / Thread / Interrupt 等“可恢复执行”原语，并顺带承载锁、幂等键、DLQ、Inbox、审计等表级能力。

## 设计动机与原则

- **checkpoint 即恢复点**：一个 thread（会话/任务运行实例）的进展显式落为 `Checkpoint`（可带 `parent_id` 链式与 `metadata`），`latest_checkpoint` 就是崩溃后的续跑点；`ThreadManager.save_checkpoint` 会同步追加一条 WAL，做到“表里都有记录”。
- **WAL 是 append-only 的意图日志**：任何状态变更先写日志再应用，靠 `sequence_no` 单调推进、按 thread 隔离可回溯；动作类型收敛为枚举 `WALActionType`（checkpoint / state_update / interrupt / resume / snapshot / tx_begin / tx_commit），并支持 `tx_id` 关联事务。
- **快照压缩防 WAL 无限膨胀**：双阈值策略（默认 1000 条 WAL **或** 60 秒），`SnapshotWorker` 负责“拍快照 + 记 `up_to_seq` 截断点”，恢复时用快照 + 之后增量日志重建（注意：该 worker 的批量触发入口 v0.2.0 有缺陷，需逐线程 `maybe_snapshot`，详见功能清单）。
- **thread 是隔离与发现的单位**：create/list/get/update_status 全覆盖；`SnapshotWorker` 启动时自动从 store 发现活跃 thread，无需外部注入监控名单。
- **HITL 可持久化**：中断（`Interrupt`）带全局唯一 token、所在 checkpoint、reason、payload，跨重启仍可 `resolve(token, response)` 续跑；`InterruptPending` 异常驱动“暂停→外部决策→resume”闭环。
- **一套 store 承载多域能力**：locks / idempotency_keys / dead_letter / inbox_messages / inbox_acks / audit_log / iteration_snapshots 全部并入同一 `CheckpointStore` 抽象（记录类型定义在 `records.py`，避免 tx/orch 反向依赖 state 以外的包）。
- **细粒度接口 + 合并接口分层**：`interfaces.py` 按职责拆分（ThreadStore/WALStore/LockStore…）并提供组合接口 `FullCheckpointStore`；`checkpoint.py` 保留“合并版大 `CheckpointStore`”抽象以向后兼容（内存/SQL 后端均实现它）。
- **后端可插拔且默认零配置**：`InMemoryCheckpointStore` 零依赖；默认 `get_default_store(None)` 落到本机 SQLite 文件；`in_memory://` 显式内存、`postgresql+asyncpg://` 选 PostgreSQL；全部方法保持 async 签名，上层代码后端无关。

## 这样设计的好处

- 恢复路径单一清晰：崩溃 → `latest_checkpoint` 续跑，或 `Snapshot` + `read_wal(after_seq)` 回放，行为可预期、可测试。
- WAL 压缩、中断、快照都是“后台协程 + 表操作”，可随 Agent 事件循环常驻，不阻塞主流程。
- 内存与 SQL 后端共用同一套表级语义，单元测试用 InMemory、集成/生产切 SQLite/Postgres，上层零改动。
- 锁/幂等/DLQ/Inbox/审计在同一事务与编排体系内共享数据源，天然支持“事务 + 图消息”组合场景。
- 记录类型内聚在 state 包，避免包间循环依赖（tx、orch、runtime 都只 import state，不反向）。

## 模块构成

| 路径（相对 `agentorchestra/orchestration/state/`） | 职责 | 主要公开导出（真实） |
|---|---|---|
| `__init__.py` | 包门面 + 默认 store 工厂 | `get_default_store`、`reset_default_store`、`Checkpoint`、`CheckpointStore`、`ThreadManager`、`ThreadState`、`ThreadStatus`、`Interrupt`、`InterruptPending`、`InterruptStatus`、`WALEntry`、`WALActionType`、`Snapshot`、`SnapshotPolicy`、`SnapshotWorker` |
| `checkpoint.py` | Checkpoint 数据模型 + 合并版 `CheckpointStore` 抽象（含 JSON 兜底序列化助手） | `Checkpoint`、`CheckpointStore`、`dumps_json`、`loads_json`、`json_default`，并从 `interfaces` 再导出细粒度接口名 |
| `interfaces.py` | 按职责拆分的细粒度 ABC | `ThreadStore`、`CheckpointStore`、`WALStore`、`SnapshotStore`、`InterruptStore`、`LockStore`、`IdempotencyStore`、`DLQStore`、`InboxStore`、`AuditStore`、`IterationSnapshotStore`、`FullCheckpointStore` |
| `records.py` | 表级记录类型（无 IO） | `LockRecord`、`IdempotencyRecord`、`DLQEntry`、`InboxMessage`、`InboxAck`、`AuditEntry` |
| `thread.py` | 会话/任务生命周期 | `ThreadManager`、`ThreadState`、`ThreadStatus` |
| `wal.py` | WAL 条目与动作类型 | `WALEntry`、`WALActionType` |
| `snapshot.py` | 周期快照与压缩 | `Snapshot`、`SnapshotPolicy`、`SnapshotWorker` |
| `interrupt.py` | HITL 中断与恢复 | `Interrupt`、`InterruptStatus`、`InterruptPending`、`InterruptResumer`、`InterruptHandler`（后两者**未进 `state.__init__.__all__`**，走深度导入） |
| `backends/memory_backend.py` | 内存后端（零依赖、线程安全） | `InMemoryCheckpointStore` |
| `backends/sqlite_backend.py` | SQLite 后端（aiosqlite） | `SQLiteCheckpointStore`（含 `in_memory()` 类方法） |
| `backends/postgres_backend.py` | PostgreSQL 后端（asyncpg，可选依赖） | `PostgresCheckpointStore` |
| `backends/sqlalchemy_base.py` | SQL 后端共享基类与 ORM 表模型 | `SQLAlchemyCheckpointStore`、`Base` |

`backends/__init__.py`：直接导出 `InMemoryCheckpointStore`；`SQLiteCheckpointStore` / `PostgresCheckpointStore` 用模块 `__getattr__` **懒加载**（避免在无 SQLAlchemy 2.0 的环境导入失败）。

## 功能清单

### CheckpointStore 抽象（合并版）与 Checkpoint

- `Checkpoint(thread_id, checkpoint_id, state, parent_id=None, metadata=None, created_at=now)`：`to_dict()`（created_at 转 ISO 字符串）/ `from_dict()`（兼容字符串或 datetime）。
- 合并版 `CheckpointStore(ABC)`（`checkpoint.py`，所有方法 async）按域分组：
  - Thread：`create_thread(thread_id, metadata=None)`（已存在忽略）/ `get_thread` / `list_threads(status=None)` / `update_thread_status(thread_id, status)`。
  - Checkpoint：`save_checkpoint`（同 id 覆盖）/ `load_checkpoint(thread_id, checkpoint_id)` / `list_checkpoints(thread_id, limit=50)`（时间倒序）/ `latest_checkpoint` / 便捷方法 `count_checkpoints`。
  - WAL：`append_wal(entry) -> int`（返回 sequence_no）/ `read_wal(thread_id, after_seq=0, limit=1000)` / `max_wal_seq(thread_id)`。
  - Snapshot：`save_snapshot` / `latest_snapshot(thread_id)`。
  - Interrupt：`create_interrupt(intr)` / `resolve_interrupt(token, response)` / `get_interrupt(token)` / `list_interrupts(status=None, thread_id=None)`。
  - Lock（M1）：`acquire_lock(resource_key, owner_tx, ttl_seconds=30.0) -> Optional[LockRecord]` / `compare_and_swap(resource_key, expected_version, owner_tx, expected_fencing_token=None) -> bool` / `release_lock`（仅 owner）/ `read_version` / `read_fencing_token`。
  - Idempotency（M1）：`put_idempotency(record)`（同 key 覆盖）/ `get_idempotency(key)`（过期视为无）/ `delete_expired_idempotency() -> int`。
  - DLQ（M1）：`enqueue_dlq(entry)` / `list_dlq(limit=100, status="open")` / `resolve_dlq(dlq_id, note=None)`。
  - Iteration snapshot（C-N9）：`save_iteration_snapshot(graph_id, thread_id, iteration)` / `load_iteration_snapshot(...) -> dict`（O(1)，崩溃恢复 graph 循环计数用）。
  - Inbox（M2）：`enqueue_message(msg)`（同 msg_id 覆盖）/ `list_pending_messages(thread_id, to_node=None, limit=100)`（仅 queued 且未过期）/ `mark_delivered(msg_id, ack_token)` / `mark_failed(msg_id, error, attempts)` / `ack_message(msg_id, ack_token=None, status="acked")` / `delete_expired_messages()`。
  - Audit（M3，WORM）：`append_audit(entry)`（只增）/ `query_audit(limit=100, principal=None, resource=None)`（时间倒序）。
- 同名易混提醒：`state.interfaces.CheckpointStore` 是**细粒度 CRUD 版**（只含 checkpoint 方法，供接口组合用）；`state.checkpoint.CheckpointStore` 是**合并版大抽象**，两者是不同的类，公共 `state.CheckpointStore` 指后者。

### interfaces.py —— 细粒度接口

- 十个职责接口各自由上表对应方法组构成；`FullCheckpointStore(*十接口, ABC)` 组合“完整存储”契约。
- 意义：让只依赖“WAL”或只依赖“锁”的下游能声明最小接口，而不是被迫依赖大抽象。

### records.py —— 记录类型

- `LockRecord(resource_key, version, owner_tx, fencing_token=0, held_since, expires_at=None)`：`fencing_token` 单调递增（每次 acquire +1），防僵尸事务。
- `IdempotencyRecord(idempotency_key, request_hash, tx_id=None, status="running", result=None, created_at, expires_at=None)`：status 取值 `running/completed/failed`。
- `DLQEntry(tx_id, action_name, error=None, attempts=0, status="open", created_at, resolved_at=None, id=None)`：`id` 供 `resolve_dlq` 定位（内存自增 / SQL 自增）。
- `InboxMessage(msg_id, graph_id, thread_id, to_node, content, from_node=None, condition=None, status="queued", attempts=0, ...)`：status 生命周期 `queued → delivered → acked/rejected` 或 `failed/expired`；`expired` 属性。
- `InboxAck(msg_id, ack_token=None, status="acked", acked_at)`；`AuditEntry(principal, resource, action, obj_id=None, success=True, detail, tx_id=None, ts)`。

### thread.py —— 会话管理

- `ThreadStatus`：`active / interrupted / completed / failed`。
- `ThreadState`（内存视图，`from_row` 兼容 datetime/ISO 字符串）。
- `ThreadManager(store)`：`create_thread(thread_id=None, metadata=None) -> str`（缺省 `thr-<uuid4 hex12>`）/ `get(thread_id)` / `update_status(thread_id, ThreadStatus)` / `latest_checkpoint` / `list_checkpoints` / `save_checkpoint(cp)`（额外写一条 `action_type="checkpoint"` 的 WAL）。

### wal.py —— WAL 条目

- `WALActionType(str, Enum)`：`CHECKPOINT/STATE_UPDATE/INTERRUPT/RESUME/SNAPSHOT/TX_BEGIN/TX_COMMIT`。
- `WALEntry(thread_id, action_type, payload, sequence_no=0, tx_id=None, created_at)`：`to_dict()`/`from_dict()`（容忍字符串 action_type）。

### snapshot.py —— 周期快照

- `Snapshot(thread_id, snapshot_id, up_to_seq, state, metadata=None, created_at)`：`up_to_seq` 表示该快照覆盖了 WAL 前 N 条。
- `SnapshotPolicy(wal_threshold=1000, interval_seconds=60.0, enabled=False)`：`enabled` 默认关闭，避免无后台任务时的噪音。
- `SnapshotWorker(store, policy=None, thread_ids_provider=None)`：
  - `maybe_snapshot(thread_id)`：按“WAL 增量 ≥ 阈值 或 距上次快照 ≥ 间隔”判定；无最新 checkpoint 则跳过。
  - `start() -> Task`（`policy.enabled=False` 时抛 `RuntimeError`）/ `stop()`（幂等，可复用实例再 start）。
  - **已知缺陷（v0.2.0）**：批量入口 `run_once()` 把 `thread_ids_provider` 原样交给内部 `_maybe_await`——默认提供器 `_default_thread_ids` 是 bound async method（非 coroutine、非结果），会抛 `TypeError: 'method' object is not iterable`；传同步 `lambda: [...]` 同样拿到函数对象而非列表。批量自动压缩当前不可用，建议**逐线程调 `maybe_snapshot(thread_id)`** 驱动。

### interrupt.py —— HITL 中断

- `InterruptStatus`：`pending / resumed / cancelled / expired`。
- `Interrupt(token, thread_id, checkpoint_id, reason, payload=None, status=PENDING, response=None, created_at, resolved_at)`：`to_dict()`。
- `InterruptPending(token, reason, payload)`：业务代码 raise 此异常表示“等待外部输入”，捕获方应落一条 `Interrupt` 并持久化 token。
- `InterruptResumer(store, poll_interval=1.0, max_handler_failures=3)`：注册 handler（签名 `async (token, response, interrupt) -> None`，按 reason 匹配）；`start()/stop()` 后台轮询或 `poll_once()` 手动触发；消费 `resumed` 状态中断，handler 连续失败达阈值后放行后续轮询重试，避免卡死在 RESUMED。
- 深度导入：`InterruptResumer`/`InterruptHandler` 不在 `state.__init__.__all__`。

### get_default_store —— 默认 store 工厂

- `get_default_store(db_url=None) -> CheckpointStore`（懒加载单例，仅 `db_url=None` 时缓存并复用实例）：
  - `None` 或 `"memory://"` → 本机 SQLite 文件 `sqlite+aiosqlite:///./agent_state.db`；
  - `"in_memory://"` → `InMemoryCheckpointStore`（无 DB）；
  - `"postgresql..."` → `PostgresCheckpointStore(db_url)`；
  - `"sqlite..."` → `SQLiteCheckpointStore(db_url)`；
  - 其它前缀 → `ValueError`。
- 生命周期：**在无运行事件循环处调用会自动 `asyncio.run(store.init())`**；在事件循环内调用则返回未 init 的 store，init 由调用方负责。`reset_default_store()` 清缓存（测试用）。

## 使用说明

导入路径：经典 `from agentorchestra.state import ...`；规范 `from agentorchestra.orchestration.state import ...`。`backends` 等同理（`agentorchestra.state.backends` ↔ `agentorchestra.orchestration.state.backends`）。

### 场景一：store 选择

```python
import asyncio

async def main():
    # ① 无 DB 内存 store：零依赖，最常用于测试
    from agentorchestra.state.backends.memory_backend import InMemoryCheckpointStore
    mem = InMemoryCheckpointStore()
    await mem.init()
    await mem.close()

    # ② SQLite 内存（单连接）：aiosqlite/sqlalchemy 为项目核心依赖
    from agentorchestra.orchestration.state.backends.sqlite_backend import SQLiteCheckpointStore
    sq = SQLiteCheckpointStore.in_memory()
    await sq.init()
    await sq.close()

    # ③ 事件循环外调用才会自动 init：
    # store = get_default_store("in_memory://")   # 无循环 → 内部 asyncio.run(init)

asyncio.run(main())
```

注意：`get_default_store()`（不带参）会**在工作目录创建 `./agent_state.db`** 并缓存单例；只想纯内存请传 `"in_memory://"`；在 `async` 环境内取到的 store 需先 `await store.init()`。

### 场景二：thread + checkpoint + WAL + 恢复

```python
import asyncio
from agentorchestra.state import ThreadManager, Checkpoint, ThreadStatus
from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore

async def main():
    store = InMemoryCheckpointStore()
    await store.init()
    mgr = ThreadManager(store)

    tid = await mgr.create_thread(metadata={"task": "订单处理"})
    await mgr.save_checkpoint(Checkpoint(thread_id=tid, checkpoint_id="cp-1",
                                         state={"step": 1, "history": []}))
    await mgr.save_checkpoint(Checkpoint(thread_id=tid, checkpoint_id="cp-2",
                                         state={"step": 2, "history": ["reserve"]}))

    # 崩溃恢复：取最新 checkpoint
    cp = await mgr.latest_checkpoint(tid)
    assert cp.checkpoint_id == "cp-2" and cp.state["step"] == 2

    # WAL 里能看到 ThreadManager 追加的 checkpoint 记录
    entries = await store.read_wal(tid, after_seq=0)
    print([(e.action_type.value, e.sequence_no) for e in entries])

    await mgr.update_status(tid, ThreadStatus.COMPLETED)
    state = await mgr.get(tid)
    assert state.status == ThreadStatus.COMPLETED

asyncio.run(main())
```

### 场景三：快照压缩（策略驱动，手动对单 thread 触发）

```python
import asyncio
from agentorchestra.state import SnapshotPolicy, SnapshotWorker
from agentorchestra.state.backends.memory_backend import InMemoryCheckpointStore
from agentorchestra.state import ThreadManager, Checkpoint

async def main():
    store = InMemoryCheckpointStore()
    await store.init()
    mgr = ThreadManager(store)
    tid = await mgr.create_thread()

    # 造一点 WAL 增量
    for i in range(3):
        await mgr.save_checkpoint(Checkpoint(thread_id=tid, checkpoint_id=f"cp-{i}",
                                             state={"step": i}))

    policy = SnapshotPolicy(wal_threshold=2, interval_seconds=60, enabled=True)
    worker = SnapshotWorker(store, policy=policy)
    snap = await worker.maybe_snapshot(tid)         # 达到 2 条阈值 → 拍 1 张快照
    assert snap is not None
    latest = await store.latest_snapshot(tid)
    assert latest is not None and latest.state["step"] == 2

asyncio.run(main())
```

### 场景四：HITL 中断 + resume

```python
import asyncio
from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore
from agentorchestra.state import Interrupt, InterruptStatus
from agentorchestra.state.interrupt import InterruptResumer          # 深度导入

async def main():
    store = InMemoryCheckpointStore()
    await store.init()

    # Agent 侧：发起中断并持久化 token
    token = "tok-approve-1"
    intr = Interrupt(token=token, thread_id="t1", checkpoint_id="cp-3",
                     reason="approve_payment", payload={"amount": 100})
    await store.create_interrupt(intr)

    # 业务侧：审批通过 → resolve
    await store.resolve_interrupt(token, {"decision": "approved"})

    # Resumer：消费 resumed 状态并触发 handler（也支持 start()/stop() 后台轮询）
    resumer = InterruptResumer(store, poll_interval=0.05)

    async def on_approve(tok, response, interrupt):
        print("resumed:", tok, response, interrupt.payload)

    resumer.register_handler("approve_payment", on_approve)
    processed = await resumer.poll_once()       # 手动轮询一轮
    assert processed == 1

asyncio.run(main())
```

注意事项：

- InMemory 后端跨实例/进程不共享；需要持久/多进程时选 SQLite 或 Postgres，并记得 `init()`/`close()`。
- `append_wal` 的 `sequence_no` 由 store 分配（内存按 thread 内递增）；并发写由后端锁保证。
- SnapshotWorker 的批量 `run_once()` 在本版本存在缺陷（见上），请用 `maybe_snapshot(thread_id)` 手动驱动。
- 审计接口只提供 append/query，物理上无 update/delete，这是 WORM 语义的接口层保证。
- 同名陷阱：`interfaces.CheckpointStore`（CRUD 版）与 `checkpoint.CheckpointStore`（合并版）不是同一个类，别跨模块混用类型判断。

## 与其他模块的关系

真实依赖（源码 import 方向）：

- 上游消费者（都只 import state，不反向）：
  - [tx](../tx/README.md)：`coordinator.py`（缺省 store 直接用 `InMemoryCheckpointStore`、`acquire_lock/compare_and_swap/release_lock`）、`idempotency.py`/`dlq.py`/`lock.py`/`wal.py` 分别消费 `state.records` 与 `state.wal` 类型、在 store 表上实现事务语义。
  - [orchestration](../orchestration/README.md)：`inbox.py` 消费 `state.records.InboxMessage` 与 Inbox 表；`scheduler.py` 用 Inbox 发消息并用 `save/load_iteration_snapshot`（O(1)）持久化图循环计数。
  - `runtime/core/agent/base.py`：执行中自动写 Checkpoint/WAL、抛 `InterruptPending`（HITL）。
  - `ontology/governance/audit.py`：使用 `state.records.AuditEntry` 与 store 审计表。
- 后端内部：`backends/sqlite_backend.py`、`backends/postgres_backend.py` 继承 `backends/sqlalchemy_base.py` 的 `SQLAlchemyCheckpointStore`（ORM 表模型：threads / checkpoints / wal / snapshots / interrupts / locks / idempotency_keys / dead_letter / iteration_snapshots / inbox_messages / inbox_acks / audit_log）；`memory_backend.py` 独立实现（线程安全、无 SQL）。
- 装配点：`agentorchestra/components.py` 的 `Components.register_state_store(factory)` / `Components.state_store()` 默认回退到本包的 `get_default_store()`，是全局“唯一 store”的接线处。

## 测试

相关用例见 `tests/unit/test_state.py`（Checkpoint 序列化、LockRecord/IdempotencyRecord/DLQEntry、InMemory store 的 thread/checkpoint/锁/幂等原语）、`tests/integration/test_integration.py`（store + 完整工作流 + 锁生命周期）。

```bash
python -m pytest tests/unit/test_state.py -v
python -m pytest tests/integration/test_integration.py -v
python -m pytest tests/unit -v
```

`pytest.ini` 已开启 `asyncio_mode = auto`。
