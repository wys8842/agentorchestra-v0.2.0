# -*- coding: utf-8 -*-
"""AgentOrchestra 全功能综合案例：智能运维助手 Agent

从 Agent 层面开始的完整框架流程，覆盖所有模块：
- core: Config / Message / 会话 / 日志 / 指标 / 追踪 / 限流 / 健康检查 / 监控端点
- agents: ReActAgent + 子代理
- tools: ToolRegistry / 自定义Tool / 内置工具 / TaskTool / 工具过滤
- context: HistoryManager / TokenCounter / 压缩
- observability: TraceLogger
- ontology: 对象/动作/函数/接口 + mount到Agent + 治理 + 工作流/事务/调度

场景：运维助手 Agent 处理"客户下单支付"业务，全程可观测、可治理、可监控。
"""
import json
import time

# 标准领域路径（不需要 sys.path 注入；安装后自然可导入）
from agentorchestra.runtime.agents.react_agent import ReActAgent
from agentorchestra.runtime.core.config import Config
from agentorchestra.runtime.core.health import HealthCheck
from agentorchestra.runtime.core.logging import get_logger, setup_logging
from agentorchestra.runtime.core.message import Message
from agentorchestra.runtime.core.metrics import get_metrics
from agentorchestra.runtime.core.monitor import MonitorServer
from agentorchestra.runtime.core.ratelimit import RateLimiter
from agentorchestra.runtime.core.tracing import MemoryExporter, get_tracer

from agentorchestra.ontology import (
    ActionType,
    Function,
    GraphStore,
    Interface,
    LinkType,
    ObjectStore,
    ObjectType,
    OntologyEngine,
    Scheduler,
    SecurityContext,
    StepNode,
    Workflow,
)

from agentorchestra.capability.tools.base import Tool, ToolParameter
from agentorchestra.capability.tools.builtin.calculator import CalculatorTool
from agentorchestra.capability.tools.registry import ToolRegistry
from agentorchestra.capability.tools.response import ToolResponse


def section(t):
    print("=" * 70)
    print("▶ " + t)
    print("=" * 70)


# ==================== ① 全局配置与可观测基础设施 ====================
section("① 全局配置 + 日志 + 指标 + 追踪 + 限流")

config = Config(
    default_model="mock-model",
    trace_enabled=True,
    trace_dir="memory/traces",
    session_enabled=True,
    session_dir="memory/sessions",
    ontology_engine_enabled=True,
    context_window=128000,
)

setup_logging(level="INFO", json_format=False)
logger = get_logger("demo.main")
logger.info("框架初始化完成", extra={"phase": "bootstrap"})

metrics = get_metrics()
tracer = get_tracer(MemoryExporter())
limiter = RateLimiter(default_limit=100, window_seconds=60)
print("  配置: model=%s trace=%s session=%s ontology=%s" % (
    config.default_model, config.trace_enabled, config.session_enabled,
    config.ontology_engine_enabled))
print("  限流器: 每60秒 %d 次" % limiter.default_limit)

# ==================== ② 自定义工具 ====================
section("② 自定义工具 + 内置工具（tools）")


class WeatherTool(Tool):
    """自定义工具：查天气"""
    def __init__(self):
        super().__init__(name="Weather", description="查询城市天气", expandable=False)

    def get_parameters(self):
        return [ToolParameter(name="city", type="string", description="城市", required=True)]

    def run(self, parameters):
        city = parameters.get("city")
        return ToolResponse.success(text=f"{city} 今天晴 25°C")


class DiscountTool(Tool):
    """自定义工具：计算折扣"""
    def __init__(self):
        super().__init__(name="Discount", description="按会员等级计算折扣", expandable=False)

    def get_parameters(self):
        return [
            ToolParameter(name="amount", type="number", description="金额", required=True),
            ToolParameter(name="tier", type="string", description="会员等级", required=False, default="standard"),
        ]

    def run(self, parameters):
        amount = parameters.get("amount", 0)
        tier = parameters.get("tier", "standard")
        rate = {"gold": 0.2, "silver": 0.1}.get(tier, 0)
        return ToolResponse.success(text=f"折后: {amount * (1 - rate)}")


registry = ToolRegistry()
registry.register_tool(WeatherTool())
registry.register_tool(DiscountTool())
registry.register_tool(CalculatorTool())
print("  工具注册: %s" % registry.list_tools())

# ==================== ③ ontology 业务域 ====================
section("③ ontology 业务域：对象/动作/函数/接口")

Customer = ObjectType("customer", "customer_id", properties=[
    ToolParameter(name="customer_id", type="string", description="客户ID", required=True),
    ToolParameter(name="name", type="string", description="客户名", required=True),
    ToolParameter(name="tier", type="string", description="等级", default="standard"),
])
Order = ObjectType("order", "order_id", properties=[
    ToolParameter(name="order_id", type="string", description="订单ID", required=True),
    ToolParameter(name="customer_id", type="string", description="客户ID", required=True),
    ToolParameter(name="amount", type="number", description="金额", required=True),
    ToolParameter(name="status", type="string", description="状态", default="pending"),
], link_types=[LinkType("belongs_to", "order", "customer")])


def check_amount(params, ctx):
    if params.get("amount", 0) <= 0:
        return "金额必须为正"
    return None


def do_create_order(params, ctx):
    return ctx["object_store"].insert("order", {
        "order_id": params["order_id"], "customer_id": params["customer_id"],
        "amount": params["amount"], "status": "pending"})


CreateOrder = ActionType("create_order", parameters=[
    ToolParameter(name="order_id", type="string", description="ID", required=True),
    ToolParameter(name="customer_id", type="string", description="客户", required=True),
    ToolParameter(name="amount", type="number", description="金额", required=True),
], rules=[check_amount], execute_fn=do_create_order)


def compute_total(args, ctx):
    return {"with_tax": round(args.get("amount", 0) * 1.13, 2)}


ComputeTotal = Function("compute_order_total", impl=compute_total,
                        arguments=[ToolParameter(name="amount", type="number", description="金额", required=True)])

PayableIface = Interface("payable", required_properties=["amount", "status"])

engine = OntologyEngine(object_store=ObjectStore(graph=GraphStore()),
                        security_ctx=SecurityContext("agent", ["agent", "admin"]))
engine.register_object_type(Customer)
engine.register_object_type(Order)
engine.register_action(CreateOrder)
engine.register_function(ComputeTotal)
engine.register_interface(PayableIface)
engine.implement_interface("payable", "order")
engine.allow(["agent", "admin"], resource="*", action="*")

print("  Ontology 能力清单:")
print("    " + engine.describe().replace("\n", "\n    "))

# ==================== ④ mount 到 registry（Agent 工具） ====================
section("④ mount ontology 到工具注册表（Agent 可用）")
mounted = engine.mount(registry)
print("  全部工具: %s" % (registry.list_tools()))
print("  Ontology 工具: %s" % mounted)

# ==================== ⑤ 构建 ReActAgent ====================
section("⑤ 构建 ReActAgent（agents）")


class MockLLM:
    """模拟 LLM：演示用（真实场景换成 SymphonyLLM）"""
    def __init__(self, model="mock-model"):
        self.model = model

    def invoke_with_tools(self, messages, tools=None, tool_choice="auto", **kwargs):
        # 决策：如果已有工具结果（tool role）→ 返回最终答案；否则返回预设动作
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        if has_tool_result:
            last_user = next((m["content"] for m in reversed(messages)
                              if m.get("role") == "user"), "")
            return SimpleChoices(last_user, finalize=True)
        last_user = next((m["content"] for m in reversed(messages)
                          if m.get("role") == "user"), "")
        return SimpleChoices(last_user)

    def invoke(self, messages, **kwargs):
        last_user = next((m["content"] for m in reversed(messages)
                          if m.get("role") == "user"), "")
        return SimpleResponse(f"已处理: {last_user}")


class SimpleResponse:
    def __init__(self, content):
        self.content = content
        self.usage = {"total_tokens": 10}
        self.latency_ms = 5
        self.reasoning_content = None


class SimpleChoices:
    """根据输入返回不同的 tool_calls 决策"""
    def __init__(self, user_input, finalize=False):
        # choices[0] 需要是含 .message 的对象
        self.choices = [SimpleChoice(user_input, finalize)]
        self.usage = SimpleUsage()


class SimpleUsage:
    def __init__(self):
        self.total_tokens = 10
        self.prompt_tokens = 5
        self.completion_tokens = 5


class SimpleChoice:
    """mock choice：包含 .message 属性"""
    def __init__(self, user_input, finalize=False):
        self.message = SimpleMessage(user_input, finalize)


class SimpleMessage:
    def __init__(self, user_input, finalize=False):
        self.content = None
        self.tool_calls = []
        if finalize:
            # 已有工具结果 → 返回最终答案（Agent 收敛）
            self.content = f"任务完成: {user_input}"
            return
        # 决策逻辑：包含"创建订单"→ 调 create_order；包含"查天气"→ Weather
        if "创建订单" in user_input:
            self.tool_calls = [FakeToolCall("create_order",
                {"order_id": "o1", "customer_id": "c1", "amount": 99.0})]
        elif "天气" in user_input:
            self.tool_calls = [FakeToolCall("Weather", {"city": "北京"})]
        else:
            self.content = f"已理解: {user_input}"


class FakeToolCall:
    def __init__(self, name, args):
        self.id = f"call_{name}"
        self.type = "function"
        self.function = FakeFunction(name, args)


class FakeFunction:
    def __init__(self, name, args):
        self.name = name
        self.arguments = json.dumps(args)


agent = ReActAgent(
    name="OpsAgent",
    llm=MockLLM(),
    tool_registry=registry,
    system_prompt="你是智能运维助手，负责处理客户订单和查询业务。",
    config=config,
    max_steps=5,
)
print("  Agent 创建: %s, model=%s" % (agent.name, agent.llm.model))
print("  TraceLogger: session_id=%s" % agent.trace_logger.session_id)

# ==================== ⑥ Agent 运行：完整业务流 ====================
section("⑥ Agent 运行：处理订单业务")

# 限流检查
print("  限流: user_a 允许=%s" % limiter.try_acquire("user_a"))

# 记录指标
metrics.request_start()

# Agent 创建订单（工具链路：create_order → 规则校验 → 写对象）
print("\n[任务1] 创建订单")
result = agent.run("帮我创建订单")
print("  结果: %s" % result)

# Agent 查天气（工具链路：Weather）
print("\n[任务2] 查天气")
result2 = agent.run("查一下北京天气")
print("  结果: %s" % result2)

metrics.request_end()
print("  LLM 指标: %s" % ("已记录" if metrics.is_available else "降级"))

# ==================== ⑦ 会话与历史管理（context） ====================
section("⑦ 会话与历史管理（context）")

print("  history 消息数: %d" % len(agent.get_history()))
print("  历史 Token 数: %d" % agent._history_token_count)

# 演示 add_message + 压缩检查
agent.add_message(Message("测试消息", "user"))
print("  追加后 history: %d 条, tokens=%d" % (
    len(agent.get_history()), agent._history_token_count))

# ==================== ⑧ 子代理机制 ====================
section("⑧ 子代理机制（TaskTool + 子代理）")


class MockSubLLM(MockLLM):
    pass


sub_registry = ToolRegistry()
sub_registry.register_tool(WeatherTool())
sub_registry.register_tool(CalculatorTool())

sub_agent = ReActAgent(
    name="SubAgent-weather",
    llm=MockSubLLM(),
    tool_registry=sub_registry,
    config=config,
    max_steps=3,
)

# 通过 run_as_subagent 隔离执行（模拟 TaskTool 子代理）
r = sub_agent.run_as_subagent("查北京天气", return_summary=True)
print("  子代理摘要: %s" % r["summary"])
print("  子代理元数据: %s" % r["metadata"])

# ==================== ⑨ 工作流编排（process） ====================
section("⑨ 工作流编排（process/workflow）")


def exec_log(params, ctx):
    return {"ok": True, "msg": params.get("msg")}


engine.register_action(ActionType("step_a", parameters=[
    ToolParameter(name="msg", type="string", description="消息", required=True)],
    execute_fn=exec_log))
engine.register_action(ActionType("step_b", parameters=[
    ToolParameter(name="msg", type="string", description="消息", required=True)],
    execute_fn=exec_log))

wf = Workflow("fulfill_flow")
wf.add_node(StepNode("s1", "step_a", {"msg": "校验订单"}), entry=True)
wf.add_node(StepNode("s2", "step_b", {"msg": "发货"}, depends_on=["s1"]))
engine.workflow.register_workflow(wf)
wr = engine.workflow.run("fulfill_flow", ctx={"object_store": engine.object_store})
print("  工作流: success=%s, 节点=%d" % (wr["success"], len(wr["results"])))

# ==================== ⑩ 事务补偿 + 调度 ====================
section("⑩ 事务补偿 + 定时调度（process）")

inv = {"stock": 10}
engine.transaction.register("扣库存", lambda p, c: inv.__setitem__("stock", inv["stock"] - p.get("qty", 1)),
                            lambda p, c: inv.__setitem__("stock", inv["stock"] + p.get("qty", 1)))
engine.transaction.register("扣款", lambda p, c: (_ for _ in ()).throw(RuntimeError("余额不足")), None)
tr = engine.transaction.execute([
    {"action": "扣库存", "params": {"qty": 3}},
    {"action": "扣款", "params": {"amount": 100}}])
print("  事务: 失败=%s, 补偿=%s, 库存恢复=%s" % (tr["failed"], tr["compensated"], inv["stock"]))

sched = Scheduler()
sched_calls = []
sched.add_interval("health_tick", lambda p: sched_calls.append(time.monotonic()),
                   interval_seconds=0.1, max_runs=3)
sched.start()
time.sleep(0.5)
sched.stop()
print("  调度: 定时任务执行 %d 次" % len(sched_calls))

# ==================== ⑪ 治理：权限 + 审计 + 分支 ====================
section("⑪ 治理：权限 + 审计 + 分支（governance）")

viewer = SecurityContext("viewer", ["viewer"])
engine.allow(["viewer"], resource="order", action="read")
print("  viewer 读 order: %s" % engine.security.check("order", "read", viewer))
print("  viewer 写 order: %s" % engine.security.check("order", "write", viewer))

engine.audit.log("agent", "order", "create", detail={"order_id": "o1"}, success=True)
print("  审计日志: %d 条" % len(engine.audit.query()))

engine.snapshot_branch("before_reset")
engine.object_store.insert("order", {"order_id": "temp", "customer_id": "c1", "amount": 1})
engine.switch_branch("before_reset")
print("  分支回滚后 order 数: %d" % engine.object_store.count("order"))

# ==================== ⑫ 查询引擎 ====================
section("⑫ 查询引擎（query_engine）")

engine.object_store.insert("customer", {"customer_id": "c1", "name": "张三"})
engine.object_store.insert("order", {"order_id": "o1", "customer_id": "c1", "amount": 99.0, "status": "pending"})

oset = engine.query.object_set("order", conditions={"status": "pending"}, limit=10)
print("  object_set: total=%d" % oset["total"])
joined = engine.query.describe_join("order", "belongs_to", "customer")
print("  join: %d 条" % len(joined))

# ==================== ⑬ 健康检查 + 监控端点 ====================
section("⑬ 健康检查 + 监控端点（monitor）")

hc = HealthCheck("ops-agent")
hc.register_basic()
hc.register_store_check(engine.object_store)
print("  健康检查: %s" % hc.check()["status"])

monitor = MonitorServer(
    host="127.0.0.1", port=0,
    health_check=hc,
    metrics_provider=metrics.generate_latest,
    traces_provider=tracer.export_all,
)
monitor.start()
port = monitor._server.server_address[1]
print("  监控端点已启动: http://127.0.0.1:%d  (health/metrics/traces)" % port)
monitor.stop()

# ==================== ⑭ 汇总 ====================
section("⑭ 框架全景能力汇总")

summary = {
    "core": ["Config", "Message", "logging", "metrics", "tracing", "ratelimit", "health", "monitor"],
    "agents": ["ReActAgent", "子代理"],
    "tools": ["ToolRegistry", "自定义Tool", "CalculatorTool", "ToolFilter"],
    "context": ["HistoryManager", "TokenCounter"],
    "observability": ["TraceLogger"],
    "ontology": ["对象/动作/函数/接口", "治理", "工作流", "事务", "调度", "查询引擎"],
}
for layer, caps in summary.items():
    print("  %s: %s" % (layer, ", ".join(caps)))

print("\n✅ Agent 层面全功能案例运行成功")
