# -*- coding: utf-8 -*-
"""Agent 层面综合案例的压力测试 v2 —— 全面多样化

覆盖全部 11 个工具，参数多样化（多城市、多商品、多场景）。
检测真实 bug（如 DevLog 的 INVALID_PARAMETERS 枚举错误）。
"""
import json
import random
import sys
import threading
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, 'D:/proj/agentorchestra')

from agentorchestra.agents.react_agent import ReActAgent
from agentorchestra.core.config import Config
from agentorchestra.observability import TraceLogger
from agentorchestra.ontology import (
    ActionType,
    Function,
    GraphStore,
    LinkType,
    ObjectStore,
    ObjectType,
    OntologyEngine,
    SecurityContext,
    StepNode,
    Workflow,
)
from agentorchestra.tools.base import Tool, ToolParameter
from agentorchestra.tools.builtin.calculator import CalculatorTool
from agentorchestra.tools.registry import ToolRegistry
from agentorchestra.tools.response import ToolResponse


def section(t):
    print("=" * 66)
    print("▶ " + t)
    print("=" * 66)


# ============ 工具定义（多场景） ============

CITIES = ["北京", "上海", "深圳", "广州", "纽约", "东京", "巴黎", "伦敦",
          "新加坡", "首尔", "悉尼", "莫斯科", "迪拜", "曼谷", "柏林", "多伦多"]
GOODS = ["咖啡", "笔记本", "机械键盘", "显示器", "机械臂", "传感器", "无人机",
         "服务器", "GPU 卡", "路由器", "温湿度计", "PLC 控制器"]
ACTIONS = ["start", "stop", "restart", "deploy", "rollback", "scale",
           "migrate", "backup", "cleanup", "upgrade"]
LOG_CATEGORIES = ["decision", "progress", "issue", "solution", "refactor", "test", "performance"]
MATH_EXPRS = ["1+1", "3*7", "100/4", "2**10", "15-8", "123+456", "9*9",
              "1024/8", "77-19", "5*5*5"]


class WeatherTool(Tool):
    def __init__(self):
        super().__init__(name="Weather", description="查天气", expandable=False)
    def get_parameters(self):
        return [ToolParameter(name="city", type="string", description="城市", required=True)]
    def run(self, parameters):
        city = parameters.get("city", "未知")
        return ToolResponse.success(text=f"{city} 晴 {random.randint(-5, 35)}°C")


class DiscountTool(Tool):
    def __init__(self):
        super().__init__(name="Discount", description="折扣", expandable=False)
    def get_parameters(self):
        return [ToolParameter(name="amount", type="number", description="金额", required=True),
                ToolParameter(name="tier", type="string", description="等级", required=False)]
    def run(self, parameters):
        amount = parameters.get("amount", 0)
        tier = parameters.get("tier", "regular")
        rates = {"regular": 0.9, "vip": 0.8, "vvip": 0.7, "staff": 0.6}
        rate = rates.get(tier, 0.9)
        return ToolResponse.success(text=f"折后 {amount * rate:.2f}")


class SimpleUsage:
    def __init__(self):
        self.total_tokens = 10; self.prompt_tokens = 5; self.completion_tokens = 5


class FakeToolCall:
    def __init__(self, name, args):
        self.id = f"call_{name}"; self.type = "function"
        self.function = FakeFunction(name, args)


class FakeFunction:
    def __init__(self, name, args):
        self.name = name; self.arguments = json.dumps(args)


class SimpleMessage:
    def __init__(self, user_input, finalize=False):
        self.content = None; self.tool_calls = []
        if finalize:
            self.content = f"完成: {user_input}"; return
        if "订单" in user_input:
            self.tool_calls = [FakeToolCall("create_order",
                {"order_id": "o1", "customer_id": "c1", "amount": 99.0})]
        elif "天气" in user_input:
            import re as _re
            m = _re.search(r"(北京|上海|深圳|广州|纽约|东京|巴黎|伦敦|新加坡|首尔)", user_input)
            city = m.group(1) if m else "北京"
            self.tool_calls = [FakeToolCall("Weather", {"city": city})]
        elif "计算" in user_input:
            self.tool_calls = [FakeToolCall("python_calculator",
                {"expression": "2**10"})]
        elif "日志" in user_input:
            self.tool_calls = [FakeToolCall("DevLog", {"action": "append", "category": "decision", "content": "审计"})]
        elif "技能" in user_input or "技能" in user_input:
            import re as _re2
            m = _re2.search(r"(systematic-debugging|xlsx|writing-plans|skill-creator|test-driven-development|verification-before-completion)", user_input)
            sk = m.group(1) if m else "writing-plans"
            self.tool_calls = [FakeToolCall("Skill", {"skill": sk, "args": ""})]
        else:
            self.content = f"已理解: {user_input}"


class SimpleChoice:
    def __init__(self, user_input, finalize=False):
        self.message = SimpleMessage(user_input, finalize)


class SimpleChoices:
    def __init__(self, user_input, finalize=False):
        self.choices = [SimpleChoice(user_input, finalize)]
        self.usage = SimpleUsage()


class MockLLM:
    def __init__(self, model="mock-model"):
        self.model = model
    def invoke_with_tools(self, messages, tools=None, tool_choice="auto", **kwargs):
        has_tool = any(m.get("role") == "tool" for m in messages)
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        return SimpleChoices(last, finalize=has_tool)
    def invoke(self, messages, **kwargs):
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        return SimpleChoices(last).choices[0].message


def build_setup():
    config = Config(trace_enabled=False, session_enabled=False, ontology_engine_enabled=True,
                    skills_enabled=True, skills_dir="skills")

    registry = ToolRegistry()
    registry.register_tool(WeatherTool())
    registry.register_tool(DiscountTool())
    registry.register_tool(CalculatorTool())

    Customer = ObjectType("customer", "customer_id", properties=[
        ToolParameter(name="customer_id", type="string", description="ID", required=True),
        ToolParameter(name="name", type="string", description="名", required=True),
    ])
    Order = ObjectType("order", "order_id", properties=[
        ToolParameter(name="order_id", type="string", description="ID", required=True),
        ToolParameter(name="customer_id", type="string", description="客户", required=True),
        ToolParameter(name="amount", type="number", description="金额", required=True),
    ], link_types=[LinkType("belongs_to", "order", "customer")])

    def check_amount(params, ctx):
        if params.get("amount", 0) <= 0: return "金额必须为正"
        return None

    def do_create_order(params, ctx):
        return ctx["object_store"].insert("order", {
            "order_id": params["order_id"], "customer_id": params["customer_id"],
            "amount": params["amount"]})

    CreateOrder = ActionType("create_order", parameters=[
        ToolParameter(name="order_id", type="string", description="ID", required=True),
        ToolParameter(name="customer_id", type="string", description="客户", required=True),
        ToolParameter(name="amount", type="number", description="金额", required=True)],
        rules=[check_amount], execute_fn=do_create_order)

    ComputeTotal = Function("compute_order_total",
        impl=lambda a, c: {"with_tax": round(a.get("amount", 0) * 1.13, 2)},
        arguments=[ToolParameter(name="amount", type="number", description="金额", required=True)])

    engine = OntologyEngine(object_store=ObjectStore(graph=GraphStore()),
                            security_ctx=SecurityContext("agent", ["agent"]))
    engine.register_object_type(Customer)
    engine.register_object_type(Order)
    engine.register_action(CreateOrder)
    engine.register_function(ComputeTotal)
    engine.allow(["agent"], resource="*", action="*")
    mounted = engine.mount(registry)

    agent = ReActAgent(name="OpsAgent", llm=MockLLM(), tool_registry=registry,
                       config=config, max_steps=3)
    return registry, engine, agent, mounted


def tool_invoke(reg, name, **params):
    """统一调用工具，处理签名差异"""
    t = reg.get_tool(name)
    try:
        if name == "DevLog":
            # DevLog 是会话内工具，需要 session 上下文；走其挂载参数
            return t.run(params if params else {"action": "summary"})
        r = t.run(params)
        return r
    except Exception as e:
        return ToolResponse.error(code="INTERNAL_ERROR", message=str(e))


# ==================== 1. 工具清单统计 ====================
section("1. 工具清单统计")
reg, eng, agent, mounted = build_setup()
auto = [t for t in reg.list_tools() if t in ("Skill", "Task", "TodoWrite", "DevLog")]
custom_builtin = [t for t in reg.list_tools() if t not in mounted and t not in auto]
print(f"  自定义工具 ({len(custom_builtin)}): {custom_builtin}")
print(f"  Agent 自动注册框架工具 ({len(auto)}): {auto}")
print(f"  ontology 添加工具 ({len(mounted)}): {mounted}")
print(f"  总工具数: {len(reg.list_tools())} = {len(custom_builtin)}+{len(auto)}+{len(mounted)}")
assert len(reg.list_tools()) == 11

# ==================== 2. 全部工具逐一执行（多样化参数） ====================
section("2. 全部 11 个工具逐一执行")
seen = {}
for i, tname in enumerate(reg.list_tools()):
    tool = reg.get_tool(tname)
    try:
        if tname == "Weather":
            r = tool.run({"city": CITIES[i % len(CITIES)]})
        elif tname == "Discount":
            r = tool.run({"amount": random.randint(1, 9999), "tier": random.choice(["vip", "vvip", "staff"])})
        elif tname == "python_calculator":
            r = tool.run({"expression": random.choice(MATH_EXPRS)})
        elif tname == "QueryCustomer":
            r = tool.run({"customer_id": f"c{i}", "mode": "get"})
        elif tname == "QueryOrder":
            r = tool.run({"order_id": f"o{i}", "mode": "get"})
        elif tname == "create_order":
            r = tool.run({"order_id": f"o{i}", "customer_id": f"c{i}", "amount": random.randint(1, 9999)})
        elif tname == "CallComputeOrderTotal":
            r = tool.run({"amount": random.randint(1, 9999)})
        elif tname == "Skill":
            r = tool.run({"skill": "systematic-debugging", "args": "{}"})
            ok = r.status.value
            seen[tname] = ok
            print(f"  {tname:26s} → {ok} (loaded={r.data.get('loaded')}, tokens={r.data.get('token_estimate')})")
            continue
        elif tname == "Task":
            r = tool.run({"task": f"处理运维事件 #{i}", "max_steps": 3})
        elif tname == "TodoWrite":
            r = tool.run({"summary": f"巡检 #{i}", "todos": [], "action": "create"})
        elif tname == "DevLog":
            r = tool.run({"action": "append", "category": random.choice(LOG_CATEGORIES),
                          "content": f"日志 {i}"})
        else:
            r = tool.run({})
        ok = r.status.value
        seen[tname] = ok
        print(f"  {tname:26s} → {ok}")
    except Exception as e:
        seen[tname] = f"ERROR:{e}"
        print(f"  {tname:26s} → ERROR {e}")
print("  各工具结果:", {k: v for k, v in seen.items()})
assert all(v == "success" for v in seen.values()), seen
print("  ✅ 全部 11 个工具执行成功（含技能加载）")

# ==================== 3. 工具高频调用（单线程混合） ====================
section("3. 工具高频调用（单线程，混合 7 类工具 × 多样参数）")
call_log = Counter()
start = time.monotonic()
for i in range(2000):
    city = CITIES[i % len(CITIES)]
    tool_invoke(reg, "Weather", city=city); call_log["Weather"] += 1
for i in range(1500):
    tool_invoke(reg, "Discount", amount=random.randint(1, 9999),
                tier=random.choice(["regular", "vip", "vvip", "staff"])); call_log["Discount"] += 1
for i in range(1000):
    tool_invoke(reg, "python_calculator", expression=random.choice(MATH_EXPRS)); call_log["calc"] += 1
for i in range(1200):
    tool_invoke(reg, "create_order",
                order_id=f"h{i}", customer_id=f"c{i%5}", amount=random.randint(1, 9999)); call_log["create_order"] += 1
for i in range(800):
    tool_invoke(reg, "CallComputeOrderTotal", amount=random.randint(1, 9999)); call_log["total"] += 1
for i in range(600):
    tool_invoke(reg, "QueryOrder", order_id=f"h{i%1200}", mode="get"); call_log["query"] += 1
for i in range(400):
    tool_invoke(reg, "DevLog", action="append",
                category=random.choice(LOG_CATEGORIES), content=f"高频日志 {i}"); call_log["devlog"] += 1
elapsed = time.monotonic() - start
total = sum(call_log.values())
print(f"  调用分布: {dict(call_log)}")
print(f"  共 {total} 次调用: {elapsed:.2f}s ({total/elapsed:.0f} ops/s)")
print(f"  执行后订单数: {eng.object_store.count('order')}")

# ==================== 4. 工具并发调用（多样工具） ====================
section("4. 工具并发调用（8 线程 × 200，工具轮换 + 城市轮换）")
errors = []
per_thread_uses = {}

def worker(tid):
    uses = Counter()
    try:
        for i in range(200):
            choice = i % 6
            if choice == 0:
                tool_invoke(reg, "Weather", city=CITIES[(tid + i) % len(CITIES)])
                uses["Weather"] += 1
            elif choice == 1:
                tool_invoke(reg, "Discount", amount=tid * 100 + i,
                            tier=random.choice(["vip", "staff"]))
                uses["Discount"] += 1
            elif choice == 2:
                tool_invoke(reg, "python_calculator",
                            expression=MATH_EXPRS[(tid + i) % len(MATH_EXPRS)])
                uses["calc"] += 1
            elif choice == 3:
                tool_invoke(reg, "create_order",
                            order_id=f"t{tid}-{i}", customer_id=f"c{tid%5}",
                            amount=random.randint(1, 9999))
                uses["create_order"] += 1
            elif choice == 4:
                tool_invoke(reg, "CallComputeOrderTotal", amount=i)
                uses["total"] += 1
            else:
                tool_invoke(reg, "QueryOrder", order_id=f"t{tid}-{i-1}", mode="get")
                uses["query"] += 1
        per_thread_uses[tid] = uses
    except Exception as e:
        errors.append(str(e))

start = time.monotonic()
threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
for t in threads: t.start()
for t in threads: t.join()
elapsed = time.monotonic() - start
agg = Counter()
for u in per_thread_uses.values(): agg += u
total = sum(agg.values())
print(f"  调用分布: {dict(agg)}")
print(f"  并发 {total} 次: {elapsed:.2f}s ({total/elapsed:.0f} ops/s), 错误: {len(errors)}")
print(f"  订单对象数: {eng.object_store.count('order')}")
assert not errors

# ==================== 5. Agent 多轮 run（不同任务轮换） ====================
section("5. Agent 多轮 run（任务轮换：订单/多城市天气/计算/日志/技能）")
start = time.monotonic()
TASKS = ["帮我创建订单", "查一下北京天气", "查一下上海天气", "查一下纽约天气",
         "帮我计算一下 2**10", "写一条审计日志", "查一下东京天气", "查一下巴黎天气",
         "加载 systematic-debugging 技能", "加载 xlsx 技能"]
for i in range(200):
    agent.run(TASKS[i % len(TASKS)])
elapsed = time.monotonic() - start
print(f"  200 次 Agent run: {elapsed:.2f}s ({200/elapsed:.1f} runs/s)")
print(f"  历史消息: {len(agent.get_history())} 条")

# ==================== 6. Agent 并发 run（多样化任务） ====================
section("6. Agent 并发 run（4 线程 × 50，多样化任务）")
agent2 = ReActAgent(name="OpsAgent2", llm=MockLLM(),
                    tool_registry=reg,
                    config=Config(trace_enabled=False, skills_enabled=True, skills_dir="skills"),
                    max_steps=3)
errs6 = []

def agent_worker(tid):
    try:
        for i in range(50):
            agent2.run(random.choice(["帮我创建订单", "查一下北京天气", "查一下上海天气",
                                      "查一下纽约天气", "帮我计算一下 2**10",
                                      "写一条审计日志", "查一下东京天气",
                                      "加载 writing-plans 技能"]))
    except Exception as e:
        errs6.append(str(e))

start = time.monotonic()
threads = [threading.Thread(target=agent_worker, args=(t,)) for t in range(4)]
for t in threads: t.start()
for t in threads: t.join()
elapsed = time.monotonic() - start
print(f"  4线程×50 Agent run: {elapsed:.2f}s, 错误: {len(errs6)}")
assert not errs6

# ==================== 7. ontology CRUD 压力（多样商品/金额） ====================
section("7. ontology 对象 CRUD 压力（5000 insert / 3000 get / filter）")
start = time.monotonic()
for i in range(5000):
    eng.object_store.insert("order", {"order_id": f"x{i}",
                                      "customer_id": f"c{i%10}",
                                      "amount": random.randint(1, 100000)})
insert_elapsed = time.monotonic() - start

start = time.monotonic()
for i in range(3000):
    eng.object_store.get("order", f"x{i}")
get_elapsed = time.monotonic() - start

start = time.monotonic()
f = eng.object_store.filter("order", {"customer_id": "c3"})
filter_elapsed = time.monotonic() - start

print(f"  insert 5000: {insert_elapsed:.2f}s ({5000/insert_elapsed:.0f} ops/s)")
print(f"  get 3000: {get_elapsed:.3f}s")
print(f"  filter(c3): {len(f)} 条, {filter_elapsed:.3f}s")

# ==================== 8. 子代理压力（不同任务） ====================
section("8. 子代理机制压力（100 次，多城市/任务轮换）")
start = time.monotonic()
summaries = []
for i in range(100):
    r = agent2.run_as_subagent(random.choice(["查北京天气", "查上海天气", "查纽约天气",
                                              "查东京天气", "查巴黎天气",
                                              "帮我计算 3*7", "写条审计日志",
                                              "加载 skill-creator 技能"]),
                               return_summary=True)
    summaries.append(r["summary"])
elapsed = time.monotonic() - start
print(f"  100 次子代理: {elapsed:.2f}s ({100/elapsed:.1f} ops/s)")
print(f"  成功摘要: {sum([1 for s in summaries if s])}/{len(summaries)}")

# ==================== 9. 工作流 + 事务压力（多样操作） ====================
section("9. 工作流 + 事务压力（多样操作）")
eng.register_action(ActionType("log_step", parameters=[
    ToolParameter(name="msg", type="string", description="消息", required=False)],
    execute_fn=lambda p, c: {"ok": True}))
wf = Workflow("wf")
wf.add_node(StepNode("s1", "log_step", {"msg": "阶段1"}), entry=True)
wf.add_node(StepNode("s2", "log_step", {"msg": "阶段2"}, depends_on=["s1"]))
eng.workflow.register_workflow(wf)

start = time.monotonic()
for i in range(1000):
    eng.workflow.run("wf", ctx={})
wf_elapsed = time.monotonic() - start

inv = {"stock": 100000, "balance": 1000000}
eng.transaction.register("deduct_stock", lambda p, c: inv.__setitem__("stock", inv["stock"] - p.get("qty", 1)),
                         lambda p, c: inv.__setitem__("stock", inv["stock"] + p.get("qty", 1)))
eng.transaction.register("deduct_balance", lambda p, c: inv.__setitem__("balance", inv["balance"] - p.get("amt", 1)),
                         lambda p, c: inv.__setitem__("balance", inv["balance"] + p.get("amt", 1)))
eng.transaction.register("fail_op", lambda p, c: (_ for _ in ()).throw(RuntimeError("x")), None)
start = time.monotonic()
comp = 0
for i in range(1000):
    if i % 3 == 0:
        # 扣库存+扣余额+失败 → 触发补偿
        r = eng.transaction.execute([{"action": "deduct_stock", "params": {"qty": 1}},
                                     {"action": "deduct_balance", "params": {"amt": 100}},
                                     {"action": "fail_op", "params": {}}])
        comp += len(r["compensated"])
    elif i % 3 == 1:
        # 多步骤成功事务
        eng.transaction.execute([{"action": "deduct_stock", "params": {"qty": 1}},
                                 {"action": "deduct_balance", "params": {"amt": 100}}])
    else:
        # 纯查询型事务（无副作用）
        eng.transaction.execute([{"action": "deduct_stock", "params": {"qty": 0}}])
tx_elapsed = time.monotonic() - start

print(f"  1000 次工作流: {wf_elapsed:.2f}s ({1000/wf_elapsed:.0f} ops/s)")
print(f"  1000 次事务: {tx_elapsed:.2f}s, 触发补偿 {comp} 次")
print(f"  库存: {inv['stock']} (应=100000-1000-333≈98667)", f", 余额: {inv['balance']}")

# ==================== 10. TraceLogger 记录量（多样事件） ====================
section("10. TraceLogger 记录量（10000 事件，多样工具/城市）")
tl = TraceLogger(output_dir="memory/traces", sanitize=True)
N = 10000
start = time.monotonic()
for i in range(N):
    kind = i % 4
    if kind == 0:
        tl.log_event("tool_call", {"tool_name": "Weather",
                                   "args": {"city": CITIES[i % len(CITIES)]}}, step=i % 5)
    elif kind == 1:
        tl.log_event("tool_call", {"tool_name": "create_order",
                                   "args": {"order_id": f"tr{i}", "amount": i}}, step=i % 5)
    elif kind == 2:
        tl.log_event("llm_request", {"model": "mock", "tokens": 10}, step=i % 5)
    else:
        tl.log_event("action_execute", {"action": "log_step", "ok": True}, step=i % 5)
tl.log_event("session_end", {"status": "success", "total_steps": 10000})
tl.finalize()
elapsed = time.monotonic() - start
print(f"  {N} 条事件写入: {elapsed:.2f}s ({N/elapsed:.0f} ops/s)")
print(f"  JSONL 文件大小: {(tl.jsonl_path.stat().st_size / 1024):.0f} KB")

# ==================== 11. 技能加载压力（6 技能循环/并发/热重载） ====================
section("11. 技能加载压力（6 技能循环 / 并发 / 热重载）")
from agentorchestra.skills import SkillLoader
from agentorchestra.tools.builtin.skill_tool import SkillTool

SKILL_NAMES = ['skill-creator', 'systematic-debugging', 'test-driven-development',
               'verification-before-completion', 'writing-plans', 'xlsx']
loader = SkillLoader(skills_dir=Path("skills"))
stool = SkillTool(skill_loader=loader)

# 11a. 循环加载（600 次，6 技能轮换）
start = time.monotonic()
load_stats = Counter()
for i in range(600):
    sk = SKILL_NAMES[i % len(SKILL_NAMES)]
    r = stool.run({"skill": sk, "args": f"任务#{i}"})
    load_stats[r.status.value] += 1
elapsed = time.monotonic() - start
print(f"  11a. 600 次技能加载(6技能轮换): {elapsed:.2f}s ({600/elapsed:.0f} ops/s), 结果: {dict(load_stats)}")
assert load_stats["success"] == 600

# 11b. 并发技能加载（4 线程 × 150）
errs11 = []
def skill_worker(tid):
    try:
        for i in range(150):
            sk = SKILL_NAMES[(tid + i) % len(SKILL_NAMES)]
            r = stool.run({"skill": sk})
            if r.status.value != "success":
                errs11.append(sk)
    except Exception as e:
        errs11.append(str(e))

start = time.monotonic()
threads = [threading.Thread(target=skill_worker, args=(t,)) for t in range(4)]
for t in threads: t.start()
for t in threads: t.join()
elapsed = time.monotonic() - start
print(f"  11b. 4线程×150 并发技能加载: {elapsed:.2f}s, 错误: {len(errs11)}")
assert not errs11

# 11c. 热重载 + 缓存命中
t0 = time.monotonic()
for _ in range(100):
    loader.get_skill("xlsx")   # 缓存命中
t_cache = (time.monotonic() - t0) / 100 * 1000
loader.reload()                # 热重载
t1 = time.monotonic()
s = loader.get_skill("writing-plans")
t_reload = time.monotonic() - t1
print(f"  11c. 缓存命中 {t_cache:.4f} ms/次, 热重载后加载: {t_reload:.3f}s, 技能数: {len(loader.list_skills())}")
assert s is not None and len(loader.list_skills()) == 6

# 11d. Skill 参数替换（$ARGUMENTS 占位符）
import shutil
tmp_dir = Path("skills") / "test-arg"
tmp_dir.mkdir(parents=True, exist_ok=True)
(tmp_dir / "SKILL.md").write_text(
    "---\nname: test-arg\ndescription: test placeholder\n---\n执行任务: $ARGUMENTS",
    encoding="utf-8")
loader.reload()
r = stool.run({"skill": "test-arg", "args": "SYMPHONY-TEST"})
has_arg = "SYMPHONY-TEST" in r.text
shutil.rmtree(tmp_dir)
loader.reload()
print(f"  11d. $ARGUMENTS 占位符替换: {has_arg}（用临时技能验证）")
assert has_arg

print("\n" + "=" * 66)
print("ALL_STRESS_PASSED")
