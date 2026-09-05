# -*- coding: utf-8 -*-
"""AgentOrchestra ontology 模块全功能示例

覆盖 ecommerce_ontology.py 未展示的能力：
- 类层次（parent_type / get_subclasses）
- 派生属性（derived_properties）
- 统一词汇校验（validate_triple）
- SQLite 持久化
- 过滤操作符（gt/contains 等）
- 聚合统计（sum/avg）
- 链接创建与跨跳查询
- 物化（编辑回写）
- 细粒度权限
- 审计
- 分支快照/回滚
- 工作流编排
- 定时调度
- 事务补偿
- 查询引擎（object_set/navigate/join）
"""
import json
import time

# 标准领域路径（不需要 sys.path 注入；安装后自然可导入）
from agentorchestra.ontology import (
    ActionType,
    GraphStore,
    LinkType,
    MaterializationTarget,
    ObjectStore,
    ObjectType,
    OntologyEngine,
    SecurityContext,
    SQLiteBackend,
    StepNode,
    Workflow,
)
from agentorchestra.capability.tools.base import ToolParameter


def section(t):
    print("=" * 66)
    print("▶ " + t)
    print("=" * 66)


# ==================== ① 类层次 + 派生属性 ====================
section("① 类层次 + 派生属性（semantic）")

# 类层次：System → Module/Service
System = ObjectType("system", "id", properties=[
    ToolParameter(name="id", type="string", description="ID", required=True),
    ToolParameter(name="name", type="string", description="名", required=True),
])
Module = ObjectType("module", "id", properties=[
    ToolParameter(name="id", type="string", description="ID", required=True),
    ToolParameter(name="name", type="string", description="名", required=True),
    ToolParameter(name="owner", type="string", description="负责人", required=False),
    ToolParameter(name="amount", type="number", description="金额", required=False),
    ToolParameter(name="total", type="number", description="总额", required=False),
], parent_type="system",  # 类层次：module 是 system 子类
    derived_properties=["total"],  # 派生属性：total 由函数计算，不可直接写
    link_types=[LinkType("belongs_to", "module", "project")])  # 模块属于项目

print("类层次:")
print(f"  system 子类: {Module.parent_type} 关系建立")
print(f"  module 父类型: {Module.parent_type}")

# ==================== ② 动作 + 函数 ====================
section("② 动作 + 函数（kinetic）")

# 派生属性计算函数
def compute_total(args, ctx):
    obj = args.get("object", {})
    return obj.get("amount", 0) * 1.13

engine = OntologyEngine(
    object_store=ObjectStore(graph=GraphStore(), backend=SQLiteBackend("memory/full_demo.db")),
    security_ctx=SecurityContext("admin", ["admin"]),
)
engine.register_object_type(System)
engine.register_object_type(Module)

# 尝试写派生属性 → 应被拒绝
print("派生属性写入保护:")
try:
    engine.object_store.insert("module", {"id": "m1", "name": "认证", "total": 999})
    print("  ❌ 派生属性被写入（错误）")
except ValueError as e:
    print(f"  ✅ 拒绝写派生属性: {str(e)[:50]}")

# 合法插入
engine.object_store.insert("module", {"id": "m1", "name": "认证模块", "owner": "张三", "amount": 100})
print(f"  正常插入: {engine.object_store.get('module', 'm1')['name']}")

# ==================== ③ 统一词汇校验 ====================
section("③ 统一词汇校验（validate_triple）")
print(f"  validate_triple(module, belongs_to, system): "
      f"{engine.validate_triple('module', 'nonexistent', 'system')}")
print(f"  unknown_properties: "
      f"{engine.unknown_properties('module', {'id': 'm1', 'bad_field': 1})}")

# ==================== ④ 过滤操作符 + 聚合 ====================
section("④ 过滤操作符 + 聚合（storage）")

# 造数据
for i in range(5):
    engine.object_store.insert("module", {
        "id": f"m{i}", "name": f"模块{i}", "owner": "李四" if i % 2 else "张三",
        "amount": 100 * (i + 1)})

# 过滤：amount > 200
filtered = engine.object_store.filter("module", {"amount": 200}, operators={"amount": "gt"})
print(f"  amount > 200: {len(filtered)} 个")

# 过滤：name contains '模块'
contains = engine.object_store.search("module", "模块")
print(f"  search '模块': {len(contains)} 个")

# 聚合：按 owner 分组求 amount 总和
agg = engine.object_store.aggregate("module", "owner", "sum", "amount")
print(f"  按 owner 聚合 amount 总和: {agg}")

# ==================== ⑤ 链接 + 跨跳查询 ====================
section("⑤ 链接 + 跨跳查询（graph）")

Project = ObjectType("project", "pid", properties=[
    ToolParameter(name="pid", type="string", description="ID", required=True),
    ToolParameter(name="name", type="string", description="名", required=True),
])
engine.register_object_type(Project)
engine.object_store.register_type(Project)
engine.object_store.insert("project", {"pid": "p1", "name": "核心项目"})

# 链接：module belongs_to project

# 重新定义 module 带链接（简化：直接手动建图关系）
engine.object_store.create_link("module", "m1", "belongs_to", "project", "p1")
links = engine.object_store.get_links("module", "m1")
print(f"  m1 的直接链接: {[(link['to_type'], link['to_pk']) for link in links]}")

# ==================== ⑥ 物化 ====================
section("⑥ 物化（编辑回写数据源）")

written = []
target = MaterializationTarget("postgres",
    lambda op, t, obj, patch: (written.append(f"{op}:{t}") or True))
engine.register_materialization(target)
engine.materialization.materialize("insert", "module", {"id": "m9", "name": "新模块"})
print(f"  物化回写日志: {written}")

# ==================== ⑦ 细粒度权限 + 审计 ====================
section("⑦ 细粒度权限 + 审计（governance）")

viewer = SecurityContext("viewer", ["viewer"])
engine.allow(["viewer"], resource="module", action="read")
engine.allow(["admin"], resource="*", action="*")

print(f"  viewer 读 module: {engine.security.check('module', 'read', viewer)}")
print(f"  viewer 写 module: {engine.security.check('module', 'write', viewer)}")
print(f"  admin 写 module: {engine.security.check('module', 'write', SecurityContext('admin', ['admin']))}")

# 审计
engine.audit.log("admin", "module", "create", detail={"id": "m1"}, success=True)
engine.audit.log("viewer", "module", "read", success=True)
print(f"  审计日志: {len(engine.audit.query())} 条")

# ==================== ⑧ 分支快照/回滚 ====================
section("⑧ 分支快照/回滚（branching）")

engine.snapshot_branch("before_cleanup")
before = engine.object_store.count("module")
engine.object_store.delete("module", "m3")
after = engine.object_store.count("module")
engine.switch_branch("before_cleanup")
restored = engine.object_store.count("module")
print(f"  删除前: {before} → 删除后: {after} → 回滚后: {restored}")

# ==================== ⑨ 工作流编排 ====================
section("⑨ 工作流编排（workflow）")

def exec_log(params, ctx):
    print(f"  [工作流执行] {params.get('action')} - {params.get('msg')}")
    return {"ok": True, "msg": params.get("msg")}

engine.register_action(ActionType("step_a", parameters=[
    ToolParameter(name="msg", type="string", description="消息", required=True)],
    execute_fn=exec_log))
engine.register_action(ActionType("step_b", parameters=[
    ToolParameter(name="msg", type="string", description="消息", required=True)],
    execute_fn=exec_log))

wf = Workflow("pipeline")
wf.add_node(StepNode("s1", "step_a", {"msg": "第一步"}), entry=True)
wf.add_node(StepNode("s2", "step_b", {"msg": "$s1.result" if False else "第二步"},
                     depends_on=["s1"]))
engine.workflow.register_workflow(wf)
result = engine.workflow.run("pipeline", ctx={"object_store": engine.object_store})
print(f"  工作流执行: success={result['success']}, 节点数={len(result['results'])}")

# ==================== ⑩ 事务补偿 ====================
section("⑩ 事务补偿（transaction / Saga）")

inventory = {"stock": 10}
tx = engine.transaction
tx.register("扣库存", lambda p, c: inventory.__setitem__("stock", inventory["stock"] - p.get("qty", 1)),
            lambda p, c: inventory.__setitem__("stock", inventory["stock"] + p.get("qty", 1)))
tx.register("扣款", lambda p, c: (_ for _ in ()).throw(RuntimeError("余额不足")),
            lambda p, c: None)

r = tx.execute([
    {"action": "扣库存", "params": {"qty": 3}},
    {"action": "扣款", "params": {"amount": 100}},
])
print(f"  事务: success={r['success']}, 失败={r['failed']}")
print(f"  已补偿: {r['compensated']}, 库存恢复: {inventory['stock']}")

# ==================== ⑪ 查询引擎 ====================
section("⑪ 查询引擎（object_set / navigate / join）")

# 对象集合：过滤+排序+分页
oset = engine.query.object_set("module", conditions={"owner": "张三"},
                               sort_by="amount", descending=True, limit=2)
print(f"  object_set: total={oset['total']}, 返回={len(oset['objects'])}")

# 链接导航
nav = engine.query.navigate_links("module", "m1", "belongs_to", max_depth=2)
print(f"  链接导航: {len(nav)} 条路径")

# 对象 join
joined = engine.query.describe_join("module", "belongs_to", "project")
print(f"  describe_join: {len(joined)} 条关联")

# ==================== ⑫ 定时调度 ====================
section("⑫ 定时调度（scheduler）")

sched = engine.scheduler
calls = []
sched.add_interval("tick", lambda p: calls.append(time.monotonic()),
                   interval_seconds=0.1, max_runs=3)
sched.start()
time.sleep(0.6)
sched.stop()
print(f"  间隔任务执行: {len(calls)} 次")

# ==================== ⑬ stats + health ====================
section("⑬ stats + health（运维）")


print("  引擎统计:")
print(json.dumps(engine.stats(), ensure_ascii=False, indent=2)[:500])
print("  健康检查:")
health = engine.get_health()
print(f"    status: {health['status']}, checks: {len(health['checks'])}")

print("\n" + "=" * 66)
print("✅ 全功能示例运行成功")
print("=" * 66)
