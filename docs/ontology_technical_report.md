# Ontology 企业级本体技术报告

> **版本**: 0.2.0  
> **模块**: `agentorchestra.ontology`  
> **核心价值**: 把业务语义建模为可执行的对象图

---

## 1. 模块定位

Ontology 是 Symphony 框架的**业务语义层**，核心职责：

- 把数据源（数据库/API）映射为**业务对象**（Customer / Order / Device）
- 定义**对象间关系**（订单属于客户、设备归属车间）
- 提供**写操作能力**（创建订单、扣库存）
- 支持**时间旅行**（分支快照回滚）
- 与 Agent **深度集成**（暴露为工具）

## 2. 六层架构

```
┌──────────────────────────────────────────┐
│  6. Agent 集成 (engine.mount)           │  ← LLM 可调用的工具
├──────────────────────────────────────────┤
│  5. 治理层 (security / audit / branch)   │  ← RBAC/审计/时间旅行
├──────────────────────────────────────────┤
│  4. 流程层 (workflow / transaction / scheduler) │  ← 编排/补偿/定时
├──────────────────────────────────────────┤
│  3. 存储层 (object_store / graph_store)  │  ← 索引+图查询
├──────────────────────────────────────────┤
│  2. 动能层 (action / function)           │  ← 写操作/函数
├──────────────────────────────────────────┤
│  1. 语义层 (object_type / link / interface) │  ← 业务对象定义
└──────────────────────────────────────────┘
```

---

## 3. 语义层（Semantic）

### 3.1 ObjectType - 对象类型

**职责**: 定义业务对象的结构、约束、关系

```python
class ObjectType:
    api_name: str                # API 标识（如 "customer"）
    primary_key: str              # 主键字段名
    properties: Dict[ToolParameter]  # 属性定义
    link_types: Dict[LinkType]    # 关系定义
    parent_type: Optional[str]    # 父类型（类层次）
    derived_properties: Set[str]  # 派生属性（值由 Function 计算）
```

**代码解析** (`semantic/object_type.py:17`)：

```python
class ObjectType:
    SYSTEM_FIELDS = {"version", "created_tx", "last_modified_tx"}
    # ↑ 由 ObjectStore 自动注入/维护，不属于业务属性

    def __init__(self, api_name, primary_key, properties, link_types,
                 display_name, description, parent_type, derived_properties):
        self.api_name = api_name
        self.primary_key = primary_key
        # 属性索引（O(1) 查找）
        self.properties: Dict[str, ToolParameter] = {}
        if properties:
            for p in properties:
                self.properties[p.name] = p
        # 链接索引
        self.link_types: Dict[str, LinkType] = {}
        if link_types:
            for link in link_types:
                self.link_types[link.name] = link
```

**关键能力**:

1. **统一词汇校验** - 拒绝未声明的属性，避免数据漂移
2. **类型校验** - 属性类型自动检查（string/number/boolean）
3. **必填校验** - required=True 的属性必须有值
4. **派生属性** - 标记为 derived 的属性不可直接写
5. **类层次** - parent_type 支持继承，子类继承父类属性

```python
def validate_object(self, obj):
    errors = []
    # 主键校验
    if self.primary_key not in obj or obj[self.primary_key] in (None, ""):
        errors.append(f"缺少主键: {self.primary_key}")
    # 必填校验
    for p in self.required_properties():
        if p.name not in obj or obj[p.name] in (None, ""):
            errors.append(f"缺少必填属性: {p.name}")
    # 类型校验
    for p in self.get_properties():
        if p.name in obj and obj[p.name] is not None:
            if not self._valid_type(p.type, obj[p.name]):
                errors.append(f"属性 '{p.name}' 类型错误")
    # 统一词汇强制
    for key in obj:
        if key not in self.properties and key not in self.SYSTEM_FIELDS:
            errors.append(f"属性 '{key}' 未在对象类型中定义")
    return errors
```

### 3.2 LinkType - 链接类型

```python
class LinkType:
    name: str          # 链接名（如 "belongs_to"）
    from_type: str     # 源对象类型
    to_type: str       # 目标对象类型
```

**关系模式**:
- 一对多：订单 belongs_to 客户
- 多对多：学生 takes 课程
- 自引用：员工 manages 员工

**链接校验**（在 ObjectStore.create_link 中）：
```python
def _validate_link(self, from_type, link_name, to_type):
    link = from_type_def.get_link_type(link_name)
    if not link:
        raise ValueError(f"链接 '{link_name}' 未定义")
    # 端类型校验（支持子类继承）
    if link.from_type != from_type:
        if not (from_type_def.is_subclass_of(link.from_type, self._types)
                or link.from_type == from_type):
            raise ValueError(...)
```

### 3.3 Interface - 接口定义

```python
class Interface:
    name: str
    required_properties: List[str]  # 必须的属性
    required_actions: List[str]    # 必须的动作
```

**作用**: 类型合约（如 "Payable" 接口要求有 amount + status 属性）

```python
PayableIface = Interface("payable",
                          required_properties=["amount", "status"])
engine.register_interface(PayableIface)
engine.implement_interface("payable", "order")  # Order 实现 Payable
```

---

## 4. 动能层（Kinetic）

### 4.1 ActionType - 动作类型

**职责**: 封装组织的写操作（创建订单、扣库存）

```python
class ActionType:
    api_name: str
    parameters: Dict[ToolParameter]
    rules: List[Callable]          # 提交前校验
    execute_fn: Callable            # 实际执行
    side_effects: List[Callable]    # 后置副作用
    idempotent: bool                # 是否可重放
```

**执行流程** (`kinetic/action.py:54`)：

```
参数校验 → 规则检查 → 执行 → 副作用 → 审计
   ↓         ↓         ↓      ↓      ↓
 必填/类型  业务规则   主体   webhook  audit log
```

```python
def execute(self, params, ctx):
    errors = []
    # ① 参数校验
    for p in self.parameters.values():
        if p.required and (p.name not in params or params[p.name] in (None, "")):
            errors.append(f"缺少必填参数: {p.name}")
    # ② 规则校验（如金额必须为正）
    if not errors:
        for rule in self.rules:
            rule_error = rule(params, ctx)
            if rule_error:
                errors.append(str(rule_error))
    if errors:
        return {"success": False, "errors": errors}
    # ③ 执行主体
    result = self.execute_fn(params, ctx)
    # ④ 副作用（webhook/通知/调度触发）
    for effect in self.side_effects:
        effect(result, ctx)
    # ⑤ 审计
    self._record_audit(params, ctx, success=True, errors=errors)
    return {"success": True, "result": result}
```

**示例**:
```python
def check_amount(params, ctx):
    if params.get("amount", 0) <= 0:
        return "金额必须为正"
    return None

def do_create_order(params, ctx):
    return ctx["object_store"].insert("order", params)

CreateOrder = ActionType(
    "create_order",
    parameters=[ToolParameter("order_id", "string", required=True), ...],
    rules=[check_amount],
    execute_fn=do_create_order,
)
```

### 4.2 Function - 函数定义

```python
class Function:
    name: str
    impl: Callable
    arguments: List[ToolParameter]
```

**与 ActionType 的区别**:
- ActionType: 有审计/副作用/规则
- Function: 纯计算，无副作用

**示例**:
```python
def compute_total(args, ctx):
    return {"with_tax": round(args.get("amount", 0) * 1.13, 2)}

ComputeTotal = Function("compute_order_total",
                       impl=compute_total,
                       arguments=[ToolParameter("amount", "number")])
```

---

## 5. 存储层（Storage）

### 5.1 ObjectStore - 对象存储

**职责**: 组合索引（查询）+ 图（关系）+ 身份（乐观锁）+ 审计

**架构**:
```
ObjectStore
├── index: ObjectIndex      # 内存/SQLite 索引
├── graph: GraphStore       # 关系图
├── materializer: 可选回写
├── wal_hook: 可选 WAL 钩子
└── audit: 可选审计管理器
```

**关键方法** (`storage/object_store.py:172`)：

```python
def insert(self, type_name, obj):
    # ① 类型校验
    errors = obj_type.validate_object(obj)
    if errors:
        raise ValueError(f"对象校验失败: {errors}")
    # ② 拒绝写入派生属性
    derived_written = [p for p in obj if obj_type.is_derived(p)]
    if derived_written:
        raise ValueError(f"派生属性不可直接写入: {derived_written}")
    # ③ 注入对象身份（M3）
    if self.enable_object_identity:
        tx = self._tx_context or "none"
        obj["version"] = 1
        obj["created_tx"] = tx
        obj["last_modified_tx"] = tx
    # ④ 多重持久化（索引+图+WAL+审计）
    self.index.index_object(type_name, pk, obj)
    self.graph.merge_node(...)
    self._wal_emit("state_update", {...})
    self._materialize("insert", type_name, dict(obj))
    self._audit_write("insert", type_name, pk)
```

**CAS 乐观锁**（`update` 方法）:
```python
def update(self, type_name, pk, patch, expected_version=None):
    current = self.index.get(type_name, pk)
    if expected_version is not None:
        cur_ver = int(current.get("version", 0))
        if cur_ver != expected_version:
            raise TxConflict(f"CAS 冲突...")
    # ... 合并 + 重新校验 + version+1
```

### 5.2 GraphStore - 图存储

**内存图**: 节点 + 关系，支持 BFS 路径查询

```python
class GraphStore:
    _nodes: Dict[str, Dict]      # name -> {label, props}
    _edges: Dict[str, List[Dict]]  # name -> [{rel, target, props}]
```

**核心操作**:
```python
def merge_node(self, label, properties, name=None):
    # 存在则更新，不存在则创建
    ...

def query_paths(self, start, rel, max_depth=3, target=None):
    # BFS 路径查询（支持传递推理）
    visited = {start}
    queue = [(start, 0, [start])]
    while queue:
        current, depth, path = queue.pop(0)
        for edge in self._edges.get(current, []):
            if edge["rel"] != rel:
                continue
            t = edge["target"]
            if t not in visited:
                visited.add(t)
                results.append({"name": t, "depth": depth+1, "path": path+[t]})
                queue.append((t, depth+1, path+[t]))
```

---

## 6. 流程层（Process）

### 6.1 Workflow - 工作流

**职责**: 节点编排 + 拓扑执行

```python
class Workflow:
    def add_node(self, step: StepNode, entry=False):
        ...
    
    def run(self, name, ctx):
        # 拓扑排序 → 顺序执行
        ...
```

### 6.2 Transaction - 事务补偿

**Saga 模式**: 正向执行 + 失败逆序补偿

```python
class Transaction:
    def register(self, name, forward_fn, compensate_fn):
        ...
    
    async def execute(self, actions):
        # 正向 + 失败补偿
        ...
```

### 6.3 Scheduler - 定时任务

```python
class Scheduler:
    def add_interval(self, name, fn, interval_seconds, max_runs=None):
        # 定时执行
        ...
```

---

## 7. 治理层（Governance）

### 7.1 SecurityManager - 权限

**支持**:
- 角色继承（`inherit(child, parent)`）
- 资源模式匹配（glob，如 `order:*`）
- 字段级权限（`field_pattern`）
- ABAC 条件（`conditions`）

### 7.2 Branching - 时间旅行

```python
class Branching:
    def snapshot(self, name):
        # 创建分支快照
        ...
    
    def switch(self, name):
        # 切换分支
        ...
```

### 7.3 QueryEngine - 查询引擎

```python
class QueryEngine:
    def object_set(self, type, conditions=None, limit=50):
        # 条件查询
        ...
    
    def navigate(self, obj_id, edge_type, depth=1):
        # 图遍历
        ...
    
    def describe_join(self, from_type, link_name, to_type):
        # 连接描述
        ...
```

---

## 8. 与 Agent 集成

### 8.1 自动暴露为工具

```python
engine = OntologyEngine(...)
engine.register_object_type(Customer)
engine.register_action(CreateOrder)
engine.register_function(ComputeTotal)

# 自动生成：
#   - Query<Type>: 查询对象
#   - create_<ActionType>: 执行动作
#   - Call<Function>: 调用函数
mounted = engine.mount(registry)
# ['QueryCustomer', 'QueryOrder', 'create_order', 'CallComputeOrderTotal']
```

### 8.2 集成流程

```
LLM 决定调用工具
       ↓
ToolRegistry.execute_tool(name, args)
       ↓
OntologyToolAdapter → engine.query / engine.action.execute
       ↓
ObjectStore + ActionType + Function
       ↓
结果返回 LLM
```

---

## 9. 改进建议

### 9.1 性能优化

- **批量操作**: 当前 insert/update 逐条，可加 batch API
- **延迟加载**: 派生属性按需计算
- **索引优化**: 添加复合索引

### 9.2 功能增强

- **GraphQL 集成**: 自动从 ObjectType 生成 schema
- **Schema 迁移**: 版本化 + 自动迁移
- **多语言支持**: 错误信息 i18n

### 9.3 可观测性

- **操作追踪**: 所有 insert/update/delete 自动 emit trace
- **审计增强**: 支持按时间/主体/资源的多维查询
- **WORM 合规**: 审计日志防篡改

---

## 10. 附录：核心 API 速查

```python
# 类型注册
engine.register_object_type(ObjectType(...))
engine.register_action(ActionType(...))
engine.register_function(Function(...))
engine.register_interface(Interface(...))
engine.implement_interface("payable", "order")

# 对象操作
store.insert("customer", {"customer_id": "c1", "name": "张三"})
store.update("customer", "c1", {"name": "李四"}, expected_version=1)
store.delete("customer", "c1")

# 查询
store.get("customer", "c1")
store.filter("order", {"status": "pending"})
store.aggregate("order", "customer_id", "sum", "amount")
qe.object_set("order", conditions={"status": "pending"})
qe.describe_join("order", "belongs_to", "customer")

# 关系
store.create_link("order", "o1", "belongs_to", "customer", "c1")
store.query_links("customer", "c1", "owns")
store.get_subclasses("customer")

# 工作流
wf = Workflow("order_flow")
wf.add_node(StepNode("validate", "check_order", {...}), entry=True)
engine.workflow.register_workflow(wf)

# 事务
engine.transaction.register("扣库存", forward_fn, compensate_fn)
result = engine.transaction.execute([...])

# 权限
engine.allow(["admin"], "order:*", "write")
engine.allow(["viewer"], "customer:*", "read")
engine.security.check("order", "write", ctx)

# Agent 集成
engine.mount(registry)  # 自动注册所有工具
```

---

**报告结束**。Ontology 模块作为 Symphony 的业务语义层，提供了完整的对象建模、关系管理、事务补偿和工作流编排能力，是企业级应用的核心基础设施。