# Governance（治理域）

> 收纳三个治理子包：`govern`（对象身份 / ACL / 权限 / 对象 CAS）、`tx`（事务运行时）与 `tenancy`（多租户）。本文以 `govern` 的公共 API 为主线；`tx` 与 `tenancy` 的详细内容见 [tx](../tx/README.md) 与 [tenancy](../tenancy/README.md)。

## 设计动机与原则

- **治理横切、随事务流动**：principal / roles 经 `contextvars.ContextVar` 承载（`agentorchestra/governance/govern/identity.py` 的 `_current_identity`），`TransactionCoordinator.transaction()` 进入时注入、退出时 `reset`，使审计、ACL、事务动作在**同一上下文**内自动拿到当前身份，无需在调用链上逐层传参。
- **两段式权限决策**：`PermissionChecker` 先做 RBAC（委托 `ontology.governance.SecurityContext` + 业务自备的 SecurityManager），再做对象级 ACL；`PermissionDenied` 设计为在“事务 pre-condition”抛出的异常，由 coordinator 捕获后自动触发逆序补偿（见 permission.py 与 tx/coordinator.py）。
- **RBAC 与 ACL 语义互补**：RBAC 决定“角色能对哪类资源/动作做什么”，无规则时默认放行；ACL 是**白名单**，决定“谁能操作具体某一行”，无规则默认拒绝（acl.py `check()` 对空规则直接返回 `False`）。
- **对象级乐观并发内嵌**：对象携带 `SYSTEM_FIELDS`（`version / created_tx / last_modified_tx`），`ObjectCAS` 统一读写校验；配合 tx 层基于 locks 表的 `fencing_token`，防止僵尸事务在 TTL 后误写（见 cas.py 与 [tx](../tx/README.md)）。
- **最小可用、显式装配**：`permission_checker`、`security`、`acl` 均为可选依赖——未装配即放行（`None` 语义），已装配行级权限才被强制，避免治理组件缺省时阻塞业务。
- **域内协作而不互相侵入**：`govern`、`tx`、`tenancy` 物理同属 `governance` 包：tx 的 `OptimisticLock` 自动调用 tenancy 的 `namespace_resource()` 拼租户前缀，coordinator 自动注入 govern 的 identity；各子包保持独立可导入。
- **扁平兼容零成本**：重构后 `governance` 收纳了 `govern/tx/tenancy`；经典扁平路径（如 `agentorchestra.governance.acl`）由 `agentorchestra/_legacy.py` 别名到新物理模块，**同一个模块对象**，不重复加载、类身份一致。

## 这样设计的好处

- 身份/租户/权限作为“上下文”存在，业务动作代码无需感知治理细节，治理规则变更不侵入核心逻辑。
- 对象行级授权与类型级 RBAC 分层：既能粗放授权也能精细到单行，`check()` 先试 `resource:obj_id`、失败再回退 `resource` 通配。
- 每个组件都保留“缺省可用”的边界，测试与最小集成不需要先搭完整治理栈。
- 角色、ACL、版本、身份在同一事务上下文内彼此引用、互相配合，跨模块一致性由测试契约约束。
- 兼容别名让存量代码无需改动即可迁移到新物理布局。

## 模块构成

| 路径（相对 `agentorchestra/governance/`） | 职责 | 主要公开导出（真实） |
|---|---|---|
| `govern/identity.py` | principal + roles 身份上下文（ContextVar 承载） | `IdentityContext`、`IdentityService`、`get_identity_service`、`current_principal`、`current_roles` |
| `govern/acl.py` | 对象级 ACL（行级授权，内存白名单） | `ACLRule`、`ACLManager` |
| `govern/permission.py` | 两段式权限决策（RBAC → ACL） | `PermissionDenied`、`PermissionChecker` |
| `govern/cas.py` | 对象 `SYSTEM_FIELDS` 的 CAS 读写辅助 | `ObjectCAS` |
| `govern/gdpr.py` | GDPR 合规导出 / PII 匿名化工具 | `DataSubjectRight`、`ExportRecord`、`PIIAnonymizer`、`GDPRExporter`（**深度模块导入**，未进入公共 `__all__`） |
| `govern/__init__.py` | `govern` 子包公共门面 | 上表各模块公共类（见 `__all__`） |
| `tx/` | 事务运行时（协调器/幂等/补偿/DLQ/锁/WAL） | 见 [tx](../tx/README.md) |
| `tenancy/` | 多租户（租户上下文/配额/用量） | 见 [tenancy](../tenancy/README.md) |
| `governance/__init__.py` | 域门面：从 `govern` **再导出**经典公共 API | `IdentityService`、`IdentityContext`、`get_identity_service`、`current_principal`、`current_roles`、`ACLRule`、`ACLManager`、`PermissionChecker`、`PermissionDenied`、`ObjectCAS` |

## 功能清单

### Identity —— 身份上下文

- **是什么 / 解决什么**：定义“当前是谁、带哪些角色”。单一进程内用 ContextVar 传递，事务、ACL、审计无需显式传参即可读取。
- **关键类与签名要点**：
  - `IdentityContext(principal="anonymous", roles=[])`，`has_role(role) -> bool`。
  - `IdentityService(default_principal="anonymous")`：`current() -> IdentityContext`；属性 `principal` / `roles`；`async run_as(principal, roles=None)`（async 上下文）与 `sync_run_as(...)`（同步上下文），进入设置、退出还原；`set(principal, roles=None)` 直接设置；`clear()` 清回默认。
  - `get_identity_service() -> IdentityService`：全局单例，懒加载。
  - 模块函数 `current_principal() -> str`、`current_roles() -> list[str]`：等价读取全局服务当前值。
- **行为边界**：`set()`/`clear()` 不做 token 生命周期管理，建议仅用于一次性注入；`ContextVar` 是协程上下文隔离的，子任务需自行携带上下文；无身份时的默认 principal 为 `"anonymous"`。

### ACL —— 对象级访问控制

- **是什么 / 解决什么**：为“具体对象行”而非“资源类型”授权。`resource` 支持精确（`order:o1`）与通配（`order:*`）。
- **关键类与签名要点**：
  - `ACLRule(resource, permission, principal=None, role=None)`。
  - `ACLManager`：
    - 管理：`grant(resource, permission, principal=None, role=None) -> ACLRule`；`revoke(...) -> int`（按四个字段全等匹配，返回撤销条数）；`clear()`；`list_rules() -> list[ACLRule]`（返回副本）。
    - 决策：`check(resource, permission, principal=None, roles=None) -> bool`。
  - 匹配语义：`permission` 支持 `"*"`；`principal`/`role` 至少一个非空，任一匹配即放行（二者同时指定时为 OR）；空规则集一律返回 `False`。
- **行为边界**：纯内存实现（`_rules: list[ACLRule]`），进程重启丢失，扩展持久化 backend 由调用方替换实现；`check()` 只读不改状态。

### Permission —— 权限决策与拒绝异常

- **是什么 / 解决什么**：把 RBAC 与 ACL 串成“两段式”决策，并统一抛出语义明确的 `PermissionDenied`。
- **关键类与签名要点**：
  - `PermissionDenied(resource, permission, principal)`：携带 `resource/permission/principal` 三字段，消息形如 `权限不足: xxx 无权执行 ... on ...`。
  - `PermissionChecker(security=None, acl=None, default_roles=None)`：
    - `check(resource, permission, principal=None, roles=None, obj_id=None, raise_on_deny=True) -> bool`。
    - ① `security` 非空时以 `SecurityContext(principal, roles)` 询问 RBAC；② `obj_id` 非空且配置 `acl` 时，先查 `f"{resource}:{obj_id}"`，再回退查 `resource`；都未命中即拒绝。
    - `raise_on_deny=False` 时返回 `False`；否则抛 `PermissionDenied`。
- **行为边界**：`security is None` 跳过 RBAC 段；`obj_id is None` 不强制行级；`acl is None` 且给了 `obj_id` 时视为“行未上锁”，由 RBAC 结果放行——ACL 是可选加固而非默认要求。

### CAS —— 对象版本读写

- **是什么 / 解决什么**：把“版本号”约定收敛到一处：insert 注入、update 递增、校验比对，业务层不必重复实现。
- **关键类与签名要点**（`ObjectCAS` 全部为 staticmethod）：
  - `version_of(obj) -> int`：无对象或无 version 视为 0。
  - `init(obj, tx_id=None)`：注入 `version=1`、`created_tx`、`last_modified_tx`（tx 缺省 `"none"`）。
  - `bump(obj, tx_id=None)`：`version + 1`，刷新 `last_modified_tx`。
  - `check(current, expected_version) -> bool`：`expected_version is None` 视为不校验（返回 True）。
  - `strip_system_fields(obj) -> dict`：剔除三个系统字段，返回纯业务视图。
  - `SYSTEM_FIELDS = {"version", "created_tx", "last_modified_tx"}`。
- **行为边界**：只操作传入的 dict，不做持久化；真正的原子性由 tx 层 locks 表的 `compare_and_swap` 保证（见 [tx](../tx/README.md)）。

### gdpr.py —— 数据主体合规工具（深度导入）

- **是什么 / 解决什么**：面向数据主体权利的最小工具集：访问/更正/删除/可携带（`DataSubjectRight` 枚举），JSON/CSV 导出（`GDPRExporter.export_to_json/export_to_csv`）、按主体取数（`get_subject_data`）与删除计数（`erase_subject_data`），以及字段级匿名化（`PIIAnonymizer`：邮箱保域名、电话保后 4 位、姓名加盐哈希、地址保留前两级等）。
- **行为边界**：仅做“记录导出/计数”与字段变形，**不执行真实存储删除**；该类工具未列入 `govern` 公共 `__all__`，只能从 `agentorchestra.governance.govern.gdpr` 深度导入。

## 使用说明

导入路径说明：以下“规范路径”指向真实物理文件；“经典路径”由 `_legacy.py` 别名解析，二者指向同一模块对象。

```python
# ---- 身份（经典：agentorchestra.governance.identity / 规范：...govern.identity）----
from agentorchestra.governance.govern.identity import IdentityService, current_principal

svc = IdentityService()
async with svc.run_as("alice", ["admin", "finance"]):
    assert current_principal() == "alice"

with svc.sync_run_as("bob"):
    assert svc.principal == "bob"
# 退出上下文后回到默认 anonymous
assert svc.principal == "anonymous"
```

```python
# ---- ACL（对象行级）----
from agentorchestra.governance.govern.acl import ACLManager

acl = ACLManager()
acl.grant("order:o1", "write", principal="alice")   # 指定用户
acl.grant("order:*", "read", role="finance")        # 指定角色 + 通配资源
acl.grant("order:*", "*", role="admin")             # permission 通配

assert acl.check("order:o1", "write", principal="alice")
assert acl.check("order:o1", "read", principal="carol", roles=["finance"])
assert not acl.check("order:o2", "write", principal="carol")  # 未授权 → False

acl.revoke("order:*", "read", role="finance")       # 返回 1
```

```python
# ---- PermissionChecker：RBAC(security) + ACL 两段式 ----
# security 由业务自备（实现 check(resource, permission, ctx) -> bool），这里用 None 表示跳过 RBAC 段
from agentorchestra.governance.govern.acl import ACLManager
from agentorchestra.governance.govern.permission import PermissionChecker, PermissionDenied

acl = ACLManager()
acl.grant("order:o1", "write", principal="alice")

checker = PermissionChecker(acl=acl)                 # security=None
assert checker.check("order", "write", principal="alice", obj_id="o1")
try:
    checker.check("order", "write", principal="mallory", obj_id="o1")
except PermissionDenied as e:
    assert e.principal == "mallory"                  # 携带决策上下文字段

# raise_on_deny=False → 返回 False 而非抛异常
assert checker.check("order", "write", principal="mallory", obj_id="o1",
                     raise_on_deny=False) is False
```

```python
# ---- ObjectCAS：insert/update 版本维护 ----
from agentorchestra.governance.govern.cas import ObjectCAS

obj = ObjectCAS.init({"sku": "A1"}, tx_id="tx-1")    # {"version":1, "created_tx":"tx-1", ...}
assert ObjectCAS.check(obj, expected_version=1)
ObjectCAS.bump(obj, tx_id="tx-2")                    # version → 2
assert obj["version"] == 2 and obj["last_modified_tx"] == "tx-2"
assert ObjectCAS.strip_system_fields(obj) == {"sku": "A1"}
```

**与事务联动（推荐用法）**：在 `TransactionCoordinator` 上装配 `PermissionChecker`，事务体内用 `tx.authorize(resource, permission, obj_id=None)` 显式授权，拒绝即抛 `PermissionDenied`，由 coordinator 逆序补偿：

```python
import asyncio
from agentorchestra.governance.tx import TransactionCoordinator
from agentorchestra.governance.govern.permission import PermissionDenied

async def main():
    coordinator = TransactionCoordinator(permission_checker=checker)   # checker 见上例
    coordinator.register_action("reserve", execute_fn=lambda p, tx: {"reserved": p["sku"]},
                                compensate_fn=lambda p, tx: None)

    # alice 对 order:o1 有写 ACL（见上例 grant）→ 授权通过并执行
    async with coordinator.transaction(idempotency_key="auth-demo-1", principal="alice") as tx:
        tx.authorize("order", "write", obj_id="o1")
        await tx.execute("reserve", {"sku": "A1"})

    # mallory 无授权 → authorize 抛 PermissionDenied，事务未执行
    try:
        async with coordinator.transaction(idempotency_key="auth-demo-2", principal="mallory") as tx:
            tx.authorize("order", "write", obj_id="o1")
            await tx.execute("reserve", {"sku": "A1"})
    except PermissionDenied as e:
        print("denied:", e.principal)

asyncio.run(main())
```

注意事项：

- `PermissionChecker.check` 需要 `security.check(resource, permission, ctx)` 的第三方实现，本包不内置 RBAC 规则库；`roles` 未传时回退 `default_roles`。
- `IdentityService` 的全局单例用于 `current_principal()`/`current_roles()` 与 coordinator 的默认身份注入；若使用自建实例，请显式传给 `transaction(principal=..., roles=...)`。
- ACL 规则为进程内存态，服务重启需重新 `grant`。

## 与其他模块的关系

真实依赖（源码 import 方向）：

- `govern/permission.py` → `ontology/governance/`（`SecurityContext`，供 RBAC 决策）。
- `governance/tx/coordinator.py` → `govern/identity.py`（`_current_identity` 注入/还原）与 `governance/tx` 内部各模块；tx 整体依赖 `orchestration/state/`（见 [tx](../tx/README.md)）。
- `governance/tx/lock.py` → `governance/tenancy/tenant.py`（`namespace_resource`，锁键自动租户命名空间化）。
- `ontology/process/transaction.py`、`ontology/storage/object_store.py` → `governance/tx`（coordinator 委托执行；CAS 提交校验失败抛 `TxConflict`）。
- 反向：coordinator 默认把 `principal/roles` 写入 `TxContext`，供审计（`state.records.AuditEntry` / store `append_audit`）读取——本域不直接写审计表，审计消费方在 state/ontology 侧。

包边界提醒：`governance` 是**域包装包**，公共符号来自 `govern` 子包再导出；不要把它当成 `govern` 各模块的物理位置。经典深层模块名（如 `agentorchestra.governance.acl`）经 `_legacy.py` 仍可直接使用。

## 测试

仓库内治理相关用例较薄，位于 `tests/unit/test_governance.py`（当前仅覆盖 `TenantContext` 基础行为，ACL/Identity/CAS 属行为面有待补强）。

```bash
python -m pytest tests/unit/test_governance.py -v
# 或连同事务/租户一起跑
python -m pytest tests/unit -v
```

`pytest.ini` 已开启 `asyncio_mode = auto`，无需手工标注 async 用例。
