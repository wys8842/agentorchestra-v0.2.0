# -*- coding: utf-8 -*-
"""ecommerce / ontology_full 示例的压力测试

覆盖：
A. ecommerce 业务路径压力：批量下单/支付、规则拦截率、动作审计
B. ontology_full 功能压力：对象批量写入、过滤聚合、SQLite 持久化、工作流、事务
"""
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, 'D:/proj/agentorchestra')

from agentorchestra.ontology import (
    ObjectType, LinkType, ActionType, Function, Interface,
    OntologyEngine, SecurityContext, ObjectStore, GraphStore,
    SQLiteBackend, MaterializationTarget, Workflow, StepNode,
)
from agentorchestra.tools.base import ToolParameter
from agentorchestra.tools.registry import ToolRegistry


def section(t):
    print("=" * 66)
    print("▶ " + t)
    print("=" * 66)


def build_ecommerce():
    """电商域引擎（对应 ecommerce_ontology.py）"""
    inventory = {"P1": 10000, "P2": 5000}

    Customer = ObjectType("customer", "customer_id", properties=[
        ToolParameter(name="customer_id", type="string", description="ID", required=True),
        ToolParameter(name="name", type="string", description="名", required=True),
        ToolParameter(name="tier", type="string", description="等级", default="standard"),
    ])
    Order = ObjectType("order", "order_id", properties=[
        ToolParameter(name="order_id", type="string", description="ID", required=True),
        ToolParameter(name="customer_id", type="string", description="客户", required=True),
        ToolParameter(name="amount", type="number", description="金额", required=True),
        ToolParameter(name="status", type="string", description="状态", default="pending"),
    ])

    def check_stock(params, ctx):
        if inventory.get(params.get("product_id", ""), 0) < params.get("qty", 1):
            return "库存不足"
        return None

    def do_create_order(params, ctx):
        return ctx["object_store"].insert("order", {
            "order_id": params["order_id"], "customer_id": params["customer_id"],
            "amount": params["amount"], "status": "pending"})

    CreateOrder = ActionType("create_order", parameters=[
        ToolParameter(name="order_id", type="string", description="ID", required=True),
        ToolParameter(name="customer_id", type="string", description="客户", required=True),
        ToolParameter(name="product_id", type="string", description="商品", required=True),
        ToolParameter(name="qty", type="integer", description="数量", required=True),
        ToolParameter(name="amount", type="number", description="金额", required=True),
    ], rules=[check_stock], execute_fn=do_create_order)

    engine = OntologyEngine(object_store=ObjectStore(graph=GraphStore()),
                            security_ctx=SecurityContext("admin", ["admin"]))
    for t in [Customer, Order]: engine.register_object_type(t)
    engine.register_action(CreateOrder)
    engine.allow(["admin"], resource="*", action="*")
    return engine, CreateOrder, inventory


# ==================== A1. 批量下单压力 ====================
section("A1. ecommerce 批量下单（单线程 5000 次）")
def stress_batch_orders():
    e, action, inv = build_ecommerce()
    n = 5000
    start = time.monotonic()
    ok = 0
    for i in range(n):
        r = action.execute(
            {"order_id": f"o{i}", "customer_id": "c1", "product_id": "P1",
             "qty": 1, "amount": 99.0},
            {"object_store": e.object_store})
        if r["success"]:
            ok += 1
    elapsed = time.monotonic() - start
    total = e.object_store.count("order")
    print(f"  下单 {n} 次: success={ok}, 对象总数={total}")
    print(f"  耗时: {elapsed:.2f}s ({n/elapsed:.0f} ops/s)")
    assert ok == n and total == n
    return n / elapsed


# ==================== A2. 并发下单压力 ====================
section("A2. ecommerce 并发下单（8 线程 × 1000）")
def stress_concurrent_orders():
    e, action, inv = build_ecommerce()
    n_threads, per = 8, 1000
    errors = []

    def worker(tid):
        try:
            for i in range(per):
                action.execute(
                    {"order_id": f"t{tid}-{i}", "customer_id": "c1",
                     "product_id": "P1", "qty": 1, "amount": 99.0},
                    {"object_store": e.object_store})
        except Exception as ex:
            errors.append(str(ex))

    start = time.monotonic()
    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = time.monotonic() - start
    expected = n_threads * per
    actual = e.object_store.count("order")
    print(f"  并发下单 {expected} 次, 实际对象: {actual}")
    print(f"  耗时: {elapsed:.2f}s ({expected/elapsed:.0f} ops/s), 错误: {len(errors)}")
    assert actual == expected and not errors
    return expected / elapsed


# ==================== A3. 规则拦截率 ====================
section("A3. ecommerce 规则拦截（库存不足拒绝率）")
def stress_rule_rejection():
    e, action, inv = build_ecommerce()
    rejected = 0
    total = 2000
    for i in range(total):
        # 一半下单超库存
        qty = 1 if i % 2 == 0 else 99999
        r = action.execute(
            {"order_id": f"r{i}", "customer_id": "c1", "product_id": "P1",
             "qty": qty, "amount": 99.0},
            {"object_store": e.object_store})
        if not r["success"]:
            rejected += 1
    print(f"  总尝试: {total}, 被拦截: {rejected} ({rejected/total*100:.0f}%)")
    # 偶数成功 1000，奇数被拒 1000
    assert rejected == total // 2
    return rejected / total


# ==================== A4. 动作审计规模 ====================
section("A4. ecommerce 动作审计")
def stress_audit():
    e, action, inv = build_ecommerce()
    for i in range(1000):
        action.execute(
            {"order_id": f"a{i}", "customer_id": "c1", "product_id": "P1",
             "qty": 1, "amount": 99.0},
            {"object_store": e.object_store})
    audits = action.get_audit()
    print(f"  动作审计条数: {len(audits)}")
    assert len(audits) >= 1000
    return len(audits)


# ==================== B1. 全功能批量写入 ====================
section("B1. ontology_full 批量写入（内存 10000 条）")
def stress_full_batch(tmp_dir=None):
    db_path = str(Path(tmp_dir) / "full.db") if tmp_dir else None
    backend = SQLiteBackend(db_path) if tmp_dir else None
    e = OntologyEngine(object_store=ObjectStore(graph=GraphStore(), backend=backend),
                       security_ctx=SecurityContext("admin", ["admin"]))
    Module = ObjectType("module", "id", properties=[
        ToolParameter(name="id", type="string", description="ID", required=True),
        ToolParameter(name="name", type="string", description="名", required=True),
        ToolParameter(name="owner", type="string", description="负责人", required=False),
        ToolParameter(name="amount", type="number", description="金额", required=False),
    ])
    e.register_object_type(Module)

    n = 10000
    start = time.monotonic()
    for i in range(n):
        e.object_store.insert("module", {
            "id": f"m{i}", "name": f"模块{i}", "owner": "张三" if i % 2 else "李四",
            "amount": i * 1.5})
    elapsed = time.monotonic() - start
    print(f"  写入 {n} 条: {elapsed:.2f}s ({n/elapsed:.0f} ops/s)")
    assert e.object_store.count("module") == n
    if backend: backend.close()
    return n / elapsed


# ==================== B2. 过滤 + 聚合压力 ====================
section("B2. ontology_full 过滤 + 聚合（10000 条）")
def stress_full_query():
    e = OntologyEngine(object_store=ObjectStore(graph=GraphStore()),
                       security_ctx=SecurityContext("admin", ["admin"]))
    Module = ObjectType("module", "id", properties=[
        ToolParameter(name="id", type="string", description="ID", required=True),
        ToolParameter(name="name", type="string", description="名", required=True),
        ToolParameter(name="owner", type="string", description="负责人", required=False),
        ToolParameter(name="amount", type="number", description="金额", required=False),
    ])
    e.register_object_type(Module)
    for i in range(10000):
        e.object_store.insert("module", {
            "id": f"m{i}", "name": f"模块{i}", "owner": "张三" if i % 2 else "李四",
            "amount": i * 1.5})

    # 过滤
    start = time.monotonic()
    filtered = e.object_store.filter("module", {"owner": "张三"})
    f_elapsed = time.monotonic() - start
    # 聚合
    start = time.monotonic()
    agg = e.object_store.aggregate("module", "owner", "count")
    a_elapsed = time.monotonic() - start

    print(f"  过滤(owner=张三): {len(filtered)} 条, {f_elapsed:.3f}s")
    print(f"  聚合(按owner): {agg}, {a_elapsed:.3f}s")
    assert len(filtered) == 5000
    assert agg == {"张三": 5000, "李四": 5000}
    return 10000 / max(f_elapsed, 1e-6)


# ==================== B3. 工作流压力（50 节点） ====================
section("B3. ontology_full 工作流（50 个顺序节点）")
def stress_full_workflow():
    e = OntologyEngine(security_ctx=SecurityContext("a", ["a"]))
    e.register_action(ActionType("log_step", parameters=[
        ToolParameter(name="msg", type="string", description="消息", required=False)],
        execute_fn=lambda p, c: {"ok": True, "msg": p.get("msg")}))

    wf = Workflow("big")
    prev = None
    for i in range(50):
        node = StepNode(f"s{i}", "log_step", {"msg": f"第{i}步"},
                        depends_on=[prev] if prev else [])
        wf.add_node(node, entry=(i == 0))
        prev = f"s{i}"
    e.workflow.register_workflow(wf)

    start = time.monotonic()
    r = e.workflow.run("big", ctx={})
    elapsed = time.monotonic() - start
    print(f"  50 节点工作流: {elapsed:.4f}s, 节点数={len(r['results'])}")
    assert r["success"] and len(r["results"]) == 50
    return 50 / max(elapsed, 1e-6)


# ==================== B4. 事务压力（1000 次 Saga） ====================
section("B4. ontology_full 事务（1000 次成功/失败混合）")
def stress_full_transaction():
    e = OntologyEngine(security_ctx=SecurityContext("a", ["a"]))
    inv = {"stock": 100000}
    tx = e.transaction
    tx.register("deduct", lambda p, c: inv.__setitem__("stock", inv["stock"] - p.get("qty", 1)),
                lambda p, c: inv.__setitem__("stock", inv["stock"] + p.get("qty", 1)))
    tx.register("fail", lambda p, c: (_ for _ in ()).throw(RuntimeError("x")), None)

    start = time.monotonic()
    compensated = 0
    for i in range(1000):
        if i % 2 == 0:
            r = tx.execute([{"action": "deduct", "params": {"qty": 1}}])
        else:
            r = tx.execute([{"action": "deduct", "params": {"qty": 1}},
                            {"action": "fail", "params": {}}])
            if "deduct" in r["compensated"]:
                compensated += 1
    elapsed = time.monotonic() - start
    print(f"  1000 次事务: {elapsed:.2f}s, 补偿 {compensated} 次")
    # 成功500次扣500，失败500次补偿恢复
    assert inv["stock"] == 100000 - 500
    assert compensated == 500
    return 1000 / elapsed


# ==================== 汇总 ====================
if __name__ == "__main__":
    results = {}
    results["ecommerce_batch_ops"] = stress_batch_orders()
    results["ecommerce_concurrent_ops"] = stress_concurrent_orders()
    results["ecommerce_rejection_rate"] = stress_rule_rejection()
    results["ecommerce_audit_count"] = stress_audit()
    results["full_batch_ops"] = stress_full_batch()
    results["full_query_ops"] = stress_full_query()
    results["full_workflow_nodes"] = stress_full_workflow()
    results["full_txn_ops"] = stress_full_transaction()

    print("\n" + "=" * 66)
    print("📊 压力测试汇总")
    print("=" * 66)
    for k, v in results.items():
        print(f"  {k}: {v:.0f}" if isinstance(v, float) else f"  {k}: {v}")
    print("\nALL_STRESS_PASSED")
