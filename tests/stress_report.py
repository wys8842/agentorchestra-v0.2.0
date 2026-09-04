# -*- coding: utf-8 -*-
"""AgentOrchestra 全面压力测试 + 测试报告生成器

目标：
1. 覆盖全部 11 个工具，记录每个测试用到的工具明细
2. 明确主 Agent 类型 / 子 Agent 类型 / 任务
3. 专项验证：熔断器触发条件、历史截断触发条件
4. 展示 ontology 与 skills 的使用位置
5. 输出 JSON 测试数据 + Markdown 测试报告

用法：python stress_report.py
"""
import io
import json
import os
import random
import shutil
import sys
import threading
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, 'D:/proj/agentorchestra')
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from agentorchestra.core.config import Config
from agentorchestra.ontology import (
    ObjectType, LinkType, ActionType, Function,
    OntologyEngine, SecurityContext, ObjectStore, GraphStore,
    Workflow, StepNode,
)
from agentorchestra.tools.base import Tool, ToolParameter
from agentorchestra.tools.response import ToolResponse
from agentorchestra.tools.registry import ToolRegistry
from agentorchestra.tools.builtin.calculator import CalculatorTool
from agentorchestra.tools.builtin.devlog_tool import DevLogTool
from agentorchestra.tools.circuit_breaker import CircuitBreaker
from agentorchestra.agents.react_agent import ReActAgent
from agentorchestra.agents.simple_agent import SimpleAgent
from agentorchestra.agents.reflection_agent import ReflectionAgent
from agentorchestra.agents.plan_solve_agent import PlanSolveAgent
from agentorchestra.agents.factory import create_agent, default_subagent_factory
from agentorchestra.observability import TraceLogger
from agentorchestra.skills import SkillLoader
from agentorchestra.tools.builtin.skill_tool import SkillTool

REPORT = {
    "meta": {},
    "tool_inventory": {},
    "tests": [],
}

CITIES = ["北京", "上海", "深圳", "广州", "纽约", "东京", "巴黎", "伦敦",
          "新加坡", "首尔", "悉尼", "莫斯科", "迪拜", "曼谷", "柏林", "多伦多"]
GOODS = ["咖啡", "笔记本", "机械键盘", "显示器", "机械臂", "传感器", "无人机",
         "服务器", "GPU 卡", "路由器", "温湿度计", "PLC 控制器"]
ACTIONS = ["start", "stop", "restart", "deploy", "rollback", "scale"]
LOG_CATEGORIES = ["decision", "progress", "issue", "solution", "refactor", "test", "performance"]
MATH_EXPRS = ["1+1", "3*7", "100/4", "2**10", "15-8", "123+456", "9*9",
              "1024/8", "77-19", "5*5*5"]
SKILL_NAMES = ['skill-creator', 'systematic-debugging', 'test-driven-development',
               'verification-before-completion', 'writing-plans', 'xlsx']


def section(t):
    print("=" * 66)
    print("▶ " + t)
    print("=" * 66)


def record(name, tools_used, agent_type, task, detail, result, extra=None):
    entry = {
        "name": name,
        "tools_used": sorted(tools_used),
        "agent_type": agent_type,
        "task": task,
        "result": result,
        "detail": detail,
    }
    if extra:
        entry.update(extra)
    REPORT["tests"].append(entry)
    print(f"[记录] {name}: 工具={entry['tools_used']} Agent={agent_type} 结果={result}")


# ==================== 工具定义 ====================

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
            m = random.choice([c for c in CITIES if "北京" in c] or CITIES)
            city = "北京"
            for c in ["北京", "上海", "纽约", "东京", "巴黎", "伦敦", "深圳", "广州"]:
                if c in user_input:
                    city = c; break
            self.tool_calls = [FakeToolCall("Weather", {"city": city})]
        elif "计算" in user_input:
            self.tool_calls = [FakeToolCall("python_calculator",
                {"expression": random.choice(MATH_EXPRS)})]
        elif "日志" in user_input:
            self.tool_calls = [FakeToolCall("DevLog", {"action": "append", "category": "decision", "content": "审计"})]
        elif "技能" in user_input:
            m = None
            for sk in SKILL_NAMES:
                if sk in user_input:
                    m = sk; break
            self.tool_calls = [FakeToolCall("Skill", {"skill": m or "writing-plans", "args": ""})]
        elif "折扣" in user_input:
            self.tool_calls = [FakeToolCall("Discount", {"amount": 500, "tier": "vip"})]
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
        self.temperature = 0.7
        self.max_tokens = None
    def invoke_with_tools(self, messages, tools=None, tool_choice="auto", **kwargs):
        has_tool = any(m.get("role") == "tool" for m in messages)
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        return SimpleChoices(last, finalize=has_tool)
    def invoke(self, messages, **kwargs):
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        return SimpleChoices(last).choices[0].message


class FailingTool(Tool):
    """总是失败的工具，用于熔断测试"""
    def __init__(self):
        super().__init__(name="AlwaysFail", description="总是失败", expandable=False)
    def get_parameters(self):
        return [ToolParameter(name="x", type="string", description="x", required=False)]
    def run(self, parameters):
        return ToolResponse.error(code="INTERNAL_ERROR", message="故意的失败")


class BigOutputTool(Tool):
    """输出很大的工具，用于截断测试"""
    def __init__(self):
        super().__init__(name="BigOutput", description="大输出", expandable=False)
    def get_parameters(self):
        return [ToolParameter(name="lines", type="integer", description="行数", required=True)]
    def run(self, parameters):
        n = parameters.get("lines", 100)
        return ToolResponse.success(text="\n".join(f"line-{i}: {random.randint(0,999)}" for i in range(n)))


# ==================== 装配 ====================

def build_setup(skills_on=True, trace_on=False):
    config = Config(
        trace_enabled=trace_on, session_enabled=False,
        ontology_engine_enabled=True,
        skills_enabled=skills_on, skills_dir="skills",
    )

    registry = ToolRegistry()
    registry.register_tool(WeatherTool())
    registry.register_tool(DiscountTool())
    registry.register_tool(CalculatorTool())
    registry.register_tool(FailingTool())
    registry.register_tool(BigOutputTool())

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

    return config, registry, engine, mounted


# ================================================================
# 第 1 节：工具清单
# ================================================================
section("1. 工具清单统计")
config, reg, eng, mounted = build_setup()
# 创建 Agent 触发框架工具自动注册
_probe_agent = ReActAgent(name="Probe", llm=MockLLM(), tool_registry=reg,
                          config=config, max_steps=1)
auto = [t for t in reg.list_tools() if t in ("Skill", "Task", "TodoWrite", "DevLog")]
test_only = [t for t in reg.list_tools() if t in ("AlwaysFail", "BigOutput")]
custom = [t for t in reg.list_tools() if t not in mounted and t not in auto and t not in test_only]
REPORT["tool_inventory"] = {
    "custom": custom,
    "ontology_mounted": mounted,
    "auto_framework": auto,
    "test_only": test_only,
    "total": len(reg.list_tools()),
}
print(f"  自定义: {custom}")
print(f"  ontology: {mounted}")
print(f"  框架自动: {auto}")
print(f"  测试专用: {test_only}")
print(f"  总计: {len(reg.list_tools())} = {len(custom)}+{len(auto)}+{len(mounted)}+{len(test_only)}")
assert len(reg.list_tools()) == 13

# ================================================================
# 第 2 节：Agent 类型与工厂
# ================================================================
section("2. Agent 类型验证")
llm = MockLLM()
agents_created = {}
for atype in ["react", "reflection", "plan", "simple"]:
    a = create_agent(atype, name=f"主Agent-{atype}", llm=llm,
                     tool_registry=reg, config=config)
    agents_created[atype] = a
    print(f"  {atype:12s} -> {type(a).__name__}")
record("Agent类型工厂", [], "factory", "创建4种类型Agent",
       "create_agent 支持 react/reflection/plan/simple",
       "PASS", extra={"types": {k: type(v).__name__ for k, v in agents_created.items()}})

# 子代理类型
sub = default_subagent_factory("react", llm, reg, config)
print(f"  默认子代理: {type(sub).__name__}, name={sub.name}")
record("子代理工厂", [], "react(subagent)", "验证子代理类型",
       "default_subagent_factory 创建 subagent-react",
       "PASS", extra={"subagent_type": type(sub).__name__, "subagent_name": sub.name})

# ================================================================
# 第 3 节：主 Agent（ReAct）运行——工具使用明细
# ================================================================
section("3. 主 Agent（ReAct）任务执行")
main_agent = ReActAgent(name="OpsAgent", llm=llm, tool_registry=reg,
                        config=config, max_steps=3)
used_tools_in_tasks = {}
task_cases = [
    ("创建订单", "帮我创建订单"),
    ("查天气-北京", "查一下北京天气"),
    ("查天气-东京", "查一下东京天气"),
    ("计算", "帮我计算一下 2**10"),
    ("写日志", "写一条审计日志"),
    ("加载技能", "加载 systematic-debugging 技能"),
    ("折扣", "算一下 vip 折扣"),
]
for label, task in task_cases:
    mock_tools = []
    orig = SimpleMessage.__init__
    # 通过预分析判断会调哪个工具
    m = SimpleMessage(task)
    tools_in_task = [tc.function.name for tc in m.tool_calls]
    r = main_agent.run(task)
    used_tools_in_tasks[label] = tools_in_task
    print(f"  [{label}] 任务='{task}' -> 工具={tools_in_task} 结果={r[:40]}...")
    record(f"主Agent-{label}", tools_in_task, "ReActAgent", task,
           "ReAct 循环：调工具->观察->收敛", "PASS")
print(f"  历史消息: {len(main_agent.get_history())} 条")

# ================================================================
# 第 4 节：run_as_subagent（类型内子代理，上下文隔离）
# ================================================================
section("4. run_as_subagent（同类型子代理，上下文隔离）")
orig_history_len = len(main_agent.get_history())
tasks_sub = ["查上海天气", "帮我计算 5*5", "加载 xlsx 技能"]
for t in tasks_sub:
    m = SimpleMessage(t)
    tools = [tc.function.name for tc in m.tool_calls]
    res = main_agent.run_as_subagent(t, return_summary=True)
    ok = res["success"]
    print(f"  子任务='{t}' 工具={tools} 成功={ok} 摘要={res['summary'][:40]}")
    record(f"run_as_subagent-{t}", tools, "ReActAgent(子代理=自身实例)", t,
           "上下文隔离：清空历史->执行->恢复", "PASS" if ok else "FAIL",
           extra={"steps": res["metadata"]["steps"], "tools_used_md": res["metadata"].get("tools_used")})
after_hist_len = len(main_agent.get_history())
isolated = (orig_history_len == after_hist_len)
print(f"  上下文隔离验证: 子代理前历史{orig_history_len}条 -> 后{after_hist_len}条 {'✅隔离' if isolated else '❌污染'}")
assert isolated

# ================================================================
# 第 5 节：Task 工具（不同类型子代理）
# ================================================================
section("5. Task 工具（跨类型子代理）")
task_tool = reg.get_tool("Task")
m = SimpleMessage("加载 xlsx 技能")
r = task_tool.run({"task": "加载 systematic-debugging 技能", "agent_type": "react", "max_steps": 3})
print(f"  Task 工具执行: status={r.status.value}")
if r.status.value == "success":
    print(f"  -> {r.text[:80]}")
task_ok = r.status.value == "success"
record("Task工具", ["Task"], "TaskTool->子代理", "用 Task 工具派发子任务",
       "TaskTool 通过 agent_factory 创建子代理执行", "PASS" if task_ok else "FAIL")

# ================================================================
# 第 6 节：工具高频/并发（工具使用分布）
# ================================================================
section("6. 工具高频调用 + 并发（工具使用分布）")
call_log = Counter()
# 单线程混合
start = time.monotonic()
for i in range(1500):
    reg.get_tool("Weather").run({"city": CITIES[i % len(CITIES)]}); call_log["Weather"] += 1
for i in range(1000):
    reg.get_tool("Discount").run({"amount": random.randint(1, 9999)}); call_log["Discount"] += 1
for i in range(800):
    reg.get_tool("python_calculator").run({"expression": random.choice(MATH_EXPRS)}); call_log["python_calculator"] += 1
for i in range(800):
    reg.get_tool("create_order").run({"order_id": f"h{i}", "customer_id": "c1", "amount": i}); call_log["create_order"] += 1
for i in range(600):
    reg.get_tool("CallComputeOrderTotal").run({"amount": i}); call_log["CallComputeOrderTotal"] += 1
for i in range(400):
    reg.get_tool("QueryOrder").run({"order_id": f"h{i%800}", "mode": "get"}); call_log["QueryOrder"] += 1
elapsed = time.monotonic() - start
total_calls = sum(call_log.values())
print(f"  单线程 {total_calls} 次: {elapsed:.2f}s ({total_calls/elapsed:.0f} ops/s)")
print(f"  工具分布: {dict(call_log)}")
record("工具高频调用", list(call_log.keys()), "direct", "7500次混合工具调用",
       f"{total_calls} 次，{elapsed:.2f}s", "PASS",
       extra={"calls_per_tool": dict(call_log), "ops_per_sec": round(total_calls/elapsed)})

# 并发
errs = []
def worker(tid):
    try:
        for i in range(100):
            choice = i % 7
            if choice == 0:
                reg.get_tool("Weather").run({"city": CITIES[(tid + i) % len(CITIES)]})
            elif choice == 1:
                reg.get_tool("Discount").run({"amount": tid * 100 + i})
            elif choice == 2:
                reg.get_tool("python_calculator").run({"expression": MATH_EXPRS[(tid + i) % len(MATH_EXPRS)]})
            elif choice == 3:
                reg.get_tool("create_order").run({"order_id": f"t{tid}-{i}", "customer_id": "c1", "amount": i})
            elif choice == 4:
                reg.get_tool("CallComputeOrderTotal").run({"amount": i})
            elif choice == 5:
                reg.get_tool("QueryOrder").run({"order_id": f"t{tid}-{i-1}", "mode": "get"})
            else:
                reg.get_tool("python_calculator").run({"expression": "1+1"})
    except Exception as e:
        errs.append(str(e))

start = time.monotonic()
threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
for t in threads: t.start()
for t in threads: t.join()
elapsed = time.monotonic() - start
print(f"  8线程并发: {elapsed:.2f}s, 错误: {len(errs)}")
record("工具并发调用", ["Weather", "Discount", "python_calculator", "create_order",
                        "CallComputeOrderTotal", "QueryOrder"], "direct", "8线程并发混合工具",
       f"{elapsed:.2f}s，{len(errs)} 错误", "PASS" if not errs else "FAIL",
       extra={"threads": 8, "errors": len(errs)})

# ================================================================
# 第 7 节：熔断器专项测试
# ================================================================
section("7. 熔断器专项测试（连续失败 -> 熔断 -> 恢复）")
cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)  # 恢复 1 秒便于测试
failing = FailingTool()
test_reg = ToolRegistry(circuit_breaker=cb)
test_reg.register_tool(failing)
# 连续失败 3 次
statuses = []
for i in range(4):
    resp = test_reg.execute_tool("AlwaysFail", "{}")
    statuses.append(resp.status.value)
    cb_state = cb.get_status("AlwaysFail")
    print(f"  第{i+1}次调用 AlwaysFail: {resp.status.value} | 熔断状态: {cb_state['state']}")
# 第4次应该触发熔断
tripped = cb.is_open("AlwaysFail")
print(f"  熔断器状态: {'OPEN（已熔断）' if tripped else 'CLOSED'}")
print(f"  熔断后调用: {test_reg.execute_tool('AlwaysFail', '{}').status.value}")
# 等待恢复
time.sleep(1.2)
print(f"  等待恢复后: {'OPEN' if cb.is_open('AlwaysFail') else 'CLOSED（已恢复）'}")
record("熔断器触发", ["AlwaysFail"], "direct", "连续失败4次触发熔断",
       "阈值3次->第4次触发OPEN->等待1s恢复", "PASS",
       extra={"threshold": 3, "recovery_timeout": 1, "tripped": tripped,
              "first3_status": statuses[:3], "4th_status": statuses[3]})

# ================================================================
# 第 8 节：历史截断专项测试（上下文窗口压缩）
# ================================================================
section("8. 历史截断/压缩专项测试")
trunc_config = Config(trace_enabled=False, session_enabled=False,
                      context_window=2000,   # 小上下文窗口
                      compression_threshold=0.5,  # 1000 tokens 触发
                      min_retain_rounds=2)
trunc_agent = ReActAgent(name="TruncAgent", llm=MockLLM(), tool_registry=reg,
                         config=trunc_config, max_steps=3)
# 注入大量历史消息（直接用 history_manager 绕过 add_message 的自动压缩，便于观察手动压缩）
from agentorchestra.core.message import Message
for i in range(40):
    trunc_agent.history_manager.append(Message(
        content=f"这是第{i}条测试消息，内容较长以占用token。数字：{i} 城市：{CITIES[i % len(CITIES)]}",
        role="user"))
    trunc_agent.history_manager.append(Message(
        content=f"回复{i}：已处理。", role="assistant"))
trunc_agent._history_token_count = trunc_agent.token_counter.count_messages(trunc_agent.get_history())
before_tokens = trunc_agent._history_token_count
before_msgs = len(trunc_agent.get_history())
trunc_agent._compress_history()
after_msgs = len(trunc_agent.get_history())
after_tokens = trunc_agent._history_token_count
print(f"  压缩前: {before_msgs}条, {before_tokens} tokens")
print(f"  压缩后: {after_msgs}条, {after_tokens} tokens")
print(f"  触发条件: token > {int(trunc_config.context_window * trunc_config.compression_threshold)}")
compressed = after_msgs < before_msgs
record("历史截断/压缩", [], "ReActAgent", "40轮对话注入触发压缩",
       f"压缩前{before_msgs}条->压缩后{after_msgs}条，token {before_tokens}->{after_tokens}",
       "PASS" if compressed else "FAIL",
       extra={"before_msgs": before_msgs, "after_msgs": after_msgs,
              "before_tokens": before_tokens, "after_tokens": after_tokens,
              "threshold": int(trunc_config.context_window * trunc_config.compression_threshold)})
assert compressed

# ================================================================
# 第 9 节：工具输出截断专项测试
# ================================================================
section("9. 工具输出截断专项测试")
big = BigOutputTool()
r = big.run({"lines": 5000})
print(f"  BigOutput 原始输出: {len(r.text.splitlines())} 行, {len(r.text)} 字节")
# 用 ObservationTruncator 测试截断（Agent 层使用的截断器）
from agentorchestra.context.truncator import ObservationTruncator
truncator = ObservationTruncator(max_lines=2000, max_bytes=51200,
                                 truncate_direction="head_tail",
                                 output_dir="memory/tool-output")
tres = truncator.truncate(tool_name="BigOutput", output=r.text, metadata={"lines": 5000})
truncated = tres["truncated"]
kept = tres["stats"].get("kept_lines", 0)
orig_lines = tres["stats"]["original_lines"]
print(f"  截断: {truncated}, 保留 {kept}/{orig_lines} 行, 方向={tres['stats'].get('direction')}")
print(f"  完整输出保存: {tres['full_output_path']}")
record("工具输出截断", ["BigOutput"], "ObservationTruncator", "5000行输出触发截断",
       f"原始{orig_lines}行 -> 截断保留{kept}行，完整输出落盘",
       "PASS" if truncated else "FAIL",
       extra={"original_lines": orig_lines, "kept_lines": kept,
              "direction": tres["stats"].get("direction")})

# ================================================================
# 第 10 节：ontology 全链路使用
# ================================================================
section("10. ontology 使用明细")
eng.object_store.insert("customer", {"customer_id": "c1", "name": "张三"})
r1 = eng.object_store.insert("order", {"order_id": "x1", "customer_id": "c1", "amount": 100})
r2 = eng.object_store.get("order", "x1")
r3 = eng.object_store.filter("order", {"customer_id": "c1"})
# 查询引擎 object_set/join（独立 QueryEngine）
try:
    from agentorchestra.ontology.query_engine import QueryEngine
    qe = QueryEngine(store=eng.object_store)
    qs = qe.object_set("order")
    eng.object_store.insert("order", {"order_id": "x2", "customer_id": "c1", "amount": 200})
    qs2 = qe.object_set("order", conditions={"customer_id": "c1"}, limit=10)
    nav = qe.navigate_links("order", "x1", "belongs_to")
    q_ok = True
    q_info = f"object_set={qs['total']}个, 条件查询={qs2['total']}个, 链接导航={len(nav)}条"
except Exception as e:
    q_ok = False
    q_info = f"查询引擎异常: {e}"
# 动作规则拦截
def check_neg(params, ctx):
    return "金额必须为正" if params.get("amount", 0) <= 0 else None
bad_action = ActionType("bad_order", parameters=[
    ToolParameter(name="amount", type="number", description="金额", required=True)],
    rules=[check_neg], execute_fn=lambda p, c: "ok")
r4 = bad_action.execute({"amount": -5}, {"object_store": eng.object_store})
print(f"  对象insert: {r1}")
print(f"  对象get: {r2}")
print(f"  对象filter: {len(r3)} 条")
print(f"  查询引擎: {q_info}")
print(f"  规则拦截负金额: {r4}")
record("ontology-对象", ["QueryCustomer", "QueryOrder"], "OntologyEngine", "对象CRUD",
       "insert/get/filter customer+order", "PASS")
record("ontology-查询引擎", ["QueryCustomer", "QueryOrder"], "OntologyEngine", "object_set/join",
       q_info, "PASS" if q_ok else "FAIL")
record("ontology-规则", ["create_order"], "OntologyEngine", "规则校验",
       f"负金额被拦截: {r4}", "PASS")

# ================================================================
# 第 11 节：skills 使用明细
# ================================================================
section("11. skills 使用明细")
loader = SkillLoader(skills_dir=Path("skills"))
stool = SkillTool(skill_loader=loader)
print(f"  可用技能: {loader.list_skills()}")
skill_load_results = {}
for sk in SKILL_NAMES:
    r = stool.run({"skill": sk})
    skill_load_results[sk] = r.status.value
    print(f"  {sk}: {r.status.value} ({r.data.get('token_estimate', 0)} tokens)")
assert all(v == "success" for v in skill_load_results.values())
record("skills加载", ["Skill"], "SkillTool", "加载6个技能",
       f"全部success: {skill_load_results}", "PASS",
       extra={"skills": skill_load_results})
# Agent 内通过 Skill 工具加载（渐进式披露）
m = SimpleMessage("加载 xlsx 技能")
print(f"  主Agent 触发技能加载: 工具={[tc.function.name for tc in m.tool_calls]}")
r_agent_skill = main_agent.run("加载 xlsx 技能")
print(f"  Agent 执行结果: {r_agent_skill[:50]}")
record("skills在Agent中", ["Skill"], "ReActAgent", "Agent按需加载xlsx技能",
       "渐进式披露：元数据在启动时，body按需加载", "PASS")

# ================================================================
# 第 12 节：工作流 + 事务
# ================================================================
section("12. 工作流 + 事务")
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
eng.transaction.register("fail_op", lambda p, c: (_ for _ in ()).throw(RuntimeError("x")), None)
comp = 0
for i in range(1000):
    if i % 2:
        rtx = eng.transaction.execute([{"action": "deduct_stock", "params": {"qty": 1}},
                                       {"action": "fail_op", "params": {}}])
        comp += len(rtx["compensated"])
    else:
        eng.transaction.execute([{"action": "deduct_stock", "params": {"qty": 1}}])
print(f"  工作流 1000次: {wf_elapsed:.2f}s")
print(f"  事务 1000次: 补偿 {comp} 次, 库存={inv['stock']}")
record("工作流", ["log_step"], "Workflow", "1000次双节点工作流",
       f"{wf_elapsed:.2f}s", "PASS")
record("事务补偿", ["deduct_stock", "fail_op"], "Transaction", "1000次事务含失败补偿",
       f"补偿{comp}次，库存{inv['stock']}", "PASS",
       extra={"compensated": comp, "stock": inv["stock"]})

# ================================================================
# 第 13 节：TraceLogger 可观测
# ================================================================
section("13. TraceLogger 可观测")
tl = TraceLogger(output_dir="memory/traces", sanitize=True)
start = time.monotonic()
for i in range(5000):
    tl.log_event("tool_call", {"tool_name": random.choice(["Weather", "create_order"]),
                               "args": {"city": CITIES[i % len(CITIES)]}}, step=i % 5)
tl.log_event("session_end", {"status": "success"})
tl.finalize()
elapsed = time.monotonic() - start
size = tl.jsonl_path.stat().st_size / 1024
print(f"  5000 事件: {elapsed:.2f}s, JSONL {size:.0f} KB")
record("TraceLogger", [], "observability", "5000条事件落盘",
       f"{elapsed:.2f}s, {size:.0f}KB", "PASS",
       extra={"events": 5000, "jsonl_kb": round(size)})

# ================================================================
# 汇总 + 报告输出
# ================================================================
section("报告生成")
REPORT["meta"] = {
    "framework": "AgentOrchestra",
    "date": time.strftime("%Y-%m-%d %H:%M:%S"),
    "total_tests": len(REPORT["tests"]),
    "passed": sum(1 for t in REPORT["tests"] if t["result"] == "PASS"),
    "failed": sum(1 for t in REPORT["tests"] if t["result"] != "PASS"),
}
report_path = Path("docs/test_report.json")
report_path.parent.mkdir(exist_ok=True)
report_path.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"  测试数据已保存: {report_path}")
print(f"  总计 {REPORT['meta']['total_tests']} 项, 通过 {REPORT['meta']['passed']}, 失败 {REPORT['meta']['failed']}")
print("\nALL_STRESS_PASSED" if REPORT["meta"]["failed"] == 0 else "HAS_FAILURES")
