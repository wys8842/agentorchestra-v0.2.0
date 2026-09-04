# -*- coding: utf-8 -*-
"""Symphony 压力测试

覆盖：
1. ObjectStore 并发写入（多线程）
2. SQLite 并发读写 + 大数据量
3. Workflow 大量节点执行
4. Scheduler 高频任务
5. ToolRegistry 大量工具注册/执行
"""
import sys
import time
import threading
sys.path.insert(0, 'D:/proj/agentorchestra')

from agentorchestra.ontology import (
    ObjectStore, ObjectType, GraphStore, SQLiteBackend, MemoryBackend,
    OntologyEngine, SecurityContext, Workflow, StepNode,
)
from agentorchestra.tools.base import Tool, ToolParameter
from agentorchestra.tools.response import ToolResponse
from agentorchestra.tools.registry import ToolRegistry


def section(t):
    print("=" * 66)
    print("▶ " + t)
    print("=" * 66)


# ==================== 1. ObjectStore 并发写入 ====================
section("① ObjectStore 多线程并发写入")
Customer = ObjectType("customer", "cid", properties=[
    ToolParameter(name="cid", type="string", description="ID", required=True),
    ToolParameter(name="name", type="string", description="名", required=True),
])

def stress_objectstore_concurrent():
    store = ObjectStore(graph=GraphStore())
    store.register_type(Customer)

    errors = []
    N_THREADS = 8
    PER_THREAD = 500

    def writer(tid):
        try:
            for i in range(PER_THREAD):
                store.insert("customer", {"cid": f"t{tid}-{i}", "name": f"n{tid}-{i}"})
        except Exception as e:
            errors.append(str(e))

    start = time.monotonic()
    threads = [threading.Thread(target=writer, args=(t,)) for t in range(N_THREADS)]
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = time.monotonic() - start

    count = store.count("customer")
    expected = N_THREADS * PER_THREAD
    print(f"  线程: {N_THREADS} × {PER_THREAD} = {expected} 条")
    print(f"  实际写入: {count} 条")
    print(f"  耗时: {elapsed:.2f}s ({expected/elapsed:.0f} ops/s)")
    print(f"  错误: {len(errors)} {'✅' if not errors else '❌ ' + errors[0]}")
    assert count == expected and not errors, "并发写入数据不一致"
    return expected / elapsed


# ==================== 2. SQLite 并发读写 ====================
section("② SQLite 并发读写 + 数据量")
def stress_sqlite(tmp_dir="memory/stress"):
    import os, tempfile
    os.makedirs(tmp_dir, exist_ok=True)
    db_path = os.path.join(tmp_dir, "stress.db")
    backend = SQLiteBackend(db_path)
    store = ObjectStore(graph=GraphStore(), backend=backend)
    store.register_type(Customer)

    # 写入 10000 条
    start = time.monotonic()
    for i in range(10000):
        store.insert("customer", {"cid": f"sqlite-{i}", "name": f"n{i}"})
    write_elapsed = time.monotonic() - start

    # 读 + 搜索 + 聚合
    start = time.monotonic()
    got = store.get("customer", "sqlite-9999")
    search = len(store.search("customer", "sqlite-5"))
    filtered = len(store.filter("customer", {"name": "n5000"}))
    agg = store.aggregate("customer", "name", "count")
    read_elapsed = time.monotonic() - start

    print(f"  写入 10000 条: {write_elapsed:.2f}s ({10000/write_elapsed:.0f} ops/s)")
    print(f"  读取+搜索+过滤+聚合: {read_elapsed:.3f}s")
    print(f"  验证: get={got['name']} search={search} filter={filtered}")
    print(f"  SQLite 文件大小: {os.path.getsize(db_path)/1024:.0f} KB")

    # 多线程并发读写
    errors = []
    def rw(tid):
        try:
            for i in range(100):
                store.insert("customer", {"cid": f"conc-{tid}-{i}", "name": "x"})
                store.get("customer", "sqlite-1")
        except Exception as e:
            errors.append(str(e))

    start = time.monotonic()
    threads = [threading.Thread(target=rw, args=(t,)) for t in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    conc_elapsed = time.monotonic() - start
    print(f"  4 线程并发读写: {conc_elapsed:.2f}s, 错误: {len(errors)} {'✅' if not errors else '❌'}")

    backend.close()
    assert not errors, f"SQLite 并发错误: {errors[:2]}"
    return 10000 / write_elapsed


# ==================== 3. Workflow 大量节点 ====================
section("③ Workflow 大量节点执行")
def stress_workflow():
    store = ObjectStore(graph=GraphStore())
    Order = ObjectType("order", "order_id", properties=[
        ToolParameter(name="order_id", type="string", description="ID", required=True),
        ToolParameter(name="status", type="string", description="状态"),
    ])
    store.register_type(Order)
    engine = OntologyEngine(object_store=store, security_ctx=SecurityContext("a", ["a"]))
    engine.register_object_type(Order)

    def exec_set(params, ctx):
        oid = params.get("order_id") or "o1"
        return {"order_id": oid, "status": params.get("status", "x")}

    engine.register_action(__import__('agentorchestra.ontology', fromlist=['ActionType']).ActionType(
        "set_status", parameters=[
            ToolParameter(name="order_id", type="string", description="id", required=False),
            ToolParameter(name="status", type="string", description="s", required=False)],
        execute_fn=exec_set))

    # 50 个顺序节点
    N_NODES = 50
    wf = Workflow("stress_wf")
    prev = None
    for i in range(N_NODES):
        node = StepNode(f"s{i}", "set_status", {"status": f"st{i}"},
                        depends_on=[prev] if prev else [])
        wf.add_node(node, entry=(i == 0))
        prev = f"s{i}"
    engine.workflow.register_workflow(wf)

    start = time.monotonic()
    result = engine.workflow.run("stress_wf", ctx={"object_store": store})
    elapsed = time.monotonic() - start

    print(f"  {N_NODES} 个顺序节点: {elapsed:.4f}s")
    print(f"  成功: {result['success']} | 执行节点数: {len(result['results'])}")
    assert result["success"] and len(result["results"]) == N_NODES
    return N_NODES / max(elapsed, 1e-6)


# ==================== 4. Scheduler 高频任务 ====================
section("④ Scheduler 高频任务")
def stress_scheduler():
    from agentorchestra.ontology.process.scheduler import Scheduler
    sched = Scheduler(tick_seconds=0.05)
    calls = []
    sched.add_interval("fast", lambda p: calls.append(time.monotonic()),
                       interval_seconds=0.05, max_runs=50)
    sched.start()
    time.sleep(3.5)
    sched.stop()
    print(f"  0.05s 间隔任务: 执行 {len(calls)} 次 (期望 ~50)")
    assert len(calls) >= 40, "调度频率不足"
    return len(calls)


# ==================== 5. ToolRegistry 大量工具 ====================
section("⑤ ToolRegistry 大量工具注册/执行")
def stress_toolregistry():
    class SimpleTool(Tool):
        def __init__(self, idx):
            super().__init__(name=f"tool{idx}", description=f"工具{idx}", expandable=False)

        def get_parameters(self):
            return [ToolParameter(name="x", type="string", description="x", required=True)]

        def run(self, parameters):
            return ToolResponse.success(text=f"ok {parameters.get('x')}")

    registry = ToolRegistry()
    N_TOOLS = 200

    start = time.monotonic()
    for i in range(N_TOOLS):
        registry.register_tool(SimpleTool(i))
    reg_elapsed = time.monotonic() - start

    start = time.monotonic()
    for i in range(N_TOOLS):
        resp = registry.execute_tool(f"tool{i}", '{"x": "test"}')
    exec_elapsed = time.monotonic() - start

    print(f"  注册 {N_TOOLS} 个工具: {reg_elapsed:.2f}s")
    print(f"  执行 {N_TOOLS} 次: {exec_elapsed:.2f}s ({N_TOOLS/exec_elapsed:.0f} ops/s)")
    assert len(registry.list_tools()) == N_TOOLS
    return N_TOOLS / exec_elapsed


if __name__ == "__main__":
    print("\n" + "=" * 66)
    print("Symphony 压力测试")
    print("=" * 66)
    results = {}
    results["objectstore_ops"] = stress_objectstore_concurrent()
    results["sqlite_ops"] = stress_sqlite()
    results["workflow_nodes_per_s"] = stress_workflow()
    results["scheduler_calls"] = stress_scheduler()
    results["tool_exec_ops"] = stress_toolregistry()

    print("\n" + "=" * 66)
    print("📊 压力测试汇总")
    print("=" * 66)
    for k, v in results.items():
        print(f"  {k}: {v:.0f}" if isinstance(v, float) else f"  {k}: {v}")
    print("\nALL_STRESS_PASSED")
