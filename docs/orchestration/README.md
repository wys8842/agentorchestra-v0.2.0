# Orchestration（编排域）

> `orchestration/` 收纳 Agent 图/DAG 通信（`orch/`）与持久化/恢复（`state/`，见 [state](../state/README.md)）。本文聚焦 `orch`：Graph / Inbox / DeliveryManager / GraphScheduler / 各类 Node，是“多 Agent 对等协作”的消息驱动执行层。

## 设计动机与原则

- **消息驱动执行**：一次图运行 = 一系列消息在节点间流转。每条消息经 `Inbox` 落库（配置 store 时），7 天可回溯、带投递回执与尝试计数；节点 `run(message, ctx)` 统一签名，天然支持跨进程/崩溃恢复后从消息继续。
- **DAG + 有界回环，保证终止**：拓扑允许回边，但按“节点 iteration 计数”设 `max_iterations`（默认 3）上限，超限发 `NODE_SKIPPED` 事件并记 error，不会死循环。
- **条件路由与无条件转发分离**：`NodeOutput.route` 是节点声明的标签，`Edge.when` 是边要求的标签，相等才激活；`when=None` 无条件总是激活。下游收到的消息 `content = {"task": output.result, **output.data}`。
- **执行进度可恢复**：graph 运行中把各节点 iteration 计数持久化到 store 的专用 `save/load_iteration_snapshot`（O(1) 读写，见 [state](../state/README.md)），崩溃续跑后循环上限依然正确，而不是全表扫 WAL。
- **失败隔离、不中断整图**：节点异常被 scheduler 捕获转成 `NodeOutput.error` → `GraphResult.status = "partially_failed"` + `NODE_ERROR` 事件 + `on_node_error` 回调；单点失败不影响其余已入队消息的处理。
- **fan-in 汇聚**：同一目标有多个上游时，scheduler 内置 barrier 语义——等所有上游边都到达才放行（MergeNode 再把内容合并），避免多源消息被拆开分别执行。
- **声明式 + 可组合**：`Graph` 是构建器（add_node/add_edge/add_subgraph），子图内部节点自动加 `{子图名}.` 前缀防命名冲突；`clone(rename)` 支持把同一张图当模板实例化多个运行。
- **沿用既有 Agent 生态**：`AgentNode` 直接包装 `runtime.core.agent.Agent`（async `arun` 优先，否则线程池 `run`）；`TaskToolGraphAdapter` 把 TaskTool 风格的 `agent_factory(agent_type)` 绑定成 Graph 节点，迁移成本最低。
- **投递语义内置进 scheduler**：v0.1.1 起 `GraphScheduler` 自己完成 `mark_delivered → 执行 → ack → 路由下游`；独立 `DeliveryManager`（指数退避重试）作为可复用组件保留但不再被 scheduler 调用（见模块文档的 deprecated 说明）。

## 这样设计的好处

- 代码即图：声明式拼装、一眼可见条件分支与回环边界，配 `validate()`/`has_cycle()` 在建图期就拦截配置错误。
- 观察性内置：`GraphResult.events` 记录每次节点 `NODE_START / NODE_FINISH / NODE_ERROR / NODE_SKIPPED`，配合回调即可做 trace/告警/测试断言。
- store 可选：不传 store 自动落到 `InMemoryCheckpointStore`，单测与原型零配置；传 store 则获得消息落库 + 回执 + iteration 持久化的完整语义。
- 执行器与节点解耦：换节点实现不改调度器；`AgentNode`/`FunctionalNode`/`RouterNode`/`MergeNode`/子图覆盖大多数编排形态。
- 与事务、存储同一套基础设施，支持“图消息 + Checkpoint 恢复 + DLQ 兜底”组合使用（见 [state](../state/README.md)、[tx](../tx/README.md)）。

## 模块构成

| 路径（相对 `agentorchestra/orchestration/orch/`） | 职责 | 主要公开导出（真实，见 `orch/__init__.py`） |
|---|---|---|
| `graph.py` | 图/节点/边/结果模型 + 拓扑工具 | `Edge`、`NodeContext`、`NodeOutput`、`Node`、`Graph`、`GraphResult` |
| `nodes.py` | 各类节点实现 | `AgentNode`、`RouterNode`、`MergeNode`、`FunctionalNode`、`SubgraphNode` |
| `inbox.py` | 持久化消息队列（send/poll/ack） | `Inbox` |
| `scheduler.py` | 图执行器 | `GraphScheduler` |
| `delivery.py` | 投递重试管理器（已弃用，见模块 docstring） | 无（`__all__ = []`；类 `DeliveryManager` 可深度导入） |
| `events.py` | 图执行事件模型 | `NodeEventType`、`NodeEvent`、`DeliveryEvent` |
| `migration.py` | TaskTool → Graph 迁移助手 | `TaskToolGraphAdapter`、`GUIDE` |
| `fanin_barrier.py` | Fan-in barrier 独立工具 | `FanInBarrier`、`BarrierManager`、`BarrierTimeoutMode`（**未进公共 `__all__`**，走深度导入） |
| `orch/__init__.py` | `orch` 门面 | 见 `__all__`（Graph…TaskToolGraphAdapter 全套） |
| `orchestration/__init__.py` | 域门面：从 `orch` **再导出** | 同 `orch.__all__` |
| `orchestration/state/` | 持久化与恢复 | 见 [state](../state/README.md) |

经典深层模块名（`agentorchestra.orchestration.graph`、`.nodes`、`.inbox`、`.scheduler`、`.events`、`.delivery`、`.migration`）经 `_legacy.py` 别名到 `...orch.*`，与规范名指向同一模块对象。

## 功能清单

### Graph / Edge / GraphResult —— 声明式图

- `Graph(store=None, max_iterations=3, message_ttl_seconds=604800)`：
  - 构建：`add_node(name, node) -> Graph`（写入 `node.name`）；`add_edge(source, target, when=None)`（源/目标必须是已知节点或子图，否则 `ValueError`）；`add_subgraph(name, subgraph, entry, exits)`（把 `subgraph` 内部节点前缀化为 `{name}.`，`entry` 接收外部输入，`exits` 映射 `{子图内节点: 外部目标}`）。
  - 查询：`nodes()`（含子图名）、`get_node(name)`、`outgoing(name)`、`incoming(name)`、`entry_nodes()`（无入边节点）。
  - 校验：`validate() -> list[str]`（未知源/目标、自环）；`has_cycle() -> bool`（DFS）。
  - 模板化：`clone(rename=None)` 深拷贝结构（共享节点对象），供模板图多实例化。
  - 执行：`async run(initial_message, thread_id, entry_node=None, on_node_error=None, on_delivery_failed=None) -> GraphResult`（内部新建 `GraphScheduler` 并 `execute`）。
- `Edge(source, target, when=None)`：`when` 为条件标签。
- `GraphResult(status, graph_id, thread_id, node_results={}, events=[], iteration_count=0, messages=[], errors=[])`：`status ∈ "completed" | "failed" | "partially_failed"`；`to_dict()` 产出可传输摘要（略去 events/messages）。

### NodeContext / NodeOutput / Node —— 执行契约

- `NodeContext(graph_id, thread_id, message_id, from_node, store=None, inbox=None, iteration={}, emit=None)`；`node_iteration(name) -> int`。
- `NodeOutput(result=None, route=None, error=None, data={})`；工厂 `ok(result=None, route=None, **data)`、`fail(error)`。
- `Node`（ABC）：`async run(self, message: dict, ctx) -> NodeOutput`。子类：
  - `AgentNode(agent_factory, system_prompt=None, input_key="task")`：agent_factory 无参返回 Agent；输入取 `message[input_key]`（缺省 `"task"`，键不存在则整条 message）；`agent.arun` 优先，否则线程池 `agent.run`；`data` 记 `agent_type`。
  - `RouterNode(route_fn)`：`route_fn(message, ctx) -> str`，输出 `NodeOutput(result=label, route=label)`；无下游边匹配该标签则消息被丢弃。
  - `MergeNode()`：接收（多源汇聚后的）消息并合并为单个 dict 输出，供 fan-in 汇合点使用。
  - `FunctionalNode(fn)`：`fn(message, ctx) -> NodeOutput` 纯函数节点（无 Agent/IO）。
  - `SubgraphNode(name, entry, nodes, edges, exits)`：由 `Graph.add_subgraph` 创建，标记子图入口/出口。
- 行为边界：scheduler 执行节点时，`run()` 抛任意异常会被捕获并转成 `NodeOutput.fail(str(e))`；`output is None` 视为 `NodeOutput.ok()`；`output.error` 非空 → `partially_failed` 并 ack 该消息（不再路由下游）。

### Inbox —— 持久化消息队列

- `Inbox(store, default_ttl_seconds=604800)`：
  - `send(graph_id, thread_id, to_node, content, from_node=None, condition=None, ttl_seconds=None) -> msg_id`；`poll(thread_id, to_node=None, limit=100)`（只取 `queued` 且未过期）；`mark_delivered(msg_id) -> ack_token`；`ack(msg_id, ack_token=None, status="acked")`；`mark_failed(msg_id, error, attempts)`；`cleanup() -> int`。
- 基于 store 的 `inbox_messages` / `inbox_acks` 表（见 [state](../state/README.md)）；同 `msg_id` 入队覆盖。
- 生命周期：`queued → delivered → acked/rejected`，或 `failed/expired`；scheduler 在节点成功/失败后都 `ack`。

### GraphScheduler —— 执行器

- `GraphScheduler(store=None, max_iterations=3, message_ttl_seconds=604800)`：无 store → 自动 `InMemoryCheckpointStore`。
- `async execute(graph, initial_message, thread_id, entry_node=None, on_node_error=None, on_delivery_failed=None) -> GraphResult`：
  1. `graph.validate()` 有错直接 `ValueError`；
  2. 生成 `graph_id`，`Inbox` 就绪，加载该 `(graph_id, thread_id)` 的 iteration 快照；
  3. 入口：`entry_node` 显式指定 → 单入口；否则自动取所有无入边节点（多入口并行，各自收到一份初始消息）；
  4. 主循环 poll queued → `_process_one`（标记 delivered → 执行 → ack → 路由下游），安全上限 10000 轮；
  5. 结束时 `iteration_count = max(iter)` 并回写 iteration 快照。
- 节点处理细节：loopback（`from_node is not None`）且目标 iteration 已达 `max_iterations` → `NODE_SKIPPED` + error + ack；fan-in 目标需所有上游边都到达才放行；条件边按 `route == when` 过滤；`on_node_error` 支持同步/协程回调（协程则 `create_task`）。
- 事件：经 `ctx.emit`/内部 `emit` 追加 `NodeEvent` 到 `GraphResult.events`。

### events.py —— 事件模型

- `NodeEventType`：`NODE_START / NODE_FINISH / NODE_ERROR / NODE_SKIPPED`。
- `NodeEvent(event_type, node_name, graph_id, thread_id, message_id=None, data={}, timestamp, error=None)`：`to_dict()`。
- `DeliveryEvent(message_id, to_node, thread_id, attempt, status, error=None, timestamp)`：投递事件（`delivered/failed/retrying`）。

### delivery.py —— DeliveryManager（已弃用）

- 模块 docstring 明确：v0.1.1 起 scheduler 内置投递，本类**不再被调用**；`__all__ = []`。保留为可复用组件：`DeliveryManager(inbox, max_attempts=5, base_backoff=0.1, backoff_factor=2.0)`，`on_event(cb)`，`async deliver(thread_id, msg, consumer, on_delivery_failed=None) -> bool`（指数退避，耗尽 `mark_failed`）。新代码请用 scheduler 内建语义或自研持久化重试 + DLQ。

### migration.py —— TaskTool → Graph

- `TaskToolGraphAdapter(agent_factory, agent_type="react")`：`make_node(input_key="task") -> AgentNode`，把 `agent_factory(agent_type)` 绑定为固定类型的节点工厂。
- `GUIDE`：内置文本迁移指南（何时继续用 TaskTool、何时换 Graph、父子 tool → 图示例）。

### fanin_barrier.py —— Barrier 工具（深度导入）

- `BarrierTimeoutMode`（`DROP/FORCE/RAISE`）；`FanInBarrier(target, expected_sources, ...)`：`add_source(source) -> bool`（到齐即 `activate`）、`get_missing_sources()`、`is_timed_out()`、`get_progress()`；`BarrierManager` 管理多 target barrier。
- 与 scheduler 内建 fan-in 的关系：scheduler 内部用 `fanin_pending` 集合实现“等所有上游到达”，本文件是独立、可配置超时行为的通用实现，供自定义执行器使用。

## 使用说明

导入路径：规范门面 `from agentorchestra.orchestration import ...`（`orchestration/__init__` 从 `orch` 再导出）；经典深层名 `agentorchestra.orchestration.graph` 等亦可直接使用。

### 场景一：功能节点 + 条件路由 + 有界回环

```python
import asyncio
from agentorchestra.orchestration import Graph, FunctionalNode, NodeOutput

# produce: 累加计数并打出 "go" 标签（无条件下游或 when="go" 的边都会激活）
def produce(msg, ctx):
    if isinstance(msg.get("task"), dict) and "v" in msg["task"]:
        v = msg["task"]["v"]
    else:
        v = int(msg.get("v", 0) or 0)
    return NodeOutput.ok(result={"v": v + 1}, route="go")

# check: v>=3 → "done"，否则 "again"（回环回写 produce）
def check(msg, ctx):
    v = msg["task"]["v"] if isinstance(msg.get("task"), dict) else 0
    return NodeOutput.ok(result={"v": v}, route="done" if v >= 3 else "again")

def finish(msg, ctx):
    return NodeOutput.ok(result="complete")

async def main():
    g = Graph(max_iterations=5)
    g.add_node("produce", FunctionalNode(produce))
    g.add_node("check", FunctionalNode(check))
    g.add_node("finish", FunctionalNode(finish))
    g.add_edge("produce", "check", when="go")
    g.add_edge("check", "produce", when="again")   # 有界回环
    g.add_edge("check", "finish", when="done")

    # 回环图所有节点都有入边 → 无“无入边”入口，需显式指定 entry_node
    result = await g.run({"v": 0}, thread_id="demo-1", entry_node="produce")

    print("status:", result.status)                 # completed
    print("finish result:", result.node_results["finish"])  # complete
    print("events:", [e.event_type.value for e in result.events])
    assert result.node_results["finish"] == "complete"

asyncio.run(main())
```

### 场景二：RouterNode 决策分支（无 Agent）

```python
import asyncio
from agentorchestra.orchestration import Graph, RouterNode, FunctionalNode, NodeOutput

def classify(msg, ctx):
    risk = msg.get("task", {}).get("risk") if isinstance(msg.get("task"), dict) else 0
    return "approve" if risk < 50 else "reject"

async def main():
    g = Graph()
    g.add_node("gate", RouterNode(classify))
    g.add_node("on_approve", FunctionalNode(lambda m, c: NodeOutput.ok("approved")))
    g.add_node("on_reject", FunctionalNode(lambda m, c: NodeOutput.ok("rejected")))
    g.add_edge("gate", "on_approve", when="approve")
    g.add_edge("gate", "on_reject", when="reject")

    result = await g.run({"task": {"risk": 10}}, thread_id="t-risk", entry_node="gate")
    assert result.node_results["on_approve"] == "approved"
    assert "on_reject" not in result.node_results

asyncio.run(main())
```

### 场景三：Inbox 直用（消息/回执，等价测试写法）

```python
import asyncio
from agentorchestra.orchestration import Inbox
from agentorchestra.state.backends.memory_backend import InMemoryCheckpointStore

async def main():
    store = InMemoryCheckpointStore()
    await store.init()
    inbox = Inbox(store, default_ttl_seconds=3600)

    msg_id = await inbox.send("g-1", "t-1", "coder", {"task": "写接口"},
                              from_node=None, condition=None)
    msgs = await inbox.poll("t-1")                      # queued 消息
    assert len(msgs) == 1 and msgs[0].msg_id == msg_id

    token = await inbox.mark_delivered(msg_id)          # delivered + ack_token
    await inbox.ack(msg_id, token, "acked")

asyncio.run(main())
```

### 场景四：带 store 的 scheduler + 节点错误观察

```python
import asyncio
from agentorchestra.orchestration import GraphScheduler, Graph, FunctionalNode, NodeOutput
from agentorchestra.state.backends.memory_backend import InMemoryCheckpointStore
from agentorchestra.orchestration import NodeEvent, NodeEventType

def boom(msg, ctx):
    raise RuntimeError("节点崩了")

def after(msg, ctx):
    return NodeOutput.ok("done")

async def main():
    store = InMemoryCheckpointStore()
    await store.init()
    g = Graph(store=store, max_iterations=2)
    g.add_node("a", FunctionalNode(boom))
    g.add_node("b", FunctionalNode(after))
    g.add_edge("a", "b")

    seen: list[str] = []
    scheduler = GraphScheduler(store=store)

    async def on_error(ev: NodeEvent):
        seen.append(f"{ev.node_name}:{ev.error}")

    result = await scheduler.execute(g, {"v": 1}, thread_id="t-err",
                                     entry_node="a", on_node_error=on_error)
    assert result.status == "partially_failed"          # a 失败 → 不再路由 b
    assert "b" not in result.node_results
    assert any(e.event_type == NodeEventType.NODE_ERROR for e in result.events)

asyncio.run(main())
```

注意事项：

- **回环/子图场景没有“无入边”的自动入口**：只要任一节点存在入边且所有节点都有入边，就必须显式传 `entry_node`，否则 `_find_entries()` 抛 `ValueError: 图中无入口节点`。
- 节点失败 = 该条消息终止 + `partially_failed`，不是整图异常；需要“失败后重试/兜底”请在节点内部实现或配合 [tx](../tx/README.md) DLQ 做持久化补偿。
- `AgentNode` 需要 `agent_factory` 返回真实 Agent（本仓库 `runtime/agents/*`），纯逻辑请用 `FunctionalNode`/`RouterNode`，示例零 LLM 依赖。
- `max_iterations` 是“每个节点累计执行次数”上限而非消息总数；`message_ttl_seconds` 默认 7 天，过期消息会被 poll 过滤。
- 想得到真实“落库 + 崩溃续跑”语义必须传持久 store（SQLite/Postgres），并先 `await store.init()`。

## 与其他模块的关系

真实依赖（源码 import 方向）：

- 向 [state](../state/README.md) 借力：`scheduler.py` → `orchestration/state/backends/memory_backend.InMemoryCheckpointStore`（缺省 store）并读写 `iteration_snapshot`；`inbox.py` → `orchestration/state/records.InboxMessage`；消息表/回执表由 store 提供。
- 向 runtime 借力：`nodes.py` / `migration.py` → `runtime/core/agent.Agent`（`AgentNode` 执行、`TaskToolGraphAdapter` 适配）。
- 被上层消费：Agent/编排入口可直接 `await graph.run(...)`；[state](../state/README.md) 的 `ThreadManager` 负责 thread 生命周期；`agentorchestra/components.py` 的 `Components.state_store()` 可作为 `Graph(store=...)` 的统一 store 来源，让图消息与事务（[tx](../tx/README.md)）同库。
- 包边界提醒：`orchestration` 是域包装包；`orch` 才是物理子包，`state` 是兄弟子包。经典深层名（`agentorchestra.orchestration.scheduler` 等）由 `_legacy.py` 别名映射，与规范名同对象。

## 测试

相关用例见 `tests/unit/test_orchestration.py`（GraphScheduler 构造、Inbox send/poll/ack）、`tests/unit/test_state.py`（store 原语）、`tests/integration/test_integration.py`（store + scheduler 组合）。

```bash
python -m pytest tests/unit/test_orchestration.py -v
python -m pytest tests/unit/test_state.py -v
python -m pytest tests/integration/test_integration.py -v
python -m pytest tests/unit -v
```

`pytest.ini` 已开启 `asyncio_mode = auto`。
