# 架构与依赖方向

Symphony 是**分层组件式**框架：Agent/工具层在上，数据/存储层在下，横切能力（可观测性、治理、多租户）以独立组件存在。依赖只能自上而下，禁止反向/环状。

## 分层总览

物理上按**领域目录**组织（agentorchestra/ 下）；经典扁平组件名保留为公共 API
（经 `agentorchestra/_legacy.py` 兼容层映射，见文末说明）：

```
┌───────────────────────────────────────────────────────────┐
│  应用层（examples / 业务装配）                              │
│    components.py（装配门面）——唯一推荐的横切装配入口          │
├───────────────────────────────────────────────────────────┤
│  runtime/          运行时域                                 │
│    agents · core · context                                 │
│      （经典名 agentorchestra.agents / .core / .context）     │
├───────────────────────────────────────────────────────────┤
│  capability/       能力域                                   │
│    tools · skills · memory                                 │
│      （经典名 agentorchestra.tools / .skills / .memory）     │
├───────────────────────────────────────────────────────────┤
│  ontology/         语义/业务层（语义/动能/存储/流程/治理）     │
├───────────────────────────────────────────────────────────┤
│  orchestration/    编排域                                   │
│    orch （经典 agentorchestra.orchestration 图/DAG 通信）    │
│    state （经典 agentorchestra.state 持久化）                │
├───────────────────────────────────────────────────────────┤
│  governance/       治理域                                   │
│    govern （经典 agentorchestra.governance 身份/权限）        │
│    tx      （经典 agentorchestra.tx 事务运行时）              │
│    tenancy （经典 agentorchestra.tenancy 多租户）             │
├───────────────────────────────────────────────────────────┤
│  observability/    可观测（Prometheus 文本/OTLP/JSONL）       │
└───────────────────────────────────────────────────────────┘
```

## 依赖方向（关键约束）

| 组件 | 依赖 | 禁止依赖 |
|------|------|----------|
| `orchestration` | `state`, `core.agent` | — |
| `tx` | `state`, `governance`, `observability`（仅指标） | — |
| `agents` | `core`, `tools`, `context`, `memory` | ontology/state 业务细节 |
| `core` | 自含（工具可 import `tools` 类型） | agents（基类被 agents 继承，方向反向） |
| `ontology` | `tools`(ToolParameter), `state`(WAL hook) | agents, orchestration |
| `memory` | `core`(LLM 可选) | — |
| `state` | SQLAlchemy/aiosqlite（仅基础件） | 一切业务包 |
| `governance` | `ontology.governance`(SecurityManager) | — |
| `tenancy` | 无（ContextVar 独立） | — |
| `observability` | `core.tracing`(Span) | — |
| `components`（装配） | 全组件（聚合门面，唯一允许依赖全部） | 业务代码反向依赖它之外的装配 |

**规则**：
1. 上层可 import 下层；下层**不得** import 上层。
2. 横切组件（governance/tenancy/observability/state）彼此独立或仅依赖下层，互不横向耦合。
3. 唯一"什么都知道"的模块是 `components.py`（门面），业务包不应自行 new 全局组件。

## 组件边界 / 单一职责

| 组件 | 做什么 | 不做什么 |
|------|--------|----------|
| `state.CheckpointStore` | 持久化一切框架状态 | 不含业务字段语义 |
| `tx.TransactionCoordinator` | 编排事务生命周期 | 不直接写业务对象 |
| `orchestration.Graph` | 图执行与消息路由 | 不实现 Agent 推理 |
| `ontology.ObjectStore` | 业务对象存取 + WAL 上报 | 不决定权限（交 governance） |
| `governance.PermissionChecker` | 权限决策（RBAC+ACL） | 不执行业务动作 |
| `observability` 各 exporter | 指标/trace 输出 | 不掺入业务逻辑 |
| `tenancy.TenantManager` | 租户上下文 | 不决定配额策略（quota 独立） |

## 装配指南（components.py）

推荐统一通过 `agentorchestra.components.Components` 获取/替换横切实现：

```python
from agentorchestra.components import Components

Components.state_store()                # CheckpointStore（SQLite 默认）
Components.tracer()                     # 分布式追踪
Components.metrics_collector()          # SLO 指标（默认 NoOp）
Components.otel_exporter()              # OTLP trace（默认关）
Components.enable_prometheus()          # 一键开启 Prometheus 文本指标

# 可插拔：注入自定义实现
Components.register_state_store(lambda: MyStore())
Components.register_metrics_collector(lambda: MyCollector())
```

## 组件测试策略

- 每组件独立测试目录（tests/<component>/）
- 组件间用接口/抽象交互；替换实现用 `Components.register_*` + `Components.reset()`
- 持久层可切 InMemory 后端（无 DB 依赖）跑单测

## 路径兼容（经典扁平名 → 领域化路径）

| 经典导入名 | 新物理路径（规范） |
|-----------|-------------------|
| `agentorchestra.agents.*` | `agentorchestra.runtime.agents.*` |
| `agentorchestra.context.*` | `agentorchestra.runtime.context.*` |
| `agentorchestra.core.*` | `agentorchestra.runtime.core.*` |
| `agentorchestra.tools.*` | `agentorchestra.capability.tools.*` |
| `agentorchestra.skills.*` | `agentorchestra.capability.skills.*` |
| `agentorchestra.memory.*` | `agentorchestra.capability.memory.*` |
| `agentorchestra.orchestration.*` | `agentorchestra.orchestration.orch.*` |
| `agentorchestra.state.*` | `agentorchestra.orchestration.state.*` |
| `agentorchestra.governance.*` | `agentorchestra.governance.govern.*` |
| `agentorchestra.tx.*` | `agentorchestra.governance.tx.*` |
| `agentorchestra.tenancy.*` | `agentorchestra.governance.tenancy.*` |
| `agentorchestra.observability.*` | `agentorchestra.observability.*` |
| `agentorchestra.ontology.*` | `agentorchestra.ontology.*` |

`agentorchestra` 包导入时自动安装兼容 finder（`_legacy.py`），经典名与规范名解析到
**同一模块对象**（不重复加载、类身份一致），因此存量代码/文档/测试无需改动。

