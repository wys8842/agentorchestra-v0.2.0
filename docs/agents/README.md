# agents：Agent 范式实现（Simple / ReAct / Reflection / PlanSolve / Loop）

> 本模块（`agentorchestra.agents`，规范路径 `agentorchestra.runtime.agents`）提供五种开箱即用的 Agent 范式、统一工厂函数与子代理默认工厂，全部建立在 `runtime.core.agent.Agent` 基类之上。

## 设计动机与原则

1. **同一基类、多种认知范式**。所有 Agent 继承 `runtime.core.agent.Agent`（`Agent(name, llm, system_prompt, config, tool_registry)`），`name/llm/config/工具`装配方式一致；差异只在"如何组织推理循环"。用户在五档复杂度之间切换几乎不动装配代码。
2. **按范式单文件、单一职责**。`simple_agent.py`（对话 + 可选工具）、`react_agent.py`（推理-行动循环）、`reflection_agent.py`（执行-反思-优化）、`plan_solve_agent.py`（规划-执行，内含独立 `Planner`/`Executor`）、`loop_agent.py`（显式认知闭环），可分别维护与演进。
3. **Function Calling 是统一执行协议**。各 Agent 都通过 `llm.invoke_with_tools(messages, tools, tool_choice="auto")` 驱动；工具以 OpenAI 风格 JSON Schema 暴露，内置"认知工具"（`Thought`/`Finish`）与用户工具共用一套消息往返，天然对齐主流模型。
4. **工具执行收敛到单一入口**。`ToolRegistry`（`capability.tools.registry`）是唯一执行通道：熔断检查、观测埋点、结果记录、contextvars 临时过滤都在 registry 内完成；`Agent` 基类再提供 `_build_tool_schemas / _execute_single_tool_call` 等公共方法，把同步/异步/流式三套循环中重复的工具处理（注释中注明 6 处）收敛成一次实现。
5. **三种执行形态统一接口**。`run`（同步）、`arun`（异步，含生命周期钩子）、`arun_stream`（异步流，产出 `StreamEvent`），参数 `on_start/on_step/on_tool_call/on_finish/on_error` 语义在各范式间保持一致，便于上层切换。
6. **向后兼容是显式约束**。`PlanAndSolveAgent` 作为 `PlanSolveAgent` 别名保留、工厂 `agent_type` 同时接受 `plan`/`plan-solve` 语义、`add_tool/remove_tool` 保留但发 `DeprecationWarning` 指引到 `register_tool/unregister_tool`；`LoopAgent` 的新特性（反思/再规划）默认关闭。
7. **复杂能力按开关递进**。LoopAgent 从"简单模式（无工具即停 + max_steps）"起步，`enable_reflection/enable_replan/max_replans/max_consecutive_errors/stuck_threshold` 全默认关闭；避免默认行为突变破坏老用户。
8. **统一工厂与子代理复用**。`create_agent` 用字符串选择范式；`default_subagent_factory` 供 `SubAgentCapability`/`TaskTool` 在"Agent 调用子 Agent"场景里按名创建带默认 system_prompt 的子代理。

## 设计优势

- 装配与范式解耦：换 Agent 类型只需改工厂参数，不需要改 LLM、工具注册或配置对象。
- 一个 Agent 同时具备同步、异步、流式三种入口，Web/CLI/长任务场景都能接。
- 工具调用自动获得熔断、截断、观测、并发限流（`max_concurrent_tools`）等横切能力，范式代码本身保持精简。
- 生命周期钩子 + 流式事件让"执行过程"可观测、可断点、可接 SSE（`stream_to_sse`）。
- 新范式只实现"循环策略"，其余（历史、Token、子代理、持久化、能力注入）由基类免费提供。

## 模块构成

物理路径 | 子模块职责 | 主要公开导出
--- | --- | ---
`agentorchestra/runtime/agents/__init__.py` | 包级聚合导出 | `SimpleAgent`、`ReActAgent`、`ReflectionAgent`、`PlanSolveAgent`、`PlanAndSolveAgent`（向后兼容别名）、`LoopAgent`、`create_agent`、`default_subagent_factory`
`agentorchestra/runtime/agents/factory.py` | Agent 工厂与子代理默认工厂 | `create_agent(agent_type, name, llm, ...)`、`default_subagent_factory(...)`、类型提示 `ToolRegistry`/`Config`
`agentorchestra/runtime/agents/simple_agent.py` | 简单对话 Agent（纯对话 + 可选多轮 Function Calling） | `SimpleAgent`
`agentorchestra/runtime/agents/react_agent.py` | ReAct（推理-行动）Agent | `ReActAgent`、`DEFAULT_REACT_SYSTEM_PROMPT`
`agentorchestra/runtime/agents/reflection_agent.py` | 自我反思与迭代优化 Agent | `ReflectionAgent`、`Memory`（轨迹记忆小工具）
`agentorchestra/runtime/agents/plan_solve_agent.py` | Plan-and-Solve 分解规划 Agent | `PlanSolveAgent`、`Planner`、`Executor`、`_NoOpAgent`（内部桥接类）
`agentorchestra/runtime/agents/loop_agent.py` | 闭环认知循环 Agent | `LoopAgent`、`LoopState`、`LoopStatus`、`Plan`、`Evidence`、`Reflection`、`Budget`、`TerminationDecision`
`agentorchestra/runtime/agents/react_executor.py` | ReAct 循环公共逻辑抽取（内部脚手架） | `ReActExecutor`（ABC）、`BuiltinTools`（**注意：当前未接线，见下方边界说明**）

> 边界说明：`react_executor.py` 中 `ReActExecutor`/`BuiltinTools` 未被任何公开模块 import，且模块顶部存在 `from .builtin_tools import BuiltinTools`——该文件在仓库中不存在，直接 import 本模块会触发 `ModuleNotFoundError`。请勿在代码中引用它；公共能力一律走 `agents/__init__.py` 导出的类。

## 功能清单

### 1. SimpleAgent —— 对话 + 可选工具（`simple_agent.py`）

- 是什么：直接回答、可选做多轮 Function Calling 的最简 Agent。
- 解决什么：不需要"推理/反思"的常规问答，或在单 Agent 里做轻量工具调用。
- 关键 API：`SimpleAgent(name, llm, system_prompt=None, config=None, tool_registry=None, enable_tool_calling=True, max_tool_iterations=3)`。
  - `run(input_text, **kwargs) -> str`；`stream_run(input_text, **kwargs) -> Iterator[str]`（同步逐块产出文本）。
  - `async arun_stream(input_text, on_start/on_step/on_tool_call/on_finish/on_error, **kwargs) -> AsyncGenerator[StreamEvent, None]`：异步流事件化版本（真实逐字块：`LLM_CHUNK`）。
  - 工具管理：`register_tool(tool, auto_expand=True)`、`unregister_tool(name) -> bool`、`list_tools()`、`has_tools()`；`add_tool/remove_tool` 已弃用（发 `DeprecationWarning`）。
- 行为与边界：`enable_tool_calling` 实际为 `enable_tool_calling and tool_registry is not None`；无工具时走 `llm.invoke` 直答。有工具时 `max_tool_iterations` 轮内循环调用 `invoke_with_tools`，`tool_calls` 为空即返回文本；超过迭代上限会再补一次 `llm.invoke` 取最终回答。`summary` 角色历史消息会被转成 `[历史摘要]` system 文本注入。

### 2. ReActAgent —— 推理-行动循环（`react_agent.py`）

- 是什么：标准 ReAct 循环。内置两个认知工具 `Thought`（记录推理，参数 `reasoning`）与 `Finish`（返回最终答案，参数 `answer`），再叠加用户工具。
- 解决什么：需要"想一步、做一步"地调用外部工具才能收敛的任务。
- 关键 API：`ReActAgent(name, llm, tool_registry=None, system_prompt=None, config=None, max_steps=5)`；默认 `system_prompt` 为模块级 `DEFAULT_REACT_SYSTEM_PROMPT`；构造时若未给 registry 会自动 `ToolRegistry()`。
  - `run(input_text, **kwargs) -> str`：`max_steps` 步内循环；无 `tool_calls` 直接返回内容；`Finish` 置 `finished=True` 时提前终止；超步数返回"无法在限定步数内完成"并把状态记为 `timeout`。Ctrl+C / 异常时自动尝试 `save_session`。
  - `async arun(...)`：完整异步版，支持 `on_start/on_step/on_tool_call/on_finish/on_error` 生命周期钩子；用户工具经 `asyncio.gather` + `Semaphore(max_concurrent_tools)` 并行执行，内置工具串行。
  - `async arun_stream(...)`：每步单次 `ainvoke_with_tools`（避免双调用），产出 `AGENT_START/STEP_START/LLM_CHUNK/TOOL_CALL_FINISH/STEP_FINISH/AGENT_FINISH/ERROR` 事件，`Finish` 按 `tool_call_id` 匹配取最终答案。
  - 工具管理：`register_tool(tool)`；`add_tool` 弃用。
- 行为与边界：工具结果来自 `registry.async_execute_tool`（含熔断），错误/部分结果走 `truncator` 截断保护；`response.usage.total_tokens` 每步累计到 `self._total_tokens`。

### 3. ReflectionAgent —— 反思迭代（`reflection_agent.py`）

- 是什么：先执行、再反思、再优化，最多 `max_steps` 轮循环的"评审式" Agent。
- 解决什么：代码生成、文档写作等"初稿质量不足、需要自我修改"的任务。
- 关键 API：`ReflectionAgent(name, llm, system_prompt=None, config=None, max_steps=3, tool_registry=None, enable_tool_calling=True, max_tool_iterations=3)`；默认 system_prompt 内建"完成任务 → 反思 → 优化 → 反思时回复『无需改进』即停"的流程；内部 `Memory` 存 `execution/reflection` 轨迹。
  - `run(input_text, **kwargs) -> str`：初始执行入记忆，随后每轮 `_reflect_on_result` → 若反馈含"无需改进"/"no need for improvement"则提前停；否则 `_refine_result` 再入记忆；返回 `memory.get_last_execution()`。
  - `async arun_stream(...)`：把阶段变成事件（`initial_execution → reflection/refinement × max_steps → AGENT_FINISH`），反思内容以 `THINKING` 事件实时下发。
- 行为与边界：反思与优化也支持工具调用（受 `max_tool_iterations` 限制，超限用一次 `invoke` 兜底）；`run` 每次重置 `self.memory`，不跨调用累计。

### 4. PlanSolveAgent —— 分解规划-逐步执行（`plan_solve_agent.py`）

- 是什么：把复杂问题先拆成步骤再逐步求解的两段式 Agent，内含独立的 `Planner` 与 `Executor` 两个组件类。
- 解决什么：多步推理、数学题、复杂分析等"一次回答说不清"的任务。
- 关键 API：`PlanSolveAgent(name, llm, system_prompt=None, config=None, planner_prompt=None, executor_prompt=None, tool_registry=None, enable_tool_calling=True, max_tool_iterations=3)`。
  - `run(input_text, **kwargs) -> str`：`self.planner.plan()` 生成 `List[str]`；拿不到计划则返回"无法生成有效的行动计划，任务终止。"并记录 `failed` 状态；随后 `self.executor.execute(input_text, plan)` 逐步骤执行并携带"原始问题+完整计划+历史步骤与结果+当前步骤"上下文，返回最后一步的结果作为最终答案。
  - `async arun_stream(...)`：事件阶段 `planning → execution(step i/total) → final_answer`。
  - `Planner(llm_client, system_prompt=None)`：`.plan(question, **kwargs) -> List[str]`，通过 `generate_plan` 工具调用拿结构化步骤（`tool_choice` 锁定该工具），异常/无调用返回 `[]`。
  - `Executor(llm_client, system_prompt=None, tool_registry=None, enable_tool_calling=True, max_tool_iterations=3)`：`.execute(question, plan, **kwargs) -> str`。
- 行为与边界：`Executor` 的 `enable_tool_calling` 也要求 `tool_registry` 非空；模块保留 `_build_tool_schemas_from_registry` 等模块级函数与 `_NoOpAgent` 桥接类（经 `Agent._build_tool_schemas` / `Agent._execute_tool_call` 委派，消除历史 ~150 行重复）。

### 5. LoopAgent —— 闭环认知循环（`loop_agent.py`）

- 是什么：按 Plan → Act → Observe → Reflect → Check → Replan 六个环节建模的循环 Agent，另带 `LoopState` 等一组可序列化的状态数据结构。
- 解决什么：逻辑不确定、需要多轮工具交互且可能要显式再规划的任务。
- 关键 API：`LoopAgent(name, llm, system_prompt=None, config=None, tool_registry=None, enable_tool_calling=True, max_steps=5, enable_reflection=False, enable_replan=False, max_replans=2, max_consecutive_errors=3, stuck_threshold=2, reflection_interval=1)`。
  - `run(input_text, **kwargs) -> str`：未开反思/再规划时走"简单模式"（`_run_simple`，无工具即停、超 `max_steps` 后补一次 invoke）；开启后走 `_run_loop` 完整认知循环：`_plan`（invoke_with_tools）→ `_check_done` → `_observe`（工具结果沉淀为 `Evidence`）→ `_reflect`（按证据数估进度、检测连续错误 → `Reflection`）→ `_check_done` → `_replan`（重置 `Plan` 让模型重新规划）。
  - `async arun_stream(...)`：产出 `AGENT_START/STEP_START/TOOL_CALL_FINISH/STEP_FINISH/AGENT_FINISH`；每一步 `ainvoke_with_tools` + `_execute_tools_async` 并行执行。
  - 子代理入口：`run_as_subagent(task, max_steps=5, return_summary=True)`、`async arun_as_subagent(...)` → `{"summary", "metadata"}`。
  - 工具管理：`register_tool(tool, auto_expand=True)`、`unregister_tool`、`list_tools`、`has_tools`。
  - 数据结构：`LoopState(goal, plan, evidence, budget, status, reflection_history, last_decision, messages)`（`to_dict()` 序列化）；`Plan(steps/current_step/open_questions/success_criteria)`、`Evidence(tool_name/tool_call_id/status/truncated/summary/supports_goal/contradictions/next_info_gap)`、`Reflection(progress/issues/next_strategy/should_replan)`、`Budget(max_steps/max_replans/...)`、`TerminationDecision(signal/action/reason)`、`LoopStatus(RUNNING/STOPPED/REPLANNING/ERROR)`。
- 行为与边界：内置 `terminate` 工具（参数 `reason`）由模型显式终止；终止判定多信号（预算耗尽 / 无工具调用但有证据 / 连续错误超 `max_consecutive_errors` / 卡死 `stuck_threshold`）。`_replan` 当前实现为返回空 `Plan()`（模型重新规划），属有意简化的占位实现。

### 6. create_agent / default_subagent_factory —— 统一工厂（`factory.py`）

- 是什么：按字符串创建 Agent、以及被 `TaskTool`/子代理体系复用的默认子代理工厂。
- 关键 API：
  - `create_agent(agent_type: str, name: str, llm, tool_registry=None, config=None, system_prompt=None) -> Agent`。`agent_type` 支持 `"react" | "reflection" | "plan" | "simple" | "loop"`（大小写不敏感）；其他值抛 `ValueError`。
  - `default_subagent_factory(agent_type, llm, tool_registry=None, config=None) -> Agent`：命名 `subagent-{agent_type}`，按类型配内置 system_prompt（react/reflection/plan/simple/loop 各一套），并在子类带 `max_steps` 时用 `config.subagent_max_steps`（默认 15）覆盖。
- 行为与边界：工厂的 `simple` 分支不传 `tool_registry`（SimpleAgent 无工具参数则纯对话）；`SubAgentCapability` 通过 `agent_factory(agent_type)` 包装本函数实现"子 Agent 工具"能力。

## 使用说明

导入（两种路径等价，导入后是同一模块对象）：

```python
# 经典扁平名
from agentorchestra.agents import (
    SimpleAgent, ReActAgent, ReflectionAgent,
    PlanSolveAgent, PlanAndSolveAgent, LoopAgent,
    create_agent, default_subagent_factory,
)
# 规范路径
from agentorchestra.runtime.agents import SimpleAgent, ReActAgent, LoopAgent
```

离线 Fake LLM（不访问任何外部 API，仓库 `tests/unit/test_agents.py` 与 `examples/agent_full_demo.py` 即采用同思路替身）。下面的 `FakeLLM` 只实现框架实际调用到的方法：`.model`、`invoke`、`invoke_with_tools`、`stream_invoke`、`ainvoke`、`ainvoke_with_tools`、`astream_invoke`：

```python
import asyncio, json
from types import SimpleNamespace

def _resp(content, tool_calls=()):
    msg = SimpleNamespace(content=content, tool_calls=list(tool_calls))
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=5, total_tokens=10),
        content=content,                 # 兼容直接读 .content 的路径
        latency_ms=1,
    )

def _tool_call(name, args):
    return SimpleNamespace(id="call_1", type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))

class FakeLLM:
    def __init__(self, reply="模拟回复", plan_steps=None):
        self.model = "fake-model"; self.provider = "fake"
        self.reply = reply; self.plan_steps = plan_steps; self._planned = False

    def invoke(self, messages, **kwargs):
        return _resp(self.reply)

    def invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        if self.plan_steps and not self._planned:      # 只回一次 generate_plan
            self._planned = True
            return _resp(None, [_tool_call("generate_plan", {"steps": self.plan_steps})])
        return _resp(self.reply)

    def stream_invoke(self, messages, **kwargs):
        yield from self.reply

    async def ainvoke(self, messages, **kwargs):            return self.invoke(messages, **kwargs)
    async def ainvoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke_with_tools(messages, tools, **kwargs)
    async def astream_invoke(self, messages, **kwargs):
        for ch in self.reply: yield ch
```

场景示例：

```python
# 1) SimpleAgent：纯对话
llm = FakeLLM("你好，我是离线助手。")
agent = SimpleAgent(name="assistant", llm=llm, system_prompt="你是客服。")
print(agent.run("你好"))                      # 你好，我是离线助手。

# 2) ReActAgent：最大步数与超时兜底（fake 直接给文本答案，一步收敛）
react = ReActAgent(name="react", llm=FakeLLM("直接结论：通过"), max_steps=5)
print(react.run("检查配置是否合规"))

# 3) PlanSolveAgent：fake 首次返回 generate_plan 工具调用 → 自动按步执行
plan = PlanSolveAgent(name="planner", llm=FakeLLM("单步执行完成", plan_steps=["读配置", "生成方案"]))
print(plan.run("请给出部署方案"))             # 返回最后一步结果：单步执行完成

# 4) ReflectionAgent：fake 回复“无需改进”→ 一轮即停
ref = ReflectionAgent(name="reflector", llm=FakeLLM("无需改进"), max_steps=3)
print(ref.run("写一段欢迎词"))

# 5) LoopAgent：简单模式（无工具即停）
loop = LoopAgent(name="loop", llm=FakeLLM("回答完毕"), max_steps=5)
print(loop.run("1+1 等于几？"))
```

带工具的异步 + 流式示例（真实走 `ToolRegistry`，用内置 `CalculatorTool`）：

```python
import asyncio, json
from types import SimpleNamespace
from agentorchestra.runtime.agents import ReActAgent, LoopAgent, LoopState
from agentorchestra.capability.tools.registry import ToolRegistry
from agentorchestra.capability.tools.builtin.calculator import CalculatorTool

class ToolLLM(FakeLLM):
    """第一次让它调内置 python_calculator，看到 tool 结果后就给最终文本。"""
    def invoke_with_tools(self, messages, tools, tool_choice="auto", **kwargs):
        if any(m.get("role") == "tool" for m in messages):
            return _resp("答案是 2")
        return _resp(None, [_tool_call("python_calculator", {"input": "1+1"})])
    async def ainvoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke_with_tools(messages, tools, **kwargs)

registry = ToolRegistry()
registry.register_tool(CalculatorTool())      # 工具名 python_calculator，参数 input=表达式

react = ReActAgent(name="calc", llm=ToolLLM(), tool_registry=registry, max_steps=3)
print(react.run("请计算 1+1"))                # 第一轮执行工具，第二轮收敛 → 答案是 2

async def main():
    # 异步执行 + 生命周期钩子（内置工具经 registry.async_execute_tool 同样会执行）
    async def hook(event):
        print("hook:", event.type.value, event.data)
    r = await react.arun("请计算 1+1", on_start=hook, on_finish=hook)
    print("arun ->", r)

    # 流式事件（SSE 可再经 stream_to_sse 转换）
    async for ev in react.arun_stream("请计算 1+1"):
        print("event:", ev.type.value, ev.data)

asyncio.run(main())

# 6) 统一工厂
from agentorchestra.runtime.agents import create_agent
created = create_agent("plan", name="factory-plan", llm=FakeLLM("ok", plan_steps=["a", "b"]))
print(created.__class__.__name__, created.run("做个计划"))

# 7) LoopAgent 完整认知闭环状态对象（数据结构与默认开关）
from agentorchestra.runtime.agents.loop_agent import Budget, LoopStatus
state = LoopState(goal="核对订单", budget=Budget(max_steps=10, max_replans=2))
print(state.goal, state.status.value, state.to_dict()["budget"])  # 核对订单 running ...
```

相关 Config 字段与注意事项：

| 配置 | 说明 | 默认 |
| --- | --- | --- |
| `config.max_concurrent_tools`（`system.max_concurrent_tools`） | ReAct/Loop 异步路径工具并行上限 | `3` |
| `config.max_concurrent_subagents` | 子 Agent 并发信号量（`get_subagent_semaphore`） | `2` |
| `config.subagent_max_steps` | `default_subagent_factory` 覆盖子代理 `max_steps` | `15` |
| `config.hook_timeout_seconds` | 生命周期钩子超时（超时不阻断主流程） | `5.0` |
| `config.tool_output.*` | `Agent` 装配的截断器参数 | 见 context 文档 |
| `config.history.*` / `context_builder.enabled` | 历史注入与 GSSC 开关（opt-in） | 见 context 文档 |
| `config.trace.enabled` / `config.session.enabled` | 需要 trace_logger / session_store 时开启（opt-in） | `False` |

注意事项：
- 本模块所有示例使用 Fake LLM，不触网；替换为真实模型时 `SymphonyLLM` 需要 `model/api_key/base_url`（见 docs/core）。若使用真实 `SymphonyLLM`，构造 Agent 时的 `config.llm.*` 只影响新建 LLM 时的默认值，不改变已传入 LLM 实例的参数。
- `ReActAgent` 会打印大量过程日志（含 emoji），正式接入时注意 stdout 噪音；`LoopAgent` 日志相对收敛。
- 各 Agent 默认把问答写入 `Agent` 历史（`add_message`），连续长会话会自动触发历史压缩与 Token 累计。

## 与其他模块的关系

- 依赖（真实 import）：
  - `runtime.core.agent`：`Agent` 基类（抽象 `run`、事件 `_emit_event`、工具公共方法、会话、子代理、checkpoint/concurrency 都由基类提供）。
  - `runtime.core.agent.lifecycle`：`EventType`、`LifecycleHook`（异步钩子）。
  - `runtime.core.llm`：`SymphonyLLM`（统一 LLM 接口）；`runtime.core.llm.streaming`：`StreamEvent`/`StreamEventType`（流式事件）。
  - `runtime.core.message`：`Message`（历史消息单元）。
  - `runtime.core.config`：`Config`。
  - `runtime.core.utils`：`duration_seconds / parse_tool_arguments / serialize_tool_calls / truncate_text`。
  - `capability.tools.registry`：`ToolRegistry`（工具执行唯一入口；`react_executor.py` 直接 import，`loop/plan/simple/reflection` 经基类或局部导入）。
- 被依赖：
  - `runtime.capabilities.builtins.SubAgentCapability`（注册 `TaskTool`）import `runtime.agents.factory.default_subagent_factory`，实现"Agent 内部再跑子 Agent"。
  - 根包 `agentorchestra/__init__.py` 直接 re-export `SimpleAgent/ReActAgent/ReflectionAgent/PlanSolveAgent`，即 `from agentorchestra import SimpleAgent` 可用。
  - 兼容层 `_legacy.py` 把 `agentorchestra.agents.*` 映射到 `agentorchestra.runtime.agents.*`。
- 上下文双向关系：各范式消费基类装配好的 context 组件（历史/截断/Token），但 `runtime.agents` 本身不 import `runtime.context`（避免环）。
- 观测：`arun`/`run` 内的事件、`tool_result` 等由基类注入的 `trace_logger`（来自 `TraceCapability`）记录，范式代码不直接依赖 observability。

## 测试

```bash
python -m pytest tests/unit/test_agents.py -v    # 五类 Agent + Loop 状态数据结构的单测
python -m pytest tests/unit -v                   # 全部单元测试（含 core/context 支撑）
python -m pytest tests/stress/test_agent_stress.py -m stress   # Agent 压力用例（如已配置）
python examples/agent_full_demo.py               # 端到端演示（含 14 节 Agents 综合场景）
```

新增范式用例建议追加到 `tests/unit/test_agents.py`；工具相关回归测试可参考 `tests/unit/test_tools.py`。
