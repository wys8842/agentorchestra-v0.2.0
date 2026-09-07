# Tenancy（多租户）

> 多租户隔离最小集（`governance/tenancy/`）：租户上下文（tenant_id + 可选 user_id）、token 配额与用量记录，并与 [tx](../tx/README.md) 的乐观锁自动协同（namespace 键隔离）。

## 设计动机与原则

- **两级边界**：`tenant_id` 是粗粒度顶层边界，`user_id` 是可选的细粒度维度；`TenantContext.namespace` 在两者都存在时拼成 `tenant_id:user_id`，单一键即可表达“租户”或“租户+用户”两种隔离粒度（tenant.py 的 `namespace` 属性）。
- **ContextVar 承载、与身份并行**：租户上下文经 `_current_tenant` ContextVar 承载，与 `govern` 的 IdentityService ContextVar 同时激活、互不覆盖，可在同一协程内分别读写（tenant.py 模块注释）。
- **隔离是默认、逃生有门**：`namespace_resource()` 给业务键自动加前缀；跨租户运维场景必须显式使用 `opt_out_namespace_scope()` 逃生口，从 API 设计上迫使“默认隔离、显式跨租户”。
- **显式校验优于隐式假设**：`enforce_tenant_access(resource_namespace)` 提供“当前租户前缀必须匹配资源前缀”的运行期断言，不匹配抛 `TenantIsolationError`，防止“读到了别人的数据才被发现”。
- **配额优雅失败**：配额耗尽抛 `QuotaExceeded`（携带 tenant/limit/used/attempted），调用方负责降级或提示，不崩进程（quota.py 注释）。
- **用量与计费解耦**：`UsageRecorder` 只做记录与导出（CSV/JSON），配额与账单语义由上层组合，内存实现自带滚动上限防无限增长。
- **与锁/幂等自动联动**：tx 层 `OptimisticLock`/`compare_and_swap` 读写的 resource_key 一律先过 `namespace_resource()`，让“租户 A 的订单锁”天然不等于“租户 B 的订单锁”。

## 设计优势

- 隔离规则收敛在**一个函数**（`namespace_resource`）与一个上下文管理器里，业务代码只需要“在租户上下文内跑”，不易漏拼前缀。
- 无租户上下文时行为向后兼容（键原样返回、`get_current_or_default` 回退 `"default"`），存量代码与单租户部署无需改造。
- 租户信息随 async/async 上下文自动传播，配合 `sync_run_as`/`run_as` 两种生命周期管理，线程/协程安全。
- 配额、用量、租户上下文三件套各自独立可测，也便于后续替换为持久化实现（当前为进程内存态）。
- 跨模块（tx 锁、LLM 推理、memory 管理器）通过 import 本域工具获得租户感知，形成**统一的多租户方言**而不是各自拼 key。

## 模块构成

| 路径（相对 `agentorchestra/governance/tenancy/`） | 职责 | 主要公开导出 |
|---|---|---|
| `tenant.py` | 租户上下文 + 命名空间隔离辅助 | `TenantContext`、`TenantManager`、`TenantIsolationError`、`namespace_resource`、`get_current_or_default`、`enforce_tenant_access`、`opt_out_namespace_scope` |
| `quota.py` | token 配额管理 | `QuotaExceeded`、`TokenQuota`、`QuotaManager` |
| `billing.py` | 用量记录与导出 | `UsageRecord`、`UsageRecorder` |
| `tenancy/__init__.py` | 子包门面（只再导出下表子集） | `TenantManager`、`TenantContext`、`QuotaManager`、`TokenQuota`、`QuotaExceeded`、`UsageRecorder`、`UsageRecord` |

注意：`namespace_resource`、`enforce_tenant_access`、`opt_out_namespace_scope`、`TenantIsolationError` 等函数**不在** `tenancy.__init__.__all__` 中，需从 `agentorchestra.governance.tenancy.tenant`（经典：`agentorchestra.tenancy.tenant`）深度导入。

## 功能清单

### TenantContext —— 租户上下文值

- **是什么 / 解决什么**：不可变（frozen）的“当前是谁的请求”快照，跨 await 点随 ContextVar 传播。
- **签名**：`TenantContext(tenant_id: str, user_id: str = "")`；属性 `namespace` 返回 `tenant_id` 或 `tenant_id:user_id`。
- 用途：作为 `TenantManager.run_as` 的产出对象，或直接手动构造/断言。

### TenantManager —— 上下文生命周期管理

- `TenantManager.current() -> Optional[TenantContext]`；`TenantManager.tenant_id() -> Optional[str]`；`TenantManager.namespace() -> str`（无租户时 `"default"`）。
- `async run_as(tenant_id, user_id="")`：async 上下文，进入注入、退出还原。
- `sync_run_as(tenant_id, user_id="")`：同步上下文版本。
- 边界：均为 staticmethod/上下文管理器，无实例状态；嵌套使用时内层覆盖外层、退出还原到外层。

### namespace_resource —— 命名空间化

- `namespace_resource(resource_key: str) -> str`：有租户上下文 → 返回 `"{namespace}:{resource_key}"`；在 `opt_out_namespace_scope()` 内或无线程租户 → 原样返回。
- 是 tx `OptimisticLock`、各种“资源键”唯一官方前缀入口，**业务应优先用它而非手拼字符串**。

### enforce_tenant_access / TenantIsolationError

- `enforce_tenant_access(resource_namespace)`：无租户上下文 → 放行（向后兼容）；否则要求 `resource_namespace.startswith(f"{ctx.namespace}:")`，不满足抛 `TenantIsolationError(current_tenant, target_resource)`。
- 边界：只做前缀断言，不做通配/多租户共享判定；用于读路径的双重校验（对象已带 namespace 字段时）。

### opt_out_namespace_scope —— 跨租户逃生口

- 仅限运维/调试：管理员接管、跨租户数据迁移。内部用独立 ContextVar（`_opt_out_token`）置位，`namespace_resource` 读它短路前缀拼接。
- 边界：这是“逃逸阀”，生产业务代码禁止使用（模块文档明确标注）；它只影响 `namespace_resource`/`enforce_tenant_access` 层面的拼接与断言，不代表绕过底层权限系统。

### TokenQuota / QuotaManager —— 配额

- `TokenQuota(tenant_id, limit=-1, used=0)`：`limit < 0` 表示不限量（`unlimited`）；`can_charge(tokens)` 判断“本次扣减后是否超限”；`remaining()` 返回剩余（不限返回 -1）。
- `QuotaManager`（单实例内存，`threading.RLock` 保护）：
  - `set_limit(tenant_id, limit)`（-1 即不限）；`get(tenant_id) -> TokenQuota`（不存在则创建“不限”配额）；`charge(tenant_id, tokens)`（超限抛 `QuotaExceeded`）；`reset(tenant_id)`；`snapshot() -> dict`（所有租户 used/limit/remaining 视图，供计费/观测）。
- 行为边界：配额维度当前只实现 **token**（roadmap 标注其余维度延后）；`charge` 对 `tokens <= 0` 直接返回；计数为进程内存态，重启清零。

### QuotaExceeded —— 配额耗尽异常

- `QuotaExceeded(tenant_id, limit, used, attempted=0)`，字段齐备供降级提示。

### UsageRecorder / UsageRecord —— 用量记录

- `UsageRecord(tenant_id, model, tokens, latency_ms=0.0, ts=ISO 时间)`。
- `UsageRecorder(max_records=100_000)`：
  - `record(tenant_id, model, tokens, latency_ms=0.0, ts="")`；超出 `max_records` 时滚动丢弃最旧。
  - 查询：`total(tenant_id=None) -> int`；`by_tenant() -> dict`；`snapshot() -> list[dict]`（供导出/观测）。
  - 导出：`export_csv(path)` / `export_json(path)`（写入本地文件）。
- 边界：内存实现，记录不跨进程持久化；`export_*` 直接写文件（调用方负责目录可写）；“账单/发票”等聚合语义不在本包实现。

## 使用说明

导入路径：经典 `from agentorchestra.tenancy import ...`；规范 `from agentorchestra.governance.tenancy import ...`。深层辅助函数走 `...tenancy.tenant` 子模块。

### 场景一：租户上下文 + 命名空间隔离

```python
import asyncio
from agentorchestra.tenancy import TenantManager
from agentorchestra.tenancy.tenant import namespace_resource, enforce_tenant_access

async def main():
    tm = TenantManager()
    async with tm.run_as("acme", "alice"):
        assert tm.tenant_id() == "acme"
        assert tm.namespace() == "acme:alice"
        key = namespace_resource("order:o1")       # "acme:alice:order:o1"
        enforce_tenant_access("acme:alice:order:o1")   # 通过
        try:
            enforce_tenant_access("globex:order:o1")   # 前缀不匹配
        except Exception as e:
            print(type(e).__name__, e)

    # 退出上下文后回退
    assert tm.tenant_id() is None
    assert namespace_resource("order:o1") == "order:o1"   # 无租户 → 原样

asyncio.run(main())
```

同步场景用 `with tm.sync_run_as("acme", "alice")`，其余一致。

### 场景二：配额控制

```python
from agentorchestra.tenancy import QuotaManager, QuotaExceeded

qm = QuotaManager()
qm.set_limit("acme", 1_000_000)

qm.charge("acme", 300_000)
qm.charge("acme", 500_000)
print(qm.get("acme").remaining())                 # 200_000

try:
    qm.charge("acme", 300_000)                    # 200_000+300_000 > 1_000_000
except QuotaExceeded as e:
    print(e.tenant_id, e.limit, e.used, e.attempted)

qm.set_limit("globex", -1)                        # 不限
qm.charge("globex", 10 ** 12)                     # OK
print(qm.snapshot())
```

### 场景三：用量记录与导出

```python
import tempfile, os
from agentorchestra.tenancy import UsageRecorder

recorder = UsageRecorder(max_records=10_000)
recorder.record("acme", "gpt-4o", tokens=1_234, latency_ms=820)
recorder.record("acme", "gpt-4o", tokens=2_000)
recorder.record("globex", "gpt-4o-mini", tokens=500)

assert recorder.total("acme") == 3_234
assert recorder.by_tenant()["acme"] == 3_234

p = os.path.join(tempfile.gettempdir(), "usage.csv")
recorder.export_csv(p)                            # ts,tenant_id,model,tokens,latency_ms
```

### 场景四：与 tx 乐观锁联动（键自动租户隔离）

```python
import asyncio
from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore
from agentorchestra.tx import OptimisticLock
from agentorchestra.tenancy import TenantManager
from agentorchestra.tenancy.tenant import namespace_resource

async def main():
    store = InMemoryCheckpointStore()
    await store.init()
    tm = TenantManager()

    async with tm.run_as("acme"):
        lock_acme = OptimisticLock(store)
        rec_a = await lock_acme.acquire("order:o1", "tx-1")     # 实际锁 "acme:order:o1"
        assert rec_a is not None

    async with tm.run_as("globex"):
        lock_globex = OptimisticLock(store)
        rec_b = await lock_globex.acquire("order:o1", "tx-2")   # 锁 "globex:order:o1"
        assert rec_b is not None     # 与 acme 的锁互不冲突（键不同）

    # 无租户上下文时按原键访问
    assert await store.read_version("acme:order:o1") is not None

asyncio.run(main())
```

注意事项：

- `TenantContext`/`run_as` 只负责“上下文”；真正的访问控制要配合 `enforce_tenant_access` 或底层 ACL/RBAC 使用。
- 配额与用量为内存实现，多进程部署下不共享；请自行替换为共享存储或仅在单进程内使用。
- `namespace_resource` 无租户上下文时原样返回，若业务**必须**强制带前缀，请在进入 `run_as` 后再调用。
- 配额“剩余可用”与“是否可扣”是瞬时判断，不存在预留语义；高并发扣减请依赖外层锁/事务（见 [tx](../tx/README.md)）。

## 与其他模块的关系

真实依赖（源码 import 方向）：

- 被 tx 消费：`governance/tx/lock.py` → `tenancy/tenant.namespace_resource`（`OptimisticLock` 全部键自动租户化）。
- 被运行时消费：`runtime/core/llm` 与 `capability/memory/manager` 均 import `governance/tenancy/tenant.TenantManager`，在 LLM 调用与记忆读写时读取当前租户（配额/计费的接线点）。
- 与 govern 的身份体系并行不冲突：identity 的 ContextVar 与 tenant 的 ContextVar 相互独立，可同时激活。
- 测试中可通过 store 的锁键（`read_version`）验证隔离确实生效（见上面场景四）。

## 测试

当前租户相关用例在 `tests/unit/test_governance.py`（仅 `TenantContext` 创建与属性）。Quota/UsageRecorder/隔离联动尚缺专门用例，建议补入 `tests/unit/test_tenancy.py`。

```bash
python -m pytest tests/unit/test_governance.py -v
python -m pytest tests/unit -v
```

`pytest.ini` 已开启 `asyncio_mode = auto`。
