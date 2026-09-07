# Ontology 模块

> Agent 的业务语义模型与对象操作框架：用 `ObjectType/LinkType/Interface` 建模、用 `ActionType/Function` 定义写逻辑、落到 `ObjectStore/GraphStore`，由 `OntologyEngine` 统一装配（安全/审计/分支/查询/物化）并自动生成 Tool 挂载到任意 `ToolRegistry`。包路径即 `agentorchestra.ontology.*`。

## 设计动机与原则

1. **schema 先行，统一词汇**：数据建模先于使用。对象属性必须由 `ObjectType` 声明、链接必须在 `LinkType` 中定义；写入时校验主键/必填/类型并**拒绝未声明属性**（`VocabularyValidator` + `ObjectType.validate_object`），让 LLM 或上游写入无法随意造字段。
2. **属性声明复用工具体系**：属性一律用 `ToolParameter`，与 tools 模块同一套参数语言。对象类型、动作、函数因此能"零翻译"生成 Tool。
3. **读模型 / 写模型分层**：`semantic`（是什么）+ `kinetic`（能做什么/怎么写）+ `storage`（存哪里）+ `governance`（谁可以）+ `process`（怎么组合）分层，各自可独立演进。
4. **组合而非重新发明**：`ObjectStore` 组合 `ObjectIndex`（搜索/过滤/聚合）与 `GraphStore`（关系/路径遍历）；`QueryEngine` 在其上提供跨类型与跨链接查询。
5. **写操作默认防御**：派生属性不可直写、更新禁改主键、可选 CAS 乐观锁（version 不一致抛 `TxConflict`）、写前自动备份上下文（version/created_tx/last_modified_tx），安全默认拒绝（deny-by-default）。
6. **审计与物化是写路径的横切环节**：写操作可选 WAL 钩子（collect-and-flush 供 checkpoint 持久化）、写审计（`AuditManager`）、触发物化回写（`MaterializationManager`），失败都不阻断主流程。
7. **动作可编排、可补偿**：`ActionType` 能放进 `Workflow`（顺序/条件/并行 + 参数展开）、由 `Scheduler` 定时触发、由 `TransactionManager` 以 Saga 补偿或委托 `tx.TransactionCoordinator` 保证原子性。
8. **与 Agent 解耦，靠工具连接**：`engine.mount(registry)` 只依赖 `ToolRegistry`，不依赖任何 Agent 类型；对象/动作/函数生成的标准 Tool 一旦注册，任意 Agent 与子代理自动获得能力。

## 这样设计的好处

- 一份 `ObjectType` 定义同时服务：写入校验、查询 schema、LLM 工具描述（自动生成 QueryXxx / action / CallXxx 工具），一处声明处处复用。
- 用统一词汇 + 链接校验约束"幻觉字段/幻链接"，对象图保持语义一致，查询与推理（路径遍历、接口查询）可依赖图结构。
- 权限、审计、物化挂在写路径的固定钩子上，业务动作代码不需要自己重复实现这些关注点。
- 从内存演示到 SQLite 持久化只换一个 backend，代码路径不变。
- 把多步业务逻辑抬升为 workflow/saga 后，回滚、重试、参数依赖有框架支撑，而不是散落在动作里。

## 模块构成

| 路径 / 子模块 | 职责 | 主要公开导出 |
|---|---|---|
| `ontology/__init__.py` | 聚合导出（面向使用方） | 见下：engine/semantic/kinetic/storage/governance/process/query/tool 的合并导出 |
| `ontology/semantic/` | 语义层：建模 | `ObjectType`、`LinkType`、`Interface`、`VocabularyValidator` |
| `ontology/kinetic/` | 动能层：写逻辑 | `ActionType`、`Function`、`derived_property` |
| `ontology/storage/` | 存储层 | `ObjectStore`、`GraphStore`、`ObjectIndex`、`MaterializationManager`、`MaterializationTarget`、`BaseStorageBackend`、`MemoryBackend`、`SQLiteBackend`（backends.py 另提供别名 `StorageBackend`） |
| `ontology/governance/` | 治理层 | `SecurityContext`、`SecurityManager`、`PermissionRule`、`AuditManager`、`BranchManager`、`Branch`（security.py 另含 `RoleInheritance`） |
| `ontology/query_engine.py` | 跨对象查询 | `QueryEngine` |
| `ontology/process/` | 执行编排 | `WorkflowEngine`、`Workflow`、`StepNode`、`ConditionNode`、`ParallelNode`、`Scheduler`、`ScheduledTask`、`TransactionManager`、`CompensatingAction`（workflow.py 另含 `WorkflowNode`、`WorkflowParamExpansionError`） |
| `ontology/tool_generator.py` | 生成 Tool | `ToolGenerator`、`ObjectQueryTool`、`ObjectActionTool`、`FunctionCallTool` |
| `ontology/engine.py` | 统一入口 | `OntologyEngine` |

## 功能清单

### 1. 语义层（semantic/）

- **ObjectType**：构造 `ObjectType(api_name, primary_key, properties=[ToolParameter], link_types=[LinkType], display_name=None, description="", parent_type=None, derived_properties=None)`。
  - 属性注册表按名索引；`required_properties()`（必填且无默认值）、`writable_properties()`（排除派生属性）、`is_derived()`。
  - `validate_object(obj)`：校验主键存在、必填、类型（string/integer/number/boolean 严格，datetime/array/object 宽松）、以及**拒绝未声明属性**（豁免系统保留字段）；`unknown_properties(obj)` 单查未声明字段。
  - 类层次：`parent_type` + `is_subclass_of(other, type_registry)`（多级、防环）。
  - 系统字段常量 `ObjectType.SYSTEM_FIELDS = {"version","created_tx","last_modified_tx"}`（由 ObjectStore 自动维护）。
- **LinkType**：`LinkType(name, from_type, to_type, cardinality="ONE_TO_MANY", description="")`，方向化关系定义。
- **Interface**：`Interface(api_name, required_properties=[...], ...)`——对象类型的"形状多态"。`register_implementation(type_name)`、`check_implements(object_type)`（必需属性子集检查）。多个对象类型实现同一接口后即可被统一查询。
- **VocabularyValidator(object_types={})**：`validate_property(type, prop)`、`unknown_properties(type, obj)`、`validate_link(from_type, link, to_type)`（校验链接存在且两端类型匹配、支持子类继承）。

### 2. 动能层（kinetic/）

- **ActionType**：`ActionType(api_name, parameters=[ToolParameter], description="", execute_fn=None, rules=[...], side_effects=[...], display_name=None, idempotent=True)`。
  - `execute(params, ctx)` 五步：① 必填参数校验 → ② `rules`（每个 `rule(params, ctx)` 返回错误字符串或 None）→ ③ 调 `execute_fn(params, ctx)` → ④ `side_effects`（每个 `effect(result, ctx)`，副作用异常只记录不改变主结果）→ ⑤ 审计：优先写 `ctx["audit"]`（`AuditManager.log`），否则记入动作内部 `_audit`。
  - 返回统一 `{"success", "result", "errors"}`；执行后向 `telemetry.metrics` 记 `record_action_execution`。
  - `idempotent` 标记供重放/事务引擎判断幂等；`get_audit()` 读动作自身审计。
- **Function**：`Function(api_name, impl, arguments=[ToolParameter], return_type="string", ...)`；`call(args, ctx)` 调 `impl(args, ctx)`。比动作更轻，无规则/副作用/审计步骤，只读型逻辑常用。
- **derived_property(api_name, impl, property_type, description)`**：把"由对象现属性计算出的属性"声明为 Function（`impl(obj) -> value`）。配合 `ObjectType.add_derived_property`：派生属性不可被 ObjectStore 直接写入。

### 3. 存储层（storage/）

- **后端抽象（backends.py）**：`BaseStorageBackend`（`register_type/put/get/delete/all/types/close`）；`MemoryBackend`（默认）；`SQLiteBackend(db_path="memory/ontology.db")`（表 `objects(type,pk,data)`，WAL，`check_same_thread=False` + 锁，跨线程可用）。别名 `StorageBackend = BaseStorageBackend`。
- **ObjectIndex（index.py）**：`ObjectIndex(backend=...)` 负责读写与查询，维护内存反向索引。`search`（包含匹配，可选 `fields`）、`filter(conditions, operators)`（operators: eq/ne/gt/gte/lt/lte/contains）、`aggregate(group_by, agg=count|sum|avg|min|max)`、`count`。
- **GraphStore（graph_store.py）**：内存图。`merge_node(label, props, name=...)`、`add_relationship(subj, rel, obj, props)`、`remove_node`、`get_node`、`get_related(name, rel=None)`、`query_paths(start, rel, max_depth=3, target=None)`（BFS 支持传递推理，返回 `{"name","depth","path"}`）、`list_nodes/node_count/edge_count/clear`。
- **ObjectStore（object_store.py）**：组合 `index + graph`，是业务主要入口。
  - 类型注册 `register_type/get_type/list_types`；关闭 `close()`。
  - **写入**：`insert(type, obj)`（校验 → 拒绝派生直写 → 补默认值 → 可选注入 version=1/created_tx/last_modified_tx → 索引+图节点 `type:pk` 合并 → WAL emit → 物化 → 审计）；`update(type, pk, patch, expected_version=None)`（禁改主键、合并后重校验、CAS 版本不符抛 `governance.tx.context.TxConflict`、version 递增）；`delete`。
  - **读取**：`get/list_objects/search/filter/aggregate/count`。
  - **链接**：`create_link(from_type, from_pk, link_name, to_type, to_pk)`（校验两端存在 + 链接定义 + domain/range 子类匹配，`_validate_link`）；`get_links(from, pk, link_name=None)`；`query_links(..., max_depth=3)`（跨链接传递路径）。
  - **类层次**：`get_subclasses(type, transitive=True)` / `get_superclasses(type)`。
  - **治理钩子**：`configure_governance(audit=AuditManager, audit_backend=...)`（审计落 `AuditManager.log`，principal 取自 `governance.identity.current_principal()`，兜底 anonymous）；`set_wal_thread_id` + `drain_wal`/`pending_wal_count`（collect-and-flush WAL 供 Agent checkpoint 周期刷盘）；`set_tx_context(tx_id)`；`stats()`。
  - `backend_type` 属性表明当前后端（memory/sqlite）。
- **物化（materialization.py）**：`MaterializationTarget(name, write_fn)`（`write_fn(operation, type_name, obj, patch) -> bool`，operation=insert/update/delete）；`to_tx_action()` 把回写包装成 `governance.tx.context.TxAction`（Saga 补偿方向：insert↔delete、update 用 before 复原）。`MaterializationManager` 注册/执行目标、`get_log/clear_log`。

### 4. 治理层（governance/）

- **Security**：`SecurityContext(principal="anonymous", roles=[], groups=[], attributes={})`（`has_role/has_any_role/in_group`）；`PermissionRule(resource, action, roles, conditions=None, field_pattern=None)`（resource 支持 glob，action 支持 `*`，conditions 支持 ABAC 值比较与 `regex:` 前缀，field_pattern 做字段级放行）；`RoleInheritance`（`add_inheritance/get_parents/get_effective_roles`）；`SecurityManager`（**deny-by-default**：无规则则拒绝）。
  - `allow(roles, resource="*", action="*", conditions=None, field_pattern=None)` 便捷授权；`inherit(child, parent)`；`check(resource, action, ctx, field=None)`。
  - 开放模式：`SecurityManager(open_mode=True)` / `set_open_mode(True)` 仅在环境变量 `AGENTORCHESTRA_ALLOW_OPEN_MODE=1` 时放行，否则拒绝并告警——生产默认禁止。
- **Audit**：`AuditManager.log(principal, resource, action, detail=None, success=True)`；`attach_backend(store)` 支持 WORM（追加到 `orchestration.state.records.AuditEntry` 的异步 append，`clear()` 只清内存不删后端行）；`query(principal=None, resource=None, limit=100)`、`count()`。
- **Branch**：`BranchManager` 对 ObjectStore 做快照式分支。`create_branch(name, store)`（快照当前对象）、`list_branches/get_active/switch_to(name, store)`（回滚 store 到快照）、`merge_to(name, store)`（注意：恢复分支快照到 main，语义近"回滚"而非 diff 合并）、`delete_branch`。

### 5. QueryEngine（query_engine.py）

- `query_interface(interface, conditions=None, limit=50)`：按接口聚合各实现类型的对象 `{type: [objects]}`。
- `navigate_links(from_type, from_pk, link_name, max_depth=3)`：沿链接多跳导航。
- `object_set(type_name, conditions=None, sort_by=None, descending=False, limit=50, offset=0)`：统一"过滤+排序+分页"返回 `{"total","offset","limit","objects"}`。
- `describe_join(type_a, link_name, type_b, conditions_a=None)`：对象 join（A 满足条件后沿链接取关联 B）。

### 6. 执行编排（process/）

- **Workflow（workflow.py）**：节点三类——`StepNode(node_id, action_name, params, depends_on=[...], max_retries=0)`、`ConditionNode(node_id, condition_fn, if_true, if_false=None)`、`ParallelNode(node_id, branches)`。`Workflow.add_node(node, entry=False)`、`validate()`（引用有效性 + DFS 循环依赖检测，只算 Step/Parallel 的真实依赖）。
  - `WorkflowEngine(actions={})`：`register_action/register_workflow`（注册前先校验，非法抛 ValueError）、`get_workflow/list_workflows`、`run(name, initial_params=None, ctx=None)` 返回 `{"success","results":{node_id:...},"errors","started_at","ended_at"}`，执行历史 `get_runs(name=None, limit=20)`。
  - 参数展开 `_expand_params`：`"$key"` 取初始参数、`"$node_id.field"` 取前置节点结果；**只允许引用已声明 `depends_on` 的节点**，违规在 strict 模式（默认）抛 `WorkflowParamExpansionError`，宽松模式置 None 并告警。
- **Scheduler（scheduler.py）**：`Scheduler(tick_seconds=1.0)` 后台线程。`add_interval(name, target, interval_seconds, params, max_runs=None)`（interval 首跑立即执行）；`add_once(name, target, delay_seconds, params)`（延迟单次）。`remove_task/list_tasks/start/stop/is_running/run_once_now`。`ScheduledTask` 记录 `run_count/last_run_at/last_result/last_error`。
- **Transaction（transaction.py）**：`CompensatingAction(name, action_fn, compensate_fn)`；`TransactionManager` 注册 `register_action`/`register`、执行 `execute(steps=[{"action","params"}], ctx=None)` 返回 `{"success","completed","failed","compensated","errors"}`。
  - 默认路径是**纯内存 Saga**：正序执行，失败后逆序补偿；无补偿函数的动作回滚时报错记录。
  - 可选 `set_coordinator(coordinator)` 委托 `governance.tx.TransactionCoordinator`（幂等 + WAL + 补偿 + DLQ）。
  - 保存点：`savepoint(name, store)` / `rollback_to(savepoint, store)`；日志 `get_log/clear_log`。

### 7. Tool 自动生成（tool_generator.py）

- `ObjectQueryTool(object_type, store, security=None, security_ctx=None, audit=None, query_engine=None)`：工具名 `Query<Type>`（Customer → `QueryCustomer`），`read_only=True`。参数 `mode`（get/search/filter/list/aggregate/links）+ 各模式字段；执行前查权限（`security.check(type,"read",ctx)`），拒绝则审计并返回 `ACCESS_DENIED`。`conditions` 强制为 dict-of-primitive（递归校验 key 必须为 str），防恶意 JSON 逃逸 schema。
- `ObjectActionTool(action, store, security, security_ctx, audit)`：工具名 = `action.api_name`；执行前查 `write` 权限；`run` 把 `{"object_store", "security", "principal", "audit"}` 注入 ctx 后调 `action.execute`。
- `FunctionCallTool(function, store=None)`：工具名 `Call<Camel>`（compute_order_total → `CallComputeOrderTotal`），`read_only=True`；失败返回 `EXECUTION_ERROR`。
- `ToolGenerator(store, security, security_ctx, audit, query_engine)`：三个工厂 `object_query_tool/action_tool/function_tool`。

### 8. OntologyEngine（engine.py）——统一入口

构造 `OntologyEngine(object_store=None, security_ctx=None)`；内部自建并持有 `security/audit/branching/materialization`、`object_store`（含 graph_store）、`query`、`workflow`（`WorkflowEngine(self.actions)`）、`scheduler`、`transaction`、`vocabulary`、`tool_generator`。

- 注册：`register_object_type`（同步注册到 ObjectStore 与 VocabularyValidator）、`register_action`（同步到 workflow）、`register_function`、`register_interface`、`implement_interface(interface_name, type_name)`（属性不满足报错）。
- 统一词汇：`validate_triple(from, link, to)`、`unknown_properties(type, obj)`；类层次：`get_subclasses/get_superclasses`。
- 治理：`allow(roles, resource="*", action="*")`、`register_materialization(target)`、`snapshot_branch(name)`、`switch_branch(name)`。
- 挂载：`build_tools()`（每个对象类型→Query 工具、动作→动作工具、函数→Call 工具）；`mount(registry: ToolRegistry) -> List[str]` 注册并返回工具名列表。
- 运维：`describe()`、`stats()`、`get_health()`（对象类型/动作/函数/存储检查）。

## 使用说明

### import 路径

```python
# 顶层聚合（推荐，真实导出见 ontology/__init__.py）
from agentorchestra.ontology import (
    OntologyEngine, ObjectType, LinkType, Interface, VocabularyValidator,
    ActionType, Function, derived_property,
    ObjectStore, GraphStore, ObjectIndex, MaterializationManager, MaterializationTarget,
    MemoryBackend, SQLiteBackend, BaseStorageBackend,
    SecurityContext, SecurityManager, PermissionRule, AuditManager, BranchManager,
    QueryEngine,
    WorkflowEngine, Workflow, StepNode, ConditionNode, ParallelNode,
    Scheduler, ScheduledTask, TransactionManager, CompensatingAction,
    ToolGenerator, ObjectQueryTool, ObjectActionTool, FunctionCallTool,
)

# 或按子包导入（与上面同一批类）
from agentorchestra.ontology.semantic import ObjectType, LinkType, Interface, VocabularyValidator
from agentorchestra.ontology.kinetic import ActionType, Function, derived_property
from agentorchestra.ontology.storage import ObjectStore, GraphStore, MemoryBackend, SQLiteBackend
from agentorchestra.ontology.governance import SecurityContext, SecurityManager, AuditManager, BranchManager
from agentorchestra.ontology.process import TransactionManager, Workflow, WorkflowEngine

# 参数类型来自工具模块
from agentorchestra.capability.tools.base import ToolParameter
from agentorchestra.capability.tools import ToolRegistry
```

### 场景 1：建模 + 内存存储 + 查询 + 自动生成工具（离线可跑）

```python
def P(name, type="string", required=True, description=""):
    return ToolParameter(name=name, type=type, required=required, description=description)

# 1) schema 先行
customer = ObjectType("Customer", primary_key="id",
                      properties=[P("id"), P("name")])
order = ObjectType("Order", primary_key="order_id",
                   properties=[P("order_id"), P("customer_id"), P("total", "number")])
order.add_link_type(LinkType("placed_by", from_type="Order", to_type="Customer"))

interface = Interface("Named", required_properties=["name"])

# 2) 动能：函数（只读计算）与动作（写）
def order_total(params, ctx):
    items = params.get("items", [])
    return sum(i["price"] * i["qty"] for i in items)

compute_total = Function("compute_order_total", impl=order_total,
                         arguments=[P("items", "array", description="商品数组")],
                         return_type="number")

def create_order(params, ctx):
    return {"order_id": params["order_id"], "customer_id": params["customer_id"]}

create_order_action = ActionType(
    "create_order",
    parameters=[P("order_id"), P("customer_id")],
    execute_fn=create_order,
)

# 3) 引擎装配（安全默认拒绝 → 显式授权）
ctx = SecurityContext(principal="agent-demo", roles=["read_only", "write"])
engine = OntologyEngine(object_store=ObjectStore(graph=GraphStore()), security_ctx=ctx)
engine.allow(["read_only"], resource="*", action="read")
engine.allow(["write"], resource="*", action="write")

engine.register_object_type(customer)
engine.register_object_type(order)
engine.register_interface(interface)
engine.implement_interface("Named", "Customer")     # 属性不满足会抛 ValueError
engine.register_function(compute_total)
engine.register_action(create_order_action)

# 4) 写对象 + 建链接
store = engine.object_store
store.insert("Customer", {"id": "C1", "name": "张三"})
store.insert("Order", {"order_id": "O1", "customer_id": "C1", "total": 100})
store.create_link("Order", "O1", "placed_by", "Customer", "C1")

print(store.get("Customer", "C1"))          # 含自动注入的 version/created_tx/last_modified_tx
print(store.get_links("Order", "O1"))       # 直接关联
print(store.query_links("Order", "O1", "placed_by"))  # 传递路径
print(store.filter("Order", {"customer_id": "C1"}))
print(engine.validate_triple("Order", "placed_by", "Customer"))  # True
print(engine.unknown_properties("Order", {"order_id": "O1", "nope": 1}))  # ['nope']

# 5) 挂载为工具，通过统一执行入口调用
registry = ToolRegistry()
print(engine.mount(registry))               # ['QueryCustomer', 'QueryOrder', 'create_order', 'CallComputeOrderTotal']
resp = registry.execute_tool("QueryCustomer", {"mode": "get", "pk": "C1"})
print(resp.status.value, resp.data)
```

### 场景 2：切换 SQLite 持久化后端

```python
from agentorchestra.ontology import ObjectStore, GraphStore, SQLiteBackend, OntologyEngine, SecurityContext

object_store = ObjectStore(graph=GraphStore(), backend=SQLiteBackend(db_path="memory/ontology.db"))
engine = OntologyEngine(object_store=object_store, security_ctx=SecurityContext(principal="agent"))
# 其余建模/注册/查询代码与场景 1 相同；对象数据持久化到 SQLite，
# 注意 GraphStore 仍是进程内存，重启后对象仍在、图需重新建链。
```

### 场景 3：权限演示（deny-by-default）

```python
from agentorchestra.ontology.governance import SecurityManager, SecurityContext

manager = SecurityManager()                       # 默认无规则 → 全部拒绝
alice = SecurityContext(principal="alice", roles=["editor"])

print(manager.check("Order", "write", alice))     # False（未授权）
manager.allow(["editor"], resource="Order", action="write")
print(manager.check("Order", "write", alice))     # True
print(manager.check("Customer", "write", alice))  # False（资源不匹配，仍是默认拒绝）
manager.allow(["editor"], resource="*", action="read")
print(manager.check("Customer", "read", alice))   # True（glob 通配）
```

> ObjectQueryTool / ObjectActionTool 在生成时被注入同一个 `SecurityManager` 与 `SecurityContext`，每次工具执行都会先 `security.check(resource, action, ctx)`：`Query<Type>` 检查 `read`、动作工具检查 `write`，被拒返回 `ACCESS_DENIED` 并写审计。

### 场景 4：工作流编排与事务补偿

```python
from agentorchestra.capability.tools.base import ToolParameter
from agentorchestra.ontology import ActionType, SecurityContext, OntologyEngine
from agentorchestra.ontology.process import Workflow, StepNode, WorkflowEngine, TransactionManager

def echo(params, ctx):
    return dict(params)

engine = OntologyEngine(security_ctx=SecurityContext(principal="p", roles=["r"]))
engine.register_action(ActionType("echo", execute_fn=echo))

wf = Workflow("wf", description="顺序示例")
wf.add_node(StepNode("s1", "echo", {"a": "$a"}), entry=True)
wf.add_node(StepNode("s2", "echo", {"b": "$s1.a"}, depends_on=["s1"]))   # $s1.a 引用必须先声明依赖

wfe = WorkflowEngine(engine.actions)
wfe.register_workflow(wf)
run = wfe.run("wf", initial_params={"a": 10})
print(run["success"], run["results"])

# 事务：正序执行、失败逆序补偿
def reserve(params, ctx):
    return "reserved"
def undo_reserve(params, ctx):
    return None
def pay(params, ctx):
    raise RuntimeError("pay failed")

tm = TransactionManager()
tm.register("reserve", reserve, undo_reserve)
tm.register("pay", pay, None)          # 无补偿 → 回滚时记入 errors
res = tm.execute([{"action": "reserve", "params": {}}, {"action": "pay", "params": {}}])
print(res["success"], res["failed"], res["compensated"])   # False pay ['reserve']
```

### 关键配置（Config `ontology.*` 子配置，供 Agent 运行时自动装配）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `ontology_engine_enabled` | `False` | OntologyCapability 开关（Agent 装配时自动 mount） |
| `ontology_engine_module` | `""` | 自定义装配模块名（须暴露 `build_engine()`），为空走内置装配 |
| `ontology_default_principal` | `agent` | 内置装配的默认 principal |
| `ontology_default_roles` | `[]` | 默认角色列表 |
| `ontology_backend` | `memory` | `memory` / `sqlite`（内置装配选择） |
| `ontology_db_path` | `memory/ontology.db` | SQLite 路径 |

## 与其他模块的关系

- **capability.tools**：本体属性/动作参数复用 `tools.base.ToolParameter`；`ToolGenerator` 产物继承 `tools.base.Tool` 并用 `tools.errors.ToolErrorCode`/`tools.response.ToolResponse`；`engine.mount()` 写入 `tools.registry.ToolRegistry`。
- **governance.tx**：`ObjectStore.update` 的 CAS 冲突抛 `governance.tx.context.TxConflict`；`MaterializationTarget.to_tx_action()` 返回 `TxAction`（Saga 补偿描述）；`TransactionManager` 可选委托 `tx.TransactionCoordinator`。
- **governance.tenancy / govern**：`ObjectStore._current_principal` 读 `governance.identity.current_principal()`（兜底 anonymous）做审计主体；审计 WORM 后端落在 `orchestration.state.records.AuditEntry`。
- **runtime.core.telemetry.metrics**：`ActionType.execute` 完成后 `record_action_execution(api_name, error=...)`。
- **runtime.capabilities.builtins**：`OntologyCapability` 按配置用 `MemoryBackend/SQLiteBackend + GraphStore + ObjectStore` 构造引擎并 `engine.mount(ctx.tool_registry)`，随后 Agent 即具备 Query/Action/Call 工具。
- **runtime.core.agent.base**：Agent 持有 `self.ontology_engine`（来自 capability state），可直读 `object_store`。

## 测试

```bash
# 仓库现状：tests/unit 下暂无 ontology 专项单测文件。
# 与之最相关的是 tools 侧单测（挂载后走 ToolRegistry 协议）：
python -m pytest tests/unit/test_tools.py -v

# 冒烟验证（建模/写入/链接/挂载/执行，全内存、无外部依赖）：
python -c "from agentorchestra.capability.tools.base import ToolParameter; from agentorchestra.capability.tools import ToolRegistry; from agentorchestra.ontology import *; import inspect; print(OntologyEngine, ObjectType, ActionType, SecurityContext)"
```

把上方"场景 1"存为脚本执行即可得到完整端到端输出。若需要落地专项覆盖，可参照 `tests/unit/test_tools.py` 风格新建 `tests/unit/test_ontology.py`（建议全部使用 `MemoryBackend` + `GraphStore`，覆盖：词汇校验拒绝未知字段、链接 domain/range 校验、CAS version 冲突、权限 deny、mount 后工具执行与审计条目数）。
