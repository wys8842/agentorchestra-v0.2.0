# 跨模块横切能力与上线 / 演进指南

> 面向“把 agentorchestra 从单机示例跑成可上线服务”的主题文档：梳理散落在 `runtime / capability / ontology / orchestration / governance / observability` 各领域的**横切能力**（安全与权限、事务一致性、持久化恢复、并发与隔离、多租户与配额、可观测、可靠性、配置热更新、审计合规），说明它们默认关闭 / 可插拔的设计动机，并给出建议的**逐步启用路线**与**上线前检查清单**。

---

## 定位与设计动机

这些能力并没有堆在“一个模块”里，而是按领域就近实现、再经统一装配门面收敛：

- **默认关闭（opt-in）**：`runtime/core/config/__init__.py` 中所有非核心 feature 的 flag 默认 `False`（trace、skills、mcp、ontology、session、state_checkpoint、subagent、todowrite、devlog、memory 等），避免隐式文件扫描、磁盘持久化、外部服务等副作用；`Config.development()` 只打开便于本地调试的项，`Config.production()` 保持核心最小集。
- **可插拔（pluggable）**：`agentorchestra/components.py` 的 `Components` 门面提供懒加载读取、`register_*` 覆盖与 `reset()`；例如持久化 `state_store()` 未注册时回退 `orchestration.state.get_default_store()`，指标回退 `observability.metrics.get_default_collector()`。业务包内部不散落全局单例。
- **独立成域**：权限/审计/身份放 `governance` 与 `ontology/governance`，事务放 `governance/tx`，租户放 `governance/tenancy`，状态底座放 `orchestration/state`，可观测放 `observability` + `runtime/core/telemetry`——每个能力既可独立取舍，也能被其它能力复用（如 `tx` 复用 `state` 的 WAL/记录模型、审计通过 `CheckpointStore.audit_log` 落库）。

> 域布局与依赖方向详见 [docs/architecture/README.md](../architecture/README.md)。

---

## 横切能力清单

### 能力速查

| 主题 | 所在领域 / 目录 | 是否默认开启 | 文档 |
| --- | --- | --- | --- |
| 身份 / ACL / 权限判定 | `governance/govern/` | opt-in（按需装配） | [governance](../governance/README.md) |
| 本体 RBAC 安全 | `ontology/governance/security.py` | opt-in（`OntologyEngine.allow` 显式授权） | [ontology](../ontology/README.md) |
| 工具调用访问过滤 | `capability/tools/tool_filter.py` | 有默认过滤器（随 Agent 装配） | [tools](../tools/README.md) |
| 事务一致性 | `governance/tx/` | opt-in | [tx](../tx/README.md) |
| 持久化与恢复 | `orchestration/state/` | opt-in（`Config.state_checkpoint.enabled` 默认 False） | [state](../state/README.md) |
| 并发与隔离（锁/乐观锁/WAL） | `governance/tx` + `orchestration/state` + `governance/govern` | opt-in | [tx](../tx/README.md) · [state](../state/README.md) |
| 多租户与配额计费 | `governance/tenancy/` | opt-in | [tenancy](../tenancy/README.md) |
| 可观测与诊断 | `observability/` + `runtime/core/telemetry/` | 记录器/指标默认 NoOp 或关，Prometheus/OTLP 显式启用 | [observability](../observability/README.md) |
| 可靠性（重试/限流/熔断） | `runtime/core/reliability/` + `capability/tools/circuit_breaker.py` | 熔断默认开（Config），重试由组件自带，限流按需 | [tools](../tools/README.md) · [core](../core/README.md) |
| 配置热更新 | `runtime/core/config/hot.py` + `Components` | 默认关，`start_hot_reload` 开启 | [core](../core/README.md) |
| 审计合规 | `ontology/governance/audit.py`（WORM 思路）+ `governance/govern/gdpr.py`（数据主体工具） | opt-in | [governance](../governance/README.md) · [ontology](../ontology/README.md) |

---

### 1. 安全与权限治理

**位置**

- 身份 / 访问控制：`agentorchestra/governance/govern/`（`identity.py`、`acl.py`、`permission.py`、`cas.py`），顶层再导出见 `agentorchestra/governance/__init__.py`；
- 本体层 RBAC：`agentorchestra/ontology/governance/security.py`（`SecurityManager` / `SecurityContext` / `PermissionRule`）；
- 工具调用级过滤：`capability/tools/tool_filter.py`（`BaseToolFilter` / `ReadOnlyFilter` / `FullAccessFilter` / `CustomFilter`）。

**启用 / 使用方式**

```python
from agentorchestra.governance import ACLManager, PermissionChecker
from agentorchestra.governance.govern.identity import IdentityService

acl = ACLManager()
acl.grant("order:o1", "write", principal="alice")   # 行级：resource 支持 order:* 前缀通配
acl.check("order:o1", "write", principal="alice")   # True；无规则命中默认拒绝

svc = IdentityService()
async with svc.run_as("alice", ["admin"]):          # ContextVar 承载，自动向下游传播
    acl.check("order:*", "read", principal=svc.principal, roles=svc.roles)
```

- RBAC：`OntologyEngine.allow(roles, resource, action)` 显式放行；`PermissionChecker(security=..., acl=...)` 组合 RBAC（角色/资源/操作）与行级 ACL 判定，拒绝时抛 `PermissionDenied`。
- 工具层：给 Agent 装配 `ReadOnlyFilter` / `CustomFilter`，把“模型能调哪些工具”约束在策略内。
- CAS：`ObjectCAS` 统一管理对象上的系统字段（`version` / `created_tx` / `last_modified_tx`），供乐观并发控制使用。

**设计要点**

- `ACLManager` 是**默认拒绝**（无匹配规则即 False），与 RBAC“默认开放”互补；`IdentityContext` 经 ContextVar 传播，可与租户上下文同时激活、互不覆盖。
- `PermissionChecker.check` 支持 `raise_on_deny=False` 的软拒绝路径，便于预检而非抛错。
- `gdpr.py`（`PIIAnonymizer` / `GDPRExporter`，JSON/CSV 导出、访问/删除权辅助）是**模块级工具，未在 `governance/govern/__init__` 再导出**，使用时需显式 `from agentorchestra.governance.govern.gdpr import ...`。

---

### 2. 事务一致性（tx）

**位置**：`agentorchestra/governance/tx/`（`coordinator.py`、`idempotency.py`、`compensation.py`、`dlq.py`、`lock.py`、`wal.py`、`context.py`、`sync.py`、`isolation.py`）。

**启用 / 使用方式**

```python
from agentorchestra.governance.tx import TransactionCoordinator, TxAbort
from agentorchestra.components import Components

coordinator = TransactionCoordinator(store=Components.state_store())  # 复用持久化 store

async with coordinator.transaction(idempotency_key="order-2024-001", timeout=30.0) as tx:
    if not await tx.pre_condition("order:o1", expected_version=3):    # 前置校验 + 乐观锁
        raise TxAbort("pre-condition failed")
    await tx.execute("扣库存", {"sku": "A1"})
# 正常退出 → 自动 commit；异常 → 逆序补偿 + 记入 DLQ/WAL
```

**设计要点**

- 最小可用集 = **幂等 + WAL + 补偿 + DLQ + 乐观锁**；`TransactionCoordinator` 内部组合 `IdempotencyStore`（幂等键 TTL 默认 24h）、`OptimisticLock`（TTL 默认 30s）、`DeadLetterQueue`、`TxActionLog` 与 `CompensationExecutor`。
- `TxActionLog` 复用 M0 `state.wal` 的同一张 WAL 表（写 `TX_BEGIN` / `STATE_UPDATE` 等动作，`tx_id` 关联），不另起日志栈。
- 同步调用方可用 `run_sync(...)` 桥接；已在事件循环内则报错并提示改用 `async with`。
- **边界（如实说明）**：`isolation.py` 只有 `IsolationSnapshot` 抽象占位，SSI / 悲观锁**未实现**，源码注释明确留作后续“并发模型定型”扩展点——不要把当前事务当强隔离并发模型使用。缺少 `store` 时自动使用 `InMemoryCheckpointStore`（不落 DB，仅供测试/旧调用兼容）。

---

### 3. 持久化与恢复（state）

**位置**：`agentorchestra/orchestration/state/`（`checkpoint.py`、`wal.py`、`thread.py`、`interrupt.py`、`snapshot.py`、`records.py`、`backends/{memory,sqlite,postgres}`）。

**启用 / 使用方式**

```python
from agentorchestra.orchestration.state import get_default_store

# 单进程/开发：SQLite 文件（默认 ./agent_state.db）
store = get_default_store()                                   # None / "memory://" → sqlite 文件
# 多实例共享 / 生产：PostgreSQL（需安装可选依赖 [postgres]，asyncpg）
store = get_default_store("postgresql+asyncpg://user:pwd@host/db")
# 纯测试：进程内内存
store = get_default_store("in_memory://")
```

- 亦可走门面统一入口：`Components.state_store()`（默认回退 `get_default_store()`）。
- 也可通过 `Config.state_checkpoint.enabled=True` + `db_url` 让 Agent 在运行时启用持久化。
- 能力面：`Checkpoint` 保存/恢复、WAL 追加、`ThreadManager`/`ThreadState`、`Interrupt`（HITL 人工接管点）、`Snapshot`/`SnapshotPolicy`/`SnapshotWorker`。

**设计要点**

- `CheckpointStore`（`checkpoint.py`）与细粒度接口（`interfaces.py`：`ThreadStore` / `WALStore` / `SnapshotStore` / `InterruptStore` / `LockStore` / `IdempotencyStore` / `DLQStore` / `InboxStore` / `AuditStore`）共同定义存储协议；SQL 实现统一落在 `sqlalchemy_base.py`，SQLite 与 Postgres 后端只是驱动差异。
- 这是被多个横切能力复用的“底座”：`governance/tx` 的幂等/锁/DLQ/WAL、审计的 WORM 落库都写它。

---

### 4. 并发与隔离（锁 / 乐观锁 / WAL）

**位置**

- 乐观锁：`governance/tx/lock.py`（`OptimisticLock`，TTL 语义）+ `governance/govern/cas.py`（`ObjectCAS`：对象 `version` 读写校验）；
- WAL：`orchestration/state/wal.py`（append-only 动作日志，事务也复用，见 tx）；
- 隔离扩展点：`governance/tx/isolation.py`（占位）。

**启用 / 使用方式**

```python
# 事务内前置条件即“期望版本”比较（乐观并发控制）
async with coordinator.transaction(idempotency_key="k", timeout=30.0) as tx:
    if not await tx.pre_condition("order:o1", expected_version=3):  # 版本不符 → 冲突
        raise TxAbort("stale version")

# 无事务场景直接用 ObjectCAS 校验/递增版本
from agentorchestra.governance import ObjectCAS
obj = {"name": "a"}; ObjectCAS.init(obj, tx_id="t1")     # version=1, created_tx, last_modified_tx
ObjectCAS.check(obj, expected_version=1)                  # True
ObjectCAS.bump(obj, tx_id="t1")                           # version=2
```

**设计要点**

- 写路径通过“系统字段 + 版本比较”实现乐观并发控制（对象更新前带 `expected_version`，不符即 `TxAbort`/`TxConflict`）。
- WAL 记录的是“发生了什么”，是崩溃恢复与事务追踪的公共事实源；不同后端（内存/SQLite/Postgres）对 WAL 与锁记录的落地方式一致。
- 跨租户的锁/资源键应带命名空间前缀（见下文多租户），避免租户间互相影响。

---

### 5. 多租户与配额计费（tenancy）

**位置**：`agentorchestra/governance/tenancy/`（`tenant.py`、`quota.py`、`billing.py`）。

**启用 / 使用方式**

```python
from agentorchestra.governance.tenancy import TenantManager, QuotaManager, UsageRecorder

tm = TenantManager()
async with tm.run_as("acme", "alice"):          # 或 sync_run_as
    key = ...                                   # 资源键一律经命名空间处理

# 资源键隔离：当前租户上下文中自动加前缀（acme:alice:orders），无租户时不加（向后兼容）
from agentorchestra.governance.tenancy.tenant import namespace_resource, enforce_tenant_access
resource_key = namespace_resource("orders")     # → "acme:alice:orders"
enforce_tenant_access(obj["namespace"])          # 前缀不一致抛 TenantIsolationError

# 配额：token 上限，超限抛 QuotaExceeded（优雅失败）
qm = QuotaManager()
qm.set_limit("acme", 100_000)
qm.charge("acme", tokens=1200)                  # 超过即抛 QuotaExceeded

# 用量记录与导出（CSV/JSON，供计费系统）
rec = UsageRecorder()
rec.record(tenant_id="acme", model="gpt-4o", tokens=1200, latency_ms=812.3)
rec.total(tenant_id="acme")                     # 累计 tokens
rec.export_csv("usage.csv"); rec.export_json("usage.json")   # 导出给计费系统
```

**设计要点**

- `TenantContext`（`tenant_id` + 可选 `user_id`）→ `namespace = tenant_id[:user_id]`，粗/细粒度两级隔离边界；与 `IdentityService`（governance）通过不同 ContextVar 承载，可同时激活。
- 兜底函数刻意偏保守：`namespace_resource()` 强制拼前缀、`enforce_tenant_access()` 前缀不一致即抛错；无租户上下文时放行（向后兼容），避免影响未启用多租户的现有代码。
- `opt_out_namespace_scope()` 是**跨租户逃生口，仅限运维/调试**（源码注释明确“生产代码禁止使用”）。
- `QuotaManager` 为线程安全的内存计数（`RLock`）；`UsageRecorder` 为内存记录 + 滚动上限（默认 100k 条），可导出 CSV/JSON。持久化的配额存储未实现（源码标注“单实例内存计数”）。

---

### 6. 可观测与诊断

**位置**：`observability/`（`trace_logger.py`、`metrics.py`、`prometheus.py`、`otel_exporter.py`、`slo.py`）+ `runtime/core/telemetry/`（`tracing.py`、`trace_context.py`、`metrics.py`、`logging.py`、`health.py`、`monitor.py`）。

**启用 / 使用方式**

```python
from agentorchestra.components import Components

Components.enable_prometheus()                 # Prometheus 文本收集器（幂等）
Components.enable_otel_trace(endpoint="http://jaeger:4318", service_name="agentorchestra")
collector = Components.metrics_collector()     # 未启用时为默认收集器（NoOp）

# 运维 HTTP 端点（标准库实现）：/metrics /health /traces
from agentorchestra.runtime.core.telemetry.monitor import MonitorServer
MonitorServer(port=9090).start()
```

- 轨迹：`observability.TraceLogger` 双格式（JSONL + HTML）记录执行轨迹；`Config.trace.enabled` 默认 `False`（避免隐式文件 I/O）。
- 指标：`PrometheusTextCollector` / `Counter` / `Gauge` / `Histogram`；`slo.py` 定义 SLO（含事务相关的 `SLO_TX_ROLLBACK_RATE`、`SLO_TX_DURATION_SECONDS`、`SLO_TX_COMPENSATION_TRIGGERED` 等）。
- 追踪：`runtime/core/telemetry/tracing.py` 提供 Span/Tracer/Exporter，`observability.otel_exporter.OTLPHttpJsonExporter` 负责 OTLP 导出；W3C TraceContext 在 `trace_context.py`。

**设计要点**

- Prometheus 文本收集与 OTLP HTTP 导出**默认关闭、端点可达才开启**（`enable_otel_trace` 的注释明确“默认关，调用即开启”）；文本渲染不依赖外部采集库，可用标准库 HTTP 服务直接被抓取。
- 可观测在“顶层 observability”与“runtime/core/telemetry”各有一份职责：基础件（Span/日志/健康/监控）下沉 core，业务化产物（TraceLogger、SLO 常量）在 observability，二者经 `Components` 装配衔接。

---

### 7. 可靠性（重试 / 限流 / 熔断）

**位置**

- 重试：`runtime/core/reliability/retry.py`（`retry_with_backoff` 装饰器、`RetryManager`）；
- 限流：`runtime/core/reliability/ratelimit.py`（`RateLimiter` / `TokenBucket` / `SlidingWindow`）；
- 熔断：`capability/tools/circuit_breaker.py`（`CircuitBreaker`，默认 enabled=True；对应 `Config.circuit_breaker` 子配置 `failure_threshold=3`、`recovery_timeout=300`）。

**启用 / 使用方式**

```python
from agentorchestra.runtime.core.reliability.retry import retry_with_backoff

@retry_with_backoff(max_retries=3, base_delay=1.0, backoff_factor=2.0)  # 默认只重试 SymphonyException
def call_llm(): ...

from agentorchestra.capability.tools.circuit_breaker import CircuitBreaker
breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=300)
if not breaker.is_open("some_tool"):
    resp = registry.execute_tool("some_tool", args)
    breaker.record_result("some_tool", resp)     # 依据 ToolResponse 状态计数（区分预期业务失败码）
```

**设计要点**

- 熔断与 `ToolResponse` 协议协作：`NON_FAILURE_CODES`（NOT_FOUND / INVALID_PARAM / ACCESS_DENIED / CONFLICT / TIMEOUT 等）视为“预期失败”不计入熔断，避免把业务拒绝当故障。
- `LLMConfig` 已带默认 `max_retries=3` / `retry_base_delay=1.0` 等，重试参数可在 `Config.llm` 调整；`RateLimiter` 提供 TokenBucket 与 SlidingWindow 两种实现供自选。
- 三者可组合为一个调用链路（限流 → 熔断 → 重试），但当前是独立组件，由使用方编排。

---

### 8. 配置热更新

**位置**：`runtime/core/config/hot.py`（`ConfigWatch`、`register_config_callback`、`start_global_hot_reload`/`stop_global_hot_reload`）+ `Components.start_hot_reload` / `on_config_change` / `notify_config_change`。

**启用 / 使用方式**

```python
from agentorchestra.components import Components

def on_change(old_cfg, new_cfg):          # fn(old, new)，供 Agent/LLM/RateLimiter 适配
    ...
Components.on_config_change(on_change)
watcher = Components.start_hot_reload("config.json", poll_interval=2.0)   # 默认轮询 5s
# watcher.config 读取最新配置；ConfigWatch 带 debounce 去抖
Components.stop_hot_reload()
```

**设计要点**

- `ConfigWatch` 后台线程按 `poll_interval` 轮询文件 mtime，带 `debounce_seconds` 去抖；变更时同时通知实例级 listener 与全局注册回调。
- 全局热更同一时刻只允许一个 watcher（`start_global_hot_reload` 重复调用只告警并返回已有实例）。
- 配置本身遵循 opt-in：非核心子系统 flag 默认 `False`，热更适合在“核心子配置”上做灰度调整。

---

### 9. 审计合规

**位置**

- 审计：`ontology/governance/audit.py`（`AuditManager`）；存储侧记录为 `orchestration/state/records.py::AuditEntry`（WORM 语义），表位见 `sqlalchemy_base.py` 的 `_AuditLogRow`；
- 数据主体工具：`governance/govern/gdpr.py`（`PIIAnonymizer` / `GDPRExporter`，模块级、未在包 `__init__` 再导出）。

**启用 / 使用方式**

```python
from agentorchestra.ontology.governance import AuditManager

audit = AuditManager()
audit.log(principal="alice", resource="order:o1", action="read",
          detail={"mode": "get"}, success=True)
audit.query(principal="alice", limit=100)

# 落库（WORM 思路）：attach 持久化 store 后 log() 会异步 append 到 audit_log；
# clear() 只清内存、不删 DB（源码注释：保证 append-only）
audit.attach_backend(Components.state_store())
```

**设计要点**

- `AuditEntry` 只允许 append + query，接口层不提供 update/delete，`AuditManager.clear()` 明确“只清内存不删 DB”，形成 WORM 思路的最小实现。
- `OntologyEngine` 把 `AuditManager` 接进 `ToolGenerator`：由本体生成的对象查询/执行 Tool 在关键路径上自动写审计（含访问拒绝记录）。
- `gdpr.py` 提供去标识化（email/phone/id_card/name/address/IP 的脱敏规则）与数据可携（JSON/CSV）工具，属辅助模块，语义上需与真实存储的数据删除配合。

---

## 逐步启用路线（建议）

按“先能看见 → 再能恢复 → 再敢并发 → 再加限制与合规”的顺序渐进；每步都可在不启用上一步的情况下独立生效（opt-in 设计使然）。

| 阶段 | 主题 | 涉及模块 | 该步要验证的检查项 |
| --- | --- | --- | --- |
| L0 | 配置与基线 | `runtime/core/config` | 确认 feature flag 默认关闭不产生隐式副作用；`Config.production()` 作为生产基座 |
| L1 | 可观测基线 | `observability` + `core/telemetry` | `Components.enable_prometheus()`；TraceLogger 出 JSONL/HTML；Monitor `/health` `/metrics` 可达 |
| L2 | 持久化与恢复 | `orchestration/state` | 先 `sqlite` 文件 → 再 `postgresql+asyncpg`；验证 Checkpoint 保存/恢复、WAL、Interrupt、Snapshot 流程 |
| L3 | 事务与一致性 | `governance/tx` | 用真实 store 建 `TransactionCoordinator`；验证幂等重放、逆序补偿、DLQ、乐观锁冲突路径 |
| L4 | 编排 + 状态联动（可选，M2） | `orchestration/orch` + `state` | Graph/Inbox/GraphScheduler 与 thread/checkpoint 结合，事件可追溯 |
| L5 | 安全与审计 | `governance/govern` + `ontology/governance` | RBAC/ACL 默认拒绝生效；`AuditManager.attach_backend` 后日志进 DB、`clear()` 不删库；工具过滤器限制调用面 |
| L6 | 多租户与配额 | `governance/tenancy` | 租户命名空间隔离；配额耗尽走 `QuotaExceeded` 优雅降级；用量可导出 CSV/JSON |
| L7 | 可靠性强化 | `core/reliability` + `tools/circuit_breaker` + `config/hot` | 重试/限流/熔断组合生效；`ConfigWatch` 热更触发回调生效 |

**里程碑对照（源码注释中使用的编号）**：M0=持久化（state，P0）、M1=事务运行时（tx，P1）、M2=Agent 图/DAG（orchestration，P2）、M3=对象身份与权限（govern/governance，P3）、M5=可观测（M5）、M6=多租户（tenancy，P6）；M4=并发模型（锁/隔离）为源码中预留的后续里程碑。

**演进设计参考（specs）**：当前仓库内**不存在** `docs/superpowers/specs/` 目录（已核对目录与文件均无）。源码 docstring / 注释中按“roadmap §章节”引用过以下规划文件路径（相对仓库根），若后续补充请直接挂链：

```text
docs/superpowers/specs/2026-09-03-enterprise-readiness-roadmap.md
docs/superpowers/specs/2026-09-03-m0-persistence-design.md
docs/superpowers/specs/2026-09-03-m1-transaction-runtime-design.md
docs/superpowers/specs/2026-09-04-m2-agent-graph-design.md
docs/superpowers/specs/2026-09-04-m3-object-identity-acl-design.md
docs/superpowers/specs/2026-09-04-m6-multitenancy-design.md
```

（引用处示例：`orchestration/state/__init__.py`、`governance/tx/__init__.py`、`governance/tenancy/__init__.py`、`governance/govern/__init__.py`、`orchestration/orch/__init__.py` 的模块 docstring。）

---

## 上线前检查清单

基于当前代码现状（v0.2.0）：

**代码质量**

- [ ] `pytest tests` 全绿（`tests/unit`、`tests/integration`、`tests/stress`；`pytest.ini` 已设 `testpaths=tests`、`asyncio_mode=auto`）；
- [ ] `ruff check agentorchestra` 通过（select `E/F/W/I`，line-length 100，`__init__.py` 豁免 E402）；
- [ ] （可选）`mypy agentorchestra` 通过（dev 依赖含 mypy）。

**依赖与配置现状（如实核对）**

- [ ] 核心依赖仅 `pydantic>=2.0`、`openai>=1.0`、`tiktoken>=0.5`、`sqlalchemy>=2.0`、`aiosqlite>=0.19`；Anthropic/Gemini/MCP/Neo4j/YAML/Postgres 均为可选 extra（`pip install "agentorchestra[all]"` 或单项）；
- [ ] Prometheus 文本收集与 Monitor HTTP 端点基于标准库，无外部服务也能跑；OTLP 导出默认关，**端点必须可达**（`enable_otel_trace` 源码注释明示）；
- [ ] 默认 `get_default_store()` 会在本地生成 SQLite 文件 `agent_state.db`；多实例/生产务必显式指定 `postgresql+asyncpg://...`（需 `[postgres]` extra）或 `Components.register_state_store(...)`；纯测试用 `in_memory://`；
- [ ] 上线前复查所有会触发文件/建库/外部调用的 opt-in flag（`trace`、`skills`、`mcp`、`session`、`memory`、`ontology`、`state_checkpoint`、`subagent` 等），只保留有意的开启项。

**横切能力核对**

- [ ] 可观测：`enable_prometheus()` / `enable_otel_trace()` 是否按环境开启；SLO 指标是否被采集；
- [ ] 事务：`TransactionCoordinator` 使用真实持久化 store（不是默认 in-memory）；幂等键、补偿动作、DLQ 已注册；
- [ ] 并发：业务写路径带 `expected_version`（乐观锁）；WAL 生效；`tx/isolation.py` 仅为占位，未把事务当强隔离使用；
- [ ] 安全：ACL 默认拒绝、RBAC 显式授权均已配置；审计 `attach_backend` 已接持久化 store；工具访问过滤器已限定 Agent 调用面；
- [ ] 多租户：租户上下文已注入；资源键经 `namespace_resource()` / `enforce_tenant_access()` 兜底；配额超额路径能优雅降级；`opt_out_namespace_scope()` 未出现在业务代码中；
- [ ] 热更新：`ConfigWatch`/`start_hot_reload` 的回调已适配会读配置的组件（Agent/LLM/RateLimiter 等）。

---

## 相关文档

| 主题 | 文档 |
| --- | --- |
| 架构总览与目录映射、依赖方向 | [docs/architecture/README.md](../architecture/README.md) |
| 运行时总览 / 核心基础件 | [docs/runtime/README.md](../runtime/README.md) · [docs/core/README.md](../core/README.md) |
| 工具 / 记忆 / Ontology | [docs/tools/README.md](../tools/README.md) · [docs/memory/README.md](../memory/README.md) · [docs/ontology/README.md](../ontology/README.md) |
| 编排 / 持久化与恢复 | [docs/orchestration/README.md](../orchestration/README.md) · [docs/state/README.md](../state/README.md) |
| 事务 / 治理 / 多租户 | [docs/tx/README.md](../tx/README.md) · [docs/governance/README.md](../governance/README.md) · [docs/tenancy/README.md](../tenancy/README.md) |
| 可观测 | [docs/observability/README.md](../observability/README.md) |
| 总入口 | [docs/README.md](../README.md) · 根 [README.md](../../README.md) |
