# runtime：运行时域总览（agents / context / core / capabilities）

> 本模块（`agentorchestra.runtime`）是框架的"运行时域"：收纳 Agent 范式（`agents`）、上下文工程（`context`）、核心运行时（`core`）与 Agent 可插拔能力机制（`capabilities`）；`agentorchestra.agents / .context / .core` 经典导入路径通过兼容层映射到这里。

## 设计动机与原则

1. **按领域物理布局取代扁平模块**。源码在 `runtime/` 下按能力划分 `agents / context / core / capabilities` 四个子包，根包不再堆叠上百个平铺模块；命名即目录，模块职责从路径上一眼可读。
2. **一层目录只装一层东西，避免"半领域包"**。`runtime` 只装"运行时"本身（跑一个 Agent 必须的东西）：范式、上下文、基座；而记忆/技能/工具实现、图编排、状态持久化、治理/事务等被明确放在其它域（`capability/`、`orchestration/`、`governance/` 等），防止运行时包无限膨胀。
3. **兼容层保证演进不动公共 API**。`agentorchestra/_legacy.py` 在根包导入时自动安装 MetaPathFinder：经典扁平路径（`agentorchestra.core.llm_response`、`agentorchestra.agents.react_agent`、`agentorchestra.context.history`…）逐一映射到新物理位置；导入走经典名与走规范名拿到的是**同一个模块对象**，类身份一致、不会重复执行模块代码。
4. **依赖方向单向收敛**。`runtime` 内部：`agents` 依赖 `core` 与（经基类）`context`；`context` 只依赖 `core.message/utils` 这种叶子；`core` 对 `context / capabilities / orchestration.state` 全部用方法体内延迟导入。这样子包可独立导入、独立测试。
5. **能力即插即用，基类不膨胀**。早期把 trace/skills/mcp/ontology/session/memory/subagent/checkpoint 等全部塞进 Agent 的做法已收敛为 `Capability` 机制：每项 feature 一个 Capability，`is_enabled(config)` 决定是否安装，`install/uninstall` 挂钩子，`CapabilityContext` 注入共享资源，业务代码下沉到 capability 内部而不是 Agent 类里。
6. **opt-in by default 贯穿运行时**。运行时的"外壳"（trace 文件、会话落盘、记忆建库、MCP 连接、ontology 引擎、GSSC 构建器、子代理）默认全部关闭，只有 core 的 LLM/配置/消息等核心能力默认开启——保证一个 `Agent(name, llm)` 在无任何配置副作用的情况下即可工作。
7. **状态与元数据统一回填**。Capability 安装结果写入共享 `state` dict，再由 `Agent.__init__` 回填成 `agent.trace_logger / agent.session_store / agent.memory_manager / agent.checkpoint_store` 等属性，旧代码直接访问的属性路径保持不变。
8. **文档与代码目录一一对应**。`docs/runtime`、`docs/agents`、`docs/context`、`docs/core` 分别对应 `runtime/` 下的域与其子包，查找文档的路径就是查找代码的路径。

## 这样设计的好处

- 经典导入路径永续：升级 v0.2 布局后旧 `from agentorchestra.core.llm import SymphonyLLM` 照常工作。
- 拆包后每个子包职责清晰，改动一个域不牵连其它域的导入图。
- Agent 能力按需装载：不开的 feature 不初始化、不产生文件/网络/数据库副作用。
- 运行时四个子包可以独立阅读、独立演进，配合本目录下四篇 README 即可从零上手。
- 新增能力 = 新增一个 `Capability` + 注册，无需改 `Agent` 类。

## 模块构成

物理路径 | 职责 | 主要公开导出
--- | --- | ---
`agentorchestra/runtime/__init__.py` | 运行时域说明（无代码导出） | （子包见下）
`agentorchestra/runtime/agents/` | Agent 范式 + 工厂 | `SimpleAgent`、`ReActAgent`、`ReflectionAgent`、`PlanSolveAgent`、`PlanAndSolveAgent`、`LoopAgent`、`create_agent`、`default_subagent_factory`（详见 `docs/agents`）
`agentorchestra/runtime/context/` | 上下文工程 | `ContextBuilder`、`ContextConfig`、`ContextPacket`、`HistoryManager`、`ObservationTruncator`、`TokenCounter`（详见 `docs/context`）
`agentorchestra/runtime/core/` | 核心运行时 | `Agent`、`SymphonyLLM`、`Message`、`Config`、`ConfigLoader`、`ConfigWatch`、`HealthCheck`、`MonitorServer`、`SymphonyException`、`LLMResponse`、`StreamStats`、`setup_logging`、`get_logger`、`MetricsCollector`、`get_metrics`、`Tracer`、`Span`、`MemoryExporter`、`JsonlExporter`、`get_tracer`、`RetryManager`、`retry_with_backoff`、`TokenBucket`、`SlidingWindow`、`RateLimiter`（详见 `docs/core`）
`agentorchestra/runtime/capabilities/__init__.py` | 可插拔能力机制聚合导出 | `Capability`、`CapabilityContext`、`CapabilityRegistry`
`agentorchestra/runtime/capabilities/base.py` | Capability 基类与共享上下文 | `Capability`（`name/is_enabled/install/uninstall`）、`CapabilityContext`（`config/llm/tool_registry/logger_name/name/state`）
`agentorchestra/runtime/capabilities/registry.py` | 能力注册表与默认集 | `CapabilityRegistry`（`register/unregister/get/list_names/install_all/uninstall_all`）、模块级 `default_capabilities()`
`agentorchestra/runtime/capabilities/builtins.py` | 13 个内置能力实现 | `TraceCapability`、`SkillsCapability`、`MCPCapability`、`OntologyCapability`、`SessionCapability`、`MemoryCapability`、`SubAgentCapability`、`TodoWriteCapability`、`DevLogCapability`、`StateCheckpointCapability`、`SnapshotCapability`、`SmartCompressionCapability`、`ContextBuilderCapability`
`agentorchestra/_legacy.py` | 经典扁平导入 → 领域化路径的兼容映射 | `install_legacy_aliases()`（`agentorchestra/__init__.py` 导入时自动调用）

> 注意区分：`agentorchestra/runtime/capabilities/`（Agent 可插拔能力机制）与顶层 `agentorchestra/capability/`（工具/技能/记忆的领域实现，经典 `agentorchestra.tools / .skills / .memory` 别名指向后者）是两个不同概念。

## 功能清单

### 1. 运行时导入兼容（`_legacy.py`）

- 是什么：注册在 `sys.meta_path` 首位的自定义 finder，把"经典扁平路径"解析到"新物理路径"。
- 解决什么：v0.2 领域化重构（`core.llm_response` → `runtime.core.llm.response` 等）不破坏既有 `import`。
- 关键 API 与行为：
  - 顶层组件映射（`_LEGACY_TOP`）：`agents/context/core` → `runtime.agents/context/core`（另含 `tools/skills/memory/state/tx/tenancy` 映射到各自域）。
  - `core` 深层映射（`_LEGACY_CORE`）：`lifecycle/config_loader/hot_config/llm_adapters/llm_response/llm_schema/streaming/prompt_guard/session_store/retry/ratelimit/logging/metrics/monitor/health/tracing/trace_context` 逐一指向新物理模块。
  - `install_legacy_aliases()` 幂等；`_AliasLoader` 直接把 `sys.modules[别名]` 指向已加载的规范模块，并尽量回填父包属性——因此别名与规范名是同一对象。
- 边界：`agentorchestra.core` 作为属性访问（`import agentorchestra; agentorchestra.core`）由根包 `__getattr__` 懒加载到 `runtime.core`，与显式 import 等价。

### 2. 运行时四子包（详见各自文档）

- `runtime.core`：底座。统一 LLM、配置、消息、Agent 基类、可靠性、观测。`Agent` 构造时完成能力编排并把组件回填到 `self`。
- `runtime.context`：历史 / Token / 截断 / GSSC。被 `Agent` 基类装配为 `history_manager/truncator/token_counter`，是"长对话不崩"的工程基础。
- `runtime.agents`：五种范式与工厂。真正执行任务时在 `Agent` 提供的装配物上运行各自的推理循环。
- `runtime.capabilities`：能力装载机制（下节详述）。

### 3. Capability 机制：base / registry / builtins（`capabilities/`）

- 是什么：Agent 的"能力"单元抽象 + 注册表 + 默认能力集，把 v0.1 塞在 Agent 里的横切 feature 全部外置。
- 解决什么：Agent 类不再成为"上帝对象"；每项能力独立启用、独立测试、可被用户自定义替换。
- 关键 API：
  - `Capability`（`runtime/capabilities/base.py`）：`name: str`；`is_enabled(ctx) -> bool`（基于 config 判断）；`install(ctx) -> None`（建依赖、注册工具、写 `ctx.state`）；`uninstall(ctx)`（默认 no-op）。
  - `CapabilityContext`：dataclass，字段 `config`（已分组的 `Config`）、`llm`、`tool_registry`（可能为 `None`）、`logger_name`、`name`（Agent 名）、`state`（任意共享状态 dict，跨能力传递句柄）。
  - `CapabilityRegistry`（`runtime/capabilities/registry.py`）：`register(capability)`（按 `name` 覆盖）、`unregister(name)`、`get(name)`、`list_names()`、`install_all(ctx)`（按注册顺序安装**已启用**的能力，单个失败只告警不阻断）、`uninstall_all(ctx)`（逆序）。
  - `default_capabilities()`（模块级函数）：返回含 13 个内置能力的注册表。
- 内置能力清单与启用条件（config 门控，全部默认关闭）：

| 能力 | `name` | 安装条件（`is_enabled`） | 安装产物（写入 `ctx.state`，再回填 Agent 属性） |
| --- | --- | --- | --- |
| TraceCapability | `trace` | `config.trace.enabled` | `trace_logger`（观测 TraceLogger） |
| SkillsCapability | `skills` | `config.skills.enabled` | `skill_loader`；`auto_register` 时向 registry 注册 `SkillTool` |
| MCPCapability | `mcp` | `config.mcp.enabled` 且有 `tool_registry` | 连接 `config.mcp.config_file` 中 server 并把工具注册进 registry |
| OntologyCapability | `ontology` | `config.ontology.engine_enabled` 且有 `tool_registry` | `ontology_engine`；`engine.mount(tool_registry)` 暴露本体工具 |
| SessionCapability | `session` | `config.session.enabled` | `session_store`（`core.message.session.SessionStore`） |
| MemoryCapability | `memory` | `config.memory.enabled` | `memory_manager`；`auto_register_tools` 时注册 `MemorySaveTool/MemoryRecallTool` |
| SubAgentCapability | `subagent` | `config.subagent.enabled` 且有 `tool_registry` | 注册 `TaskTool`（agent_factory 包装 `default_subagent_factory`，可用 light LLM） |
| TodoWriteCapability | `todowrite` | `config.todowrite.enabled` 且有 `tool_registry` | 注册 `TodoWriteTool` |
| DevLogCapability | `devlog` | `config.devlog.enabled` 且有 `tool_registry` | 注册 `DevLogTool`（含 session_id/agent_name） |
| StateCheckpointCapability | `state_checkpoint` | `config.state_checkpoint.enabled` | `checkpoint_store` + `thread_manager`；按 `persistence_mode`（sqlite/postgres/in_memory）建 store；`wal_snapshot_enabled` 时建 `snapshot_worker` |
| SnapshotCapability | `snapshot` | 依赖 state_checkpoint 开启且 `checkpoint_store` 就绪 | 实际由 StateCheckpointCapability 创建 worker，此处仅声明依赖 |
| SmartCompressionCapability | `smart_compression` | `config.smart_compression.enabled` | `state["enable_smart_compression"] = True`（驱动 `Agent` 走 LLM 智能摘要） |
| ContextBuilderCapability | `context_builder` | `config.context_builder.enabled` | `context_builder`（GSSC `ContextBuilder`） |

- 行为与边界：能力名重复注册即覆盖；`install_all` 逐个 `try/except`，单能力失败仅 `logger.warning`；Agent 构造后通过 `self._capability_state` 与回填属性访问能力产物。

## 使用说明

导入（经典路径优先示例；两条路径等价）：

```python
# 经典扁平路径（兼容层自动映射，同一模块对象）
from agentorchestra.agents import SimpleAgent, create_agent
from agentorchestra.core import Config, SymphonyLLM
from agentorchestra.context import HistoryManager

# 规范路径
from agentorchestra.runtime.agents import ReActAgent
from agentorchestra.runtime.core import Config, get_tracer, TokenBucket
from agentorchestra.runtime.context import TokenCounter

# 能力机制（规范路径导入）
from agentorchestra.runtime.capabilities import Capability, CapabilityContext, CapabilityRegistry
from agentorchestra.runtime.capabilities.registry import default_capabilities

# 经典深层模块别名同样可用
from agentorchestra.core.hot_config import ConfigWatch
from agentorchestra.core.llm_response import LLMResponse
```

场景示例（不触网，LLM 用替身）：

```python
# 1) 从经典路径导入并确认与规范路径是同一模块对象
import agentorchestra.agents as ag, agentorchestra.runtime.agents as rag
print(ag is rag)                                   # True

# 2) 能力机制：默认集 + 自定义 Capability
class PingCapability(Capability):
    name = "ping"
    def is_enabled(self, ctx): return True
    def install(self, ctx): ctx.state["ping"] = "pong"

reg = CapabilityRegistry().register(PingCapability())
print(reg.list_names())                            # ['ping']
reg.unregister("ping")

# 3) Agent 构造时的能力编排（默认能力全部 opt-in，未开 config 不生效）
from agentorchestra.runtime.agents import SimpleAgent

class FakeLLM:                                      # 仅实现框架用到的成员
    model = "fake-model"
    provider = "fake"
    def invoke(self, messages, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(content="ok", usage=SimpleNamespace(total_tokens=1),
                               latency_ms=0, choices=[])
    def stream_invoke(self, messages, **kwargs):
        yield "ok"

agent = SimpleAgent(name="a", llm=FakeLLM(), config=Config.development())
print(agent.name, agent.history_manager is not None)  # a True
print(agent.trace_logger is not None)              # True（development() 开了 trace）
print(agent.memory_manager, agent.checkpoint_store)   # None None（未开启）
print(agent.run("hi"))                             # ok（纯离线）
```

注意事项表：

| 事项 | 说明 |
| --- | --- |
| 经典路径与规范路径 | 相同模块、相同类；文档示例以经典名为主，新代码推荐规范路径 |
| `runtime/capabilities` vs `capability/` | 前者是"Agent 能力机制"，后者是工具/技能/记忆等领域实现（`agentorchestra.tools/.skills/.memory`） |
| 默认开关 | 13 个内置能力全部 opt-in；不配置任何 `.enabled=True` 时 Agent 只有核心能力 |
| 能力产物 | 通过 `agent.<attr>`（`trace_logger` 等）或 `agent._capability_state[key]` 访问，优先用前者 |
| 组合依赖 | `Snapshot` 依赖 `StateCheckpointCapability` 先注入 store；`DevLog` 会复用 `trace_logger.session_id` |

## 与其他模块的关系

- `runtime` 内部依赖：`agents → core（Agent 基类/LLM/Message/Config）`；`core.agent.base` 运行期用 `context` 与 `capabilities`（延迟导入）；`context → core.message/utils`。
- 与 `runtime` 之外：`capabilities.builtins` 装配时引用 `capability.tools.builtin.*`（SkillTool/MCP/Calculator 等）、`capability.memory`、`capability.skills`、`ontology.engine`、`orchestration.state`（checkpoint/snapshot/thread）、`runtime.agents.factory`（SubAgentCapability）；`components.Components` 聚合了 `core.telemetry.tracing` 与 `core.config.hot`。
- 兼容层：`_legacy.py` 把 `agents/context/core` 指向本域；`agentorchestra/__init__.py` 懒暴露 `agentorchestra.agents/.context/.core` 属性并 re-export 顶层便捷符号（`SymphonyLLM/Config/Message/SymphonyException/SimpleAgent/ReActAgent/ReflectionAgent/PlanSolveAgent`）。

## 测试

```bash
python -m pytest tests/unit -v                          # 全部单元测试（agents/core 用例）
python -m pytest tests/unit/test_agents.py -v           # Agent 范式与装配
python -m pytest tests/unit/test_core.py -v             # Config/Message/异常
python -m pytest tests/stress/test_agent_stress.py -m stress   # Agent 压力用例
python examples/agent_full_demo.py                      # 端到端全能力演示
```

capabilities 机制尚无独立测试文件，建议新增 `tests/unit/test_capabilities.py` 覆盖 `register/install_all` 容错与各内置能力的 `is_enabled` 门控。
