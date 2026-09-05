# -*- coding: utf-8 -*-
"""AgentOrchestra 企业级 Ontology 完整示例：电商域

展示 领域设计期 → 装配 → 暴露给 Agent → 运行时 → 动态扩展 的完整流程。
"""
import sys

# 标准领域路径（不需要 sys.path 注入；安装后自然可导入）
from agentorchestra.ontology import (
    ActionType,
    Function,
    GraphStore,
    Interface,
    LinkType,
    ObjectStore,
    ObjectType,
    OntologyEngine,
    SecurityContext,
)
from agentorchestra.capability.tools.base import ToolParameter
from agentorchestra.capability.tools.registry import ToolRegistry


def section(t):
    print("=" * 66)
    print("▶ " + t)
    print("=" * 66)


# ==================== ① 领域设计期：定义业务语义 ====================
section("① 领域设计期：定义对象类型（业务实体）")

Customer = ObjectType(
    "customer", "customer_id",
    properties=[
        ToolParameter(name="customer_id", type="string", description="客户ID", required=True),
        ToolParameter(name="name", type="string", description="客户名", required=True),
        ToolParameter(name="tier", type="string", description="等级", default="standard"),
    ],
)
print("Customer 对象类型:")
print(f"  主键: {Customer.primary_key}")
print(f"  属性: {[p.name for p in Customer.get_properties()]}")
print(f"  必填: {[p.name for p in Customer.required_properties()]}")

Order = ObjectType(
    "order", "order_id",
    properties=[
        ToolParameter(name="order_id", type="string", description="订单ID", required=True),
        ToolParameter(name="customer_id", type="string", description="客户ID", required=True),
        ToolParameter(name="amount", type="number", description="金额", required=True),
        ToolParameter(name="status", type="string", description="状态", default="pending"),
    ],
    link_types=[LinkType("belongs_to", "order", "customer")],  # 订单属于客户
)
print("\nOrder 对象类型:")
print(f"  链接: {[link.name for link in Order.get_link_types()]}")

Product = ObjectType(
    "product", "product_id",
    properties=[
        ToolParameter(name="product_id", type="string", description="商品ID", required=True),
        ToolParameter(name="stock", type="integer", description="库存", required=True),
        ToolParameter(name="price", type="number", description="价格", required=True),
    ],
)
print("Product 对象类型已定义")

# ==================== ② 领域设计期：定义动作类型（业务操作） ====================
section("② 领域设计期：定义动作类型（增删改 + 业务规则）")

# 库存表（内存模拟数据库）
inventory = {"P1": 100, "P2": 50}


def check_stock(params, ctx):
    """规则：库存足够才能下单"""
    if inventory.get(params.get("product_id", ""), 0) < params.get("qty", 1):
        return "库存不足"
    return None


def check_amount(params, ctx):
    """规则：金额必须为正"""
    if params.get("amount", 0) <= 0:
        return "金额必须为正"
    return None


def do_create_order(params, ctx):
    """执行：创建订单对象（只写 order 定义的属性）"""
    return ctx["object_store"].insert("order", {
        "order_id": params["order_id"],
        "customer_id": params["customer_id"],
        "amount": params["amount"],
        "status": "pending",
    })


def notify_new_order(result, ctx):
    """副作用：下单后通知"""
    print(f"  📢 [副作用] 通知: 新订单 {result['order_id']} 已创建")


CreateOrder = ActionType(
    "create_order",
    parameters=[
        ToolParameter(name="order_id", type="string", description="订单ID", required=True),
        ToolParameter(name="customer_id", type="string", description="客户ID", required=True),
        ToolParameter(name="product_id", type="string", description="商品ID", required=True),
        ToolParameter(name="qty", type="integer", description="数量", required=True),
        ToolParameter(name="amount", type="number", description="金额", required=True),
    ],
    rules=[check_stock, check_amount],   # 提交前校验规则
    execute_fn=do_create_order,          # 实际执行
    side_effects=[notify_new_order],     # 副作用
)
print("CreateOrder 动作:")
print(f"  参数: {[p.name for p in CreateOrder.get_parameters()]}")
print(f"  规则: {len(CreateOrder.rules)} 条")
print(f"  副作用: {len(CreateOrder.side_effects)} 个")


def do_pay(params, ctx):
    """执行：支付 = 改状态 + 扣库存"""
    order_id = params["order_id"]
    ctx["object_store"].update("order", order_id, {"status": "paid"})
    pid = params["product_id"]
    inventory[pid] -= params["qty"]  # 扣库存
    return {"order_id": order_id, "status": "paid", "remaining_stock": inventory[pid]}


def rule_pending(params, ctx):
    """规则：只有 pending 状态才能支付"""
    order = ctx["object_store"].get("order", params["order_id"])
    return None if order and order.get("status") == "pending" else "订单状态不是 pending，无法支付"


PayOrder = ActionType(
    "pay_order",
    parameters=[
        ToolParameter(name="order_id", type="string", description="订单ID", required=True),
        ToolParameter(name="product_id", type="string", description="商品ID", required=True),
        ToolParameter(name="qty", type="integer", description="数量", required=True),
    ],
    rules=[rule_pending],
    execute_fn=do_pay,
)
print("PayOrder 动作已定义")

# ==================== ③ 领域设计期：定义函数（计算） ====================
section("③ 领域设计期：定义函数（纯计算）")


def compute_total(args, ctx):
    """计算订单总额"""
    amount = args.get("amount", 0)
    return {"amount": amount, "with_tax": round(amount * 1.13, 2)}


def compute_shipping(args, ctx):
    """计算运费"""
    amount = args.get("amount", 0)
    return {"amount": amount, "shipping": 0 if amount >= 100 else 10}


ComputeTotal = Function("compute_order_total", impl=compute_total,
                        arguments=[ToolParameter(name="amount", type="number", description="金额", required=True)])
ComputeShipping = Function("compute_shipping", impl=compute_shipping,
                           arguments=[ToolParameter(name="amount", type="number", description="金额", required=True)])
print("函数已定义: compute_order_total, compute_shipping")

# ==================== ④ 领域设计期：接口（统一抽象） ====================
section("④ 领域设计期：接口（多态）")

PayableIface = Interface("payable", required_properties=["amount", "status"])
print(f"接口 'payable' 要求属性: {PayableIface.required_properties}")

# ==================== ⑤ 装配到 OntologyEngine ====================
section("⑤ 装配到 OntologyEngine（统一管理）")

engine = OntologyEngine(
    object_store=ObjectStore(graph=GraphStore()),
    security_ctx=SecurityContext("admin", ["admin"]),
)

# 注册对象类型
for t in [Customer, Order, Product]:
    engine.register_object_type(t)
# 注册动作
for a in [CreateOrder, PayOrder]:
    engine.register_action(a)
# 注册函数
for f in [ComputeTotal, ComputeShipping]:
    engine.register_function(f)
# 注册接口 + 让 order 实现它
engine.register_interface(PayableIface)
engine.implement_interface("payable", "order")

# 权限：admin 全部允许
engine.allow(["admin"], resource="*", action="*")

print("引擎装配完成:")
print(engine.describe())

# ==================== ⑥ 暴露给 Agent（mount 到 registry） ====================
section("⑥ 暴露给 Agent（engine.mount → 自动生成工具）")

registry = ToolRegistry()
mounted = engine.mount(registry)
print("自动生成的工具:", mounted)
print(f"共 {len(mounted)} 个工具")

# ==================== ⑦ 直接通过工具执行（模拟 Agent 决策） ====================
section("⑦ 运行时：通过工具操作业务对象（模拟 Agent 决策）")

print("\n[用户指令] '创建订单'")
qt = registry.get_tool("QueryCustomer")
resp = qt.run({"mode": "list"})
print(f"  QueryCustomer(list): {resp.status.value}")

# Agent 决定创建订单
at = registry.get_tool("create_order")
resp = at.run({"order_id": "o1", "customer_id": "c1", "product_id": "P1",
               "qty": 2, "amount": 99.0})
print(f"  create_order: {resp.status.value} | {resp.text}")

# 支付
at = registry.get_tool("pay_order")
resp = at.run({"order_id": "o1", "product_id": "P1", "qty": 2})
print(f"  pay_order: {resp.status.value} | {resp.text}")

# 调用函数
ft = registry.get_tool("CallComputeOrderTotal")
resp = ft.run({"amount": 99.0})
print(f"  CallComputeOrderTotal: {resp.status.value} | {resp.text}")

# 接口查询（多态）
print("\n[接口查询] 所有 payable 对象:")
results = engine.query.query_interface(PayableIface)
for t, objs in results.items():
    print(f"  {t}: {len(objs)} 个对象")

# ==================== ⑧ 动作规则校验演示 ====================
section("⑧ 运行时：规则校验拦截（非法操作被拒绝）")

print("\n[用户指令] '再下单，但库存不足'")
at = registry.get_tool("create_order")
resp = at.run({"order_id": "o2", "customer_id": "c1", "product_id": "P2",
               "qty": 999, "amount": 50.0})
print(f"  create_order(库存不足): {resp.status.value}")
print(f"  错误: {resp.error_info['message'] if resp.error_info else resp.text}")

print("\n[用户指令] '重复支付已支付订单'")
at = registry.get_tool("pay_order")
resp = at.run({"order_id": "o1", "product_id": "P1", "qty": 2})
print(f"  pay_order(已支付): {resp.status.value}")
print(f"  错误: {resp.error_info['message'] if resp.error_info else resp.text}")

# ==================== ⑨ 动态扩展（受控） ====================
section("⑨ 动态扩展：新增对象类型（受治理）")

NewProduct = ObjectType(
    "new_weapon" if False else "promo_product", "product_id",
    properties=[
        ToolParameter(name="product_id", type="string", description="ID", required=True),
        ToolParameter(name="discount", type="number", description="折扣", required=True),
    ],
)
engine.register_object_type(NewProduct)
engine.object_store.register_type(NewProduct)
engine.object_store.insert("promo_product", {"product_id": "D1", "discount": 0.8})
print("动态新增对象类型 'promo_product':")
print(f"  引擎对象类型: {list(engine.object_types.keys())}")
print(f"  存储对象数: {engine.object_store.count('promo_product')}")

print("\n" + "=" * 66)
print("✅ 完整示例运行成功")
print("=" * 66)
