# 架构总览（Architecture）

> `agentorchestra` v0.2.0（Python ≥ 3.10，MIT）的领域化架构导览：源码按「运行时 / 能力 / 语义 / 编排 / 治理 / 可观测」六个领域物理分域；由统一装配门面 `Components`（`agentorchestra/components.py`）暴露横切组件；并以 `_legacy.py` 兼容层保留经典扁平导入名。本页是理解全项目的入口：先看图，再读分层与领域、设计动机、依赖方向，最后看装配/兼容与阅读指引。

---

## 1. 框架结构图

> 结构已定稿，与当前源码领域目录一一对应（2× 高清渲染，文件 `framework-structure.png`）。

![Symphony 框架结构图](framework-structure.png)

从图中可以看出三件事：

1. **自上而下分层**：应用层 → 装配门面 → 领域包；越靠近顶部越贴近业务，越靠近底部越是基础底座。
2. **领域内聚**：运行时（agents/context/capabilities/core）、能力（tools/skills/memory）贴近 Agent 执行；语义（ontology）、编排（orch/state）、治理（govern/tx/tenancy）、可观测各自独立成域。
3. **横切收敛**：装配只通过 `components.py` 一个入口，兼容只通过 `_legacy.py` 一处机制。

---

## 2. 领域布局与分层模型

包名 `agentorchestra` 以**领域**而非技术层次划分源码。用户代码在上、基础底座在下，横切能力（治理 / 可观测 / 装配）以独立包或门面存在，避免“扁平大杂烩”。

### 2.1 领域目录与一句话职责

| 领域 | 目录 | 职责 |
| --- | --- | --- |
| 运行时 | `runtime/` | 多智能体范式、核心基础件（LLM / 配置 / 消息）、上下文工程与 Capability 插槽，是 Agent 程序运行的主体 |
| 能力 | `capability/` | 供 Agent 调用的“能力资产”：工具系统（含内置工具与熔断）、Skills 加载、跨会话记忆 |
| 语义 | `ontology/` | 业务对象 / 链接 / 动作 / 函数 / 接口的统一语义建模，及其存储、查询、治理（安全/审计/分支）与“一键生成 Tool” |
| 编排 | `orchestration/` | 多 Agent 图/DAG 通信（Graph / Inbox / 事件）与运行状态持久化底座（Checkpoint / WAL / Interrupt） |
| 治理 | `governance/` | 跨模块治理：对象身份与权限、事务运行时、多租户与配额（另见 [enterprise](../enterprise/README.md)） |
| 可观测 | `observability/` | 执行轨迹记录、指标（Prometheus 文本）、可选 OTLP 追踪与 SLO 指标定义 |

各领域内部细分（物理路径 = 规范导入路径，随源码更新而同步）：

| 领域 | 组成 |
| --- | --- |
| `runtime/` | `agents/`（范式 + 工厂）、`context/`（历史 / Token / GSSC / 截断）、`capabilities/`（Capability 插槽）、`core/`（`agent/` `config/` `llm/` `message/` `reliability/` `telemetry/` + `exceptions` `utils`） |
| `capability/` | `tools/`（含 `builtin/`）、`skills/`、`memory/` |
| `ontology/` | `semantic/` `kinetic/` `storage/` `governance/` `process/` `query_engine/` `tool_generator/` `engine.py` |
| `orchestration/` | `orch/`（图/DAG）、`state/`（Checkpoint/WAL/Thread/Interrupt/Snapshot + `backends/`） |
| `governance/` | `govern/`（身份/ACL/权限/CAS）、`tx/`（事务运行时）、`tenancy/`（租户/配额/用量） |
| `observability/` | `TraceLogger`、`metrics/`、`prometheus/`、`otel_exporter/`、`slo/` |

### 2.2 顶层结构性成员

不属于任何领域、专门承担“横切职责”的三个文件：

| 文件 | 职责 |
| --- | --- |
| `agentorchestra/components.py` | 统一装配门面：懒加载、可插拔、测试可复位 |
| `agentorchestra/_legacy.py` | 经典扁平导入名 → 领域化规范路径的兼容映射 |
| `agentorchestra/__init__.py` | 顶层公共 API 再导出 + 安装兼容层 + 懒暴露顶层经典名 |

### 2.3 依赖方向总则

1. 上层可 `import` 下层；下层**不得**静态 `import` 上层。
2. 横切组件（governance / observability / state 等）彼此独立或只依赖更底层。
3. 业务包不直接持有“装配地图”——唯一“知道全部”的是 `components.py`。

---

## 3. 设计动机与原则

### 3.1 为什么是“领域化 + 组件化 + 兼容层”

- **领域化（分域物理布局）**：重构前顶层是 `agents / core / context / tools / skills / memory / state / tx / tenancy / ontology / …` 的扁平堆叠，难以判断一段代码“属于谁”。现在按业务领域落位，职责就近内聚、依赖方向可推导、新代码按目录归属即可自检。
- **组件化（横切组件可插拔）**：存储、追踪、指标、trace 导出这类横切组件若在各业务包维护全局单例，装配与测试都会失控。`components.py` 把它们收敛到**一个入口**：全部懒加载、未注册时回退既有全局实现、`register_*` 可覆盖、`reset()` 还原（测试用）。
- **兼容层（向后兼容）**：目录搬家不应让调用方跟着搬家。`_legacy.py` 用 `MetaPathFinder + AliasLoader` 把经典扁平导入名映射到新物理路径；经典名与新名拿到**同一个模块对象**（类身份一致、不重复执行模块代码）。

### 3.2 分层依赖规则（可在代码中对照验证）

1. **上层可 import 下层，下层不反向 import 上层**。例如 `ontology/`、`orchestration/`、`governance/` 都没有反向 `import runtime/agents`。
2. **基础件下沉**：`runtime/core/` 只提供 LLM 适配、`Config`、`Message`、异常、可靠性（retry / ratelimit）与 telemetry 基础件，是各领域的公共地基。
3. **横切组件独立、按需挂钩**：跨域能力通过**函数内懒导入**在功能点挂钩，避免模块顶层成环——例如 `runtime/core/agent/base.py` 在需要时懒导入 `orchestration.state` 的 Checkpoint/WAL/Interrupt，`runtime/core/llm/__init__.py` 懒接入 `governance.tenancy` 的租户计费。
4. **持久化底座只认抽象**：`governance/tx` 只依赖 `orchestration/state` 的 `CheckpointStore` / `records` / `wal` 接口，不关心后端是内存、SQLite 还是 PostgreSQL；后端由 `state/backends/` 提供，上层用 `get_default_store(db_url)` 选择。
5. **装配地图集中**：`components.py` 只做聚合与委托，业务包不直接持有全局组件实例。

### 3.3 向后兼容策略

- 顶层映射见 `_legacy.py::_LEGACY_TOP`：`agents → runtime.agents`、`core → runtime.core`、`tools → capability.tools`、`state → orchestration.state`、`tx → governance.tx` 等。
- `runtime/core` 内被按职责归并的经典模块走 `_LEGACY_CORE` 细粒度映射（如 `core.hot_config → core.config.hot`、`core.tracing → core.telemetry.tracing`、`core.llm_adapters → core.llm.adapters`）。
- `governance` / `orchestration` 是“域包装包”：公共符号（`ACLManager`、`Graph` 等）由 `govern/` / `orch/` 子包再导出；经典深层路径（如 `orchestration.graph`）经 `_GOVERN_FLAT` / `_ORCH_FLAT` 映射。
- 兼容层在 `agentorchestra/__init__.py` 导入早期自动安装（`install_legacy_aliases()`），并配合 `__getattr__` 懒暴露顶层经典名。

### 3.4 关键权衡与收敛点

- **“核心不得静态依赖上层”下的收敛点**：持久化循环、租户计费等确实需要“下钻”的能力，统一用**函数内懒导入**挂接，而不是把依赖写进模块顶层——既满足分层约束，又保留功能。
- **默认安全、按需开启（opt-in）**：`Config` 的 feature flag 全部默认 `False`（trace / skills / mcp / ontology / state_checkpoint / memory 等），避免隐式文件扫描、磁盘持久化、外部服务等副作用；`Config.development()` 只打开便于本地调试的几项，`Config.production()` 保持核心最小集。
- **语义与执行解耦**：`ontology` 只描述业务对象/动作，通过 `ToolGenerator` 生成标准 Tool 挂到任意 Agent 的 `ToolRegistry`，本体层不依赖具体 Agent 类型，业务语义可被不同范式复用。

---

## 4. 这样设计的好处

1. **依赖方向一目了然**：快速回答“这段逻辑属于哪个领域、它允许依赖谁”，避免扁平包互相乱引的蔓延。
2. **横切装配收敛、可测试**：存储 / 追踪 / 指标 / 导出统一走 `Components`；未装配可回退默认实现，测试可 `register_*` 注入替身再 `reset()`。
3. **持久化后端可替换**：`orchestration/state` 提供 `memory / sqlite / postgres` 后端，事务、审计、图执行都以同一 `CheckpointStore` 抽象为底座，从“开发态内存/单文件”平滑切到共享存储。
4. **默认安全、副作用最小**：opt-in 设计让大多数能力“不开不用”，本地运行零配置即可。
5. **平滑迁移不破坏调用方**：兼容层让经典导入名持续可用，仓库内部与外部使用方可按节奏切换到规范路径。
6. **语义与 Agent 解耦、可复用**：ontology 描述一次、生成 Tool 到处挂，业务语义不被单一范式绑定。

---

## 5. 模块全景与依赖方向

### 5.1 模块全景

| 领域 / 模块 | 位置 | 职责 | 主要能力 |
| --- | --- | --- | --- |
| 运行时·Agents | `runtime/agents/` | 多智能体范式 | `SimpleAgent` / `ReActAgent` / `ReflectionAgent` / `PlanSolveAgent` / `LoopAgent` + `factory` |
| 运行时·Core | `runtime/core/` | 公共基础件 | `llm/`(统一客户端·适配·guard) `config/`(Config+loader+hot) `message/`(session) `agent/`(base·lifecycle) `reliability/`(retry·ratelimit) `telemetry/`(tracing·metrics·logging·health·monitor) `exceptions` `utils` |
| 运行时·Context | `runtime/context/` | 上下文工程 | 历史管理、Token 计数、GSSC 构建、输出截断 |
| 运行时·Capabilities | `runtime/capabilities/` | Agent 特性插槽 | `Capability` / `CapabilityContext` / `CapabilityRegistry`（trace/skills/mcp/ontology/checkpoint/… 以特性注册） |
| 能力·Tools | `capability/tools/` | 工具系统 | `Tool` / `ToolRegistry`、`ToolResponse`/错误码、工具过滤、`CircuitBreaker`、内置工具（calculator/file/task/skill/mcp/todowrite/devlog） |
| 能力·Skills | `capability/skills/` | 知识外化 | Skill 加载与注册、元数据渐进披露 |
| 能力·Memory | `capability/memory/` | 跨会话记忆 | `MemoryManager`、分级记忆、混合检索（关键词 + 向量）、Summarizer |
| 语义 | `ontology/` | 业务语义模型 | `semantic/`(ObjectType/LinkType/Interface) `kinetic/`(ActionType/Function) `storage/`(ObjectStore/GraphStore/Index/后端) `governance/`(SecurityManager/审计/分支) `process/`(Workflow/Scheduler/Transaction) `query_engine/` `tool_generator/` `engine` |
| 编排·图 | `orchestration/orch/` | Agent 图 / DAG | `Graph` / 节点（Agent/Router/Merge/Functional）、`Inbox`、`DeliveryManager`、`GraphScheduler`、事件模型 |
| 编排·状态 | `orchestration/state/` | 持久化与恢复底座 | `Checkpoint`/`CheckpointStore`、WAL、Thread、Interrupt(HITL)、Snapshot、`records`(Audit/DLQ/Idempotency/Lock/Inbox)、`backends/`(memory·sqlite·postgres) |
| 治理·权限 | `governance/govern/` | 对象身份与访问控制 | `IdentityService`/`IdentityContext`、`ACLManager`(行级)、`PermissionChecker`、`ObjectCAS`；另有 `gdpr.py`（PII 数据主体工具，未在 `__init__` 导出） |
| 治理·事务 | `governance/tx/` | 事务运行时 | `TransactionCoordinator`、幂等、补偿、DLQ、乐观锁、WAL、`run_sync` 桥接、隔离（SSI 扩展点） |
| 治理·多租户 | `governance/tenancy/` | 租户隔离与配额 | `TenantManager`/`TenantContext`、配额 `QuotaManager`、用量 `UsageRecorder` |
| 可观测 | `observability/` | 运行诊断 | `TraceLogger`(JSONL+HTML)、Prometheus 文本收集器、`OTLPHttpJsonExporter`(默认关)、SLO；telemetry 基础来自 `runtime/core/telemetry/` |
| 装配门面 | `components.py` | 横切装配唯一入口 | `Components.state_store / tracer / metrics_collector / otel_exporter` + `register_*` / `enable_prometheus` / `enable_otel_trace` / `on_config_change` / `start_hot_reload` / `reset` |
| 兼容层 | `_legacy.py` | 经典名 → 新路径 | MetaPathFinder + AliasLoader，映射见模块 docstring |

### 5.2 关键依赖方向

| 谁 | 依赖谁 | 说明 / 约束 |
| --- | --- | --- |
| `runtime/agents` | `runtime/core`、`capability/tools` | Agent 建在 core 之上，经 `ToolRegistry` 调用工具；不依赖 ontology/governance 具体实现 |
| `ontology/` | `capability/tools`（`Tool` 等协议） | `tool_generator` 与 `engine.mount(registry)` 只负责生成并挂载工具；另有懒 import 接 telemetry 指标、governance 身份/事务、state 审计 |
| `orchestration/orch/` | —（独立） | 不静态依赖 `state` / `governance` / `capability`；store / Inbox 依赖经构造参数注入 |
| `orchestration/state/` | 自身 `backends/` + SQLAlchemy | 最干净的持久化底座；被 agent 持久化、tx、审计复用 |
| `governance/tx` | `orchestration/state`（records/wal/CheckpointStore 抽象） | 幂等 / 锁 / DLQ / 动作日志落到 state 的存储接口，后端由 `state.backends` 提供 |
| `governance/govern` | `ontology/governance`（可选） | 传入 RBAC `SecurityManager` 时复用其 `SecurityContext`；ACL / Identity 自带内存实现 |
| `observability/` | `runtime/core`（utils、telemetry.tracing 协议） | 顶层可观测包基于 core telemetry 基础件实现导出 |
| `runtime/core` 中 agent/llm | `orchestration/state`、`governance/tenancy`（**懒 import**） | 持久化与租户计费在功能点挂钩；是“核心不静态依赖上层”下刻意保留的收敛点 |
| `components.py` | 全部领域 | 唯一知道横切组件默认实现 / 可替换实现 / 装配组合的门面 |

---

## 6. 装配与扩展点

横切组件一律通过 `agentorchestra.components` 装配。所有 getter **懒加载**：未显式注册时回退各领域既有全局实现，因此“不装配也能跑”。

```python
from agentorchestra.components import Components

# —— 读取（默认回退全局实现，无需先装配）——
store     = Components.state_store()            # 回退 orchestration.state.get_default_store()
tracer    = Components.tracer()                 # 回退 runtime.core.telemetry.tracing.get_tracer
collector = Components.metrics_collector()      # 回退 observability.metrics.get_default_collector
exporter  = Components.otel_exporter()          # 回退 observability.otel_exporter.get_default_exporter

# —— 替换（可插拔）：注入工厂/实现，调用方无感 ——
Components.register_state_store(lambda: my_store)
Components.register_tracer(my_tracer_factory)
Components.register_metrics_collector(my_collector_factory)
Components.register_otel_exporter(my_exporter_factory)

# —— 装配常用组合（幂等）——
Components.enable_prometheus()                  # 开启 Prometheus 文本指标收集器
Components.enable_otel_trace(                   # 开启 OTLP trace 导出（端点须可达）
    endpoint="http://jaeger:4318",
    service_name="agentorchestra",
)

# —— 配置热更新（回调 + 轮询，可选）——
Components.on_config_change(lambda old_cfg, new_cfg: handle(old_cfg, new_cfg))
Components.start_hot_reload("config.json", poll_interval=2.0)   # 返回 ConfigWatch
Components.stop_hot_reload()

# —— 清理 / 还原（测试与装配变更）——
Components.reset()      # 清空注册并还原 observability 默认 NoOp 收集器
```

扩展点归纳：

- 持久化后端：`orchestration/state.get_default_store` 选择 `sqlite` / `postgres` / `in_memory`（详见 [state](../state/README.md)）。
- 指标收集器、trace 导出器、全局 Tracer、配置热更新回调。
- `Capability` 插槽：`runtime/capabilities/` 以特性方式注册 trace/skills/mcp/ontology/checkpoint 等能力。
- 逐项启用的“上线路线”：见 [enterprise](../enterprise/README.md)。

---

## 7. 兼容层说明（经典导入名）

`agentorchestra/_legacy.py` 维护“经典扁平导入名 → 领域化规范路径”映射；`agentorchestra/__init__.py` 导入早期调用 `install_legacy_aliases()` 安装兼容 Finder。命中时，经典名与规范名得到**同一个模块对象**。

| 经典导入（仍可用） | 规范物理路径 |
| --- | --- |
| `agentorchestra.agents` | `agentorchestra.runtime.agents` |
| `agentorchestra.context` | `agentorchestra.runtime.context` |
| `agentorchestra.core` | `agentorchestra.runtime.core` |
| `agentorchestra.tools` | `agentorchestra.capability.tools` |
| `agentorchestra.skills` | `agentorchestra.capability.skills` |
| `agentorchestra.memory` | `agentorchestra.capability.memory` |
| `agentorchestra.state` | `agentorchestra.orchestration.state` |
| `agentorchestra.tx` | `agentorchestra.governance.tx` |
| `agentorchestra.tenancy` | `agentorchestra.governance.tenancy` |
| `agentorchestra.orchestration.graph` | `agentorchestra.orchestration.orch.graph` |
| `agentorchestra.governance.acl` | `agentorchestra.governance.govern.acl` |
| `agentorchestra.core.tracing` | `agentorchestra.runtime.core.telemetry.tracing` |
| `agentorchestra.core.hot_config` | `agentorchestra.runtime.core.config.hot` |
| `agentorchestra.core.llm_adapters` | `agentorchestra.runtime.core.llm.adapters` |

```python
# 经典扁平导入（兼容层自动映射）
from agentorchestra.tools import ToolRegistry
from agentorchestra.state import get_default_store
from agentorchestra.tx import TransactionCoordinator
from agentorchestra.orchestration.graph import Graph

# 等价的新规范路径
from agentorchestra.capability.tools import ToolRegistry
from agentorchestra.orchestration.state import get_default_store
from agentorchestra.governance.tx import TransactionCoordinator
from agentorchestra.orchestration.orch.graph import Graph
```

机制要点：

- `_LegacyAliasFinder`（MetaPathFinder）优先于 `PathFinder` 拦截经典路径，经 `_guess()` 解析完整模块名（覆盖 `_LEGACY_TOP` 顶层映射、`_LEGACY_CORE` 细粒度映射、`_GOVERN_FLAT` / `_ORCH_FLAT` 域内扁平映射）。
- 命中后返回 `_AliasLoader`：`import` 规范模块并把 `sys.modules[别名]` 指向该对象，同时把别名挂到父包属性——因此 `import agentorchestra.governance.acl as a` 与 `import agentorchestra.governance.govern.acl as b` 得到 `a is b is True`（不重复执行模块代码）。
- 未命中映射或规范模块不存在的路径照常走原生导入；Finder 安装幂等。

---

## 8. 使用说明与阅读指引

### 8.1 建议阅读顺序

1. 根 [README.md](../../README.md)：快速开始、公共 API、顶层目录说明；
2. [docs/README.md](../README.md)：模块文档入口；
3. 本页（架构总览）后，按依赖自底向上阅读：
   - 运行时基础件：[runtime](../runtime/README.md)、[core](../core/README.md)、[context](../context/README.md)；
   - Agent 与能力：[agents](../agents/README.md)、[tools](../tools/README.md)、[memory](../memory/README.md)；
   - 业务语义：[ontology](../ontology/README.md)；
   - 编排与状态：[orchestration](../orchestration/README.md)、[state](../state/README.md)；
   - 治理与多租户：[governance](../governance/README.md)、[tx](../tx/README.md)、[tenancy](../tenancy/README.md)；
   - 可观测：[observability](../observability/README.md)；
4. 上线 / 演进视角：[enterprise（跨模块横切能力与上线指南）](../enterprise/README.md)。

### 8.2 运行测试与质量命令

```bash
pip install -e ".[dev]"        # 开发依赖：pytest / ruff / mypy 等
pytest tests                   # 仓库测试：tests/unit · tests/integration · tests/stress
ruff check agentorchestra      # 代码风格（select E/F/W/I，line-length=100，__init__.py 豁免 E402）
mypy agentorchestra            # 类型检查（可选；dev 依赖含 mypy）
```

### 8.3 模块文档索引

| 模块 | 文档 |
| --- | --- |
| 运行时总览 | [docs/runtime/README.md](../runtime/README.md) |
| Agent | [docs/agents/README.md](../agents/README.md) |
| 核心基础件 | [docs/core/README.md](../core/README.md) |
| 上下文工程 | [docs/context/README.md](../context/README.md) |
| 工具 | [docs/tools/README.md](../tools/README.md) |
| 记忆 | [docs/memory/README.md](../memory/README.md) |
| Ontology | [docs/ontology/README.md](../ontology/README.md) |
| 编排（图通信） | [docs/orchestration/README.md](../orchestration/README.md) |
| 持久化与恢复（state） | [docs/state/README.md](../state/README.md) |
| 事务运行时（tx） | [docs/tx/README.md](../tx/README.md) |
| 治理（权限/审计） | [docs/governance/README.md](../governance/README.md) |
| 多租户与配额 | [docs/tenancy/README.md](../tenancy/README.md) |
| 可观测性 | [docs/observability/README.md](../observability/README.md) |
| 横切能力与上线/演进指南 | [docs/enterprise/README.md](../enterprise/README.md) |
