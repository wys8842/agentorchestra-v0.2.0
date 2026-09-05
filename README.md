# Symphony

> **当前版本 0.2.0** - 企业级多智能体编排框架

Symphony 是一个面向生产的**企业级多智能体编排框架**：Agent 负责思考与决策，
Ontology 承载业务语义与对象操作，而编排、事务、持久化、治理与多租户等能力
由框架以组件化方式提供——既可开箱即用，也能按需插拔替换。

从"Demo 框架"到"可生产 Multi-Agent 平台"，Symphony 内置：

- **确定性编排**：Workflow / Scheduler / 图(DAG) 三种编排模型，覆盖线性流程、定时调度与条件协作
- **可恢复事务**：WAL + Checkpoint 崩溃恢复、幂等，逆序补偿、DLQ
- **企业治理**：对象身份 + RBAC/行级 ACL + WORM 审计
- **多租户与配额**：租户隔离、token 配额，用量导出
- **可插拔组件**：统一装配门面，存储 / 追踪 / 指标均可替换

## 特性

- **多智能体**：Simple / ReAct / Reflection / PlanSolve / Loop 五种范式 + 子代理机制
- **企业级 Ontology**：对象类型 / 链接 / 动作 / 函数 / 接口，统一业务语义建模
- **执行编排**：Workflow（流程）、Scheduler（调度）、Transaction（事务补偿）
- **治理**：权限 / 审计 / 分支 / 物化
- **工具生态**：内置工具（文件/计算/子代理/技能/MCP）+ 自定义 Tool
- **上下文工程**：历史管理、Token 预算、压缩、GSSC 流水线
- **可观测**：TraceLogger 双格式（JSONL+HTML）审计、事件系统，流式输出
- **Skills 知识外化**：渐进式披露（元数据 + 按需 body)，节省 Token
- **跨会话持久记忆**：长期/短期/工作之外的"跨任务记忆"，混合检索（关键词+向量）
- **企业级运维**：结构化日志、Prometheus 指标、分布式追踪、限流、配置热更新、健康检查、监控端点
- **企业级就绪**：
  - **持久化与恢复**：WAL + Checkpoint + Snapshot + HITL interrupt（`state/`）
  - **事务运行时**：幂等 + 补偿 + DLQ + 乐观锁（`tx/`）
  - **Agent 图/DAG 通信**：条件边 + Inbox + 有界回环（`orchestration/`）
  - **对象身份与权限**：RBAC + 行级 ACL + WORM 审计（`governance/`）
  - **多租户**：tenant namespace 隔离 + token 配额 + 用量导出（`tenancy/`）
  - **企业级可观测**：Prometheus 文本指标 + 可选 OTLP trace（零依赖）

## 安装

```bash
pip install agentorchestra            # 核心
pip install "agentorchestra[all]"     # 全部可选依赖（Anthropic/Gemini/MCP/Neo4j/YAML）
```

## 快速开始

```python
from agentorchestra import ReActAgent, SymphonyLLM, ToolRegistry

# 统一 LLM 客户端：自动按 base_url 识别提供商（OpenAI/Anthropic/Gemini 及兼容接口）
llm = SymphonyLLM(
    model="gpt-4o",
    api_key="sk-xxx",
    base_url="https://api.openai.com/v1",
)
registry = ToolRegistry()

agent = ReActAgent(name="Assistant", llm=llm, tool_registry=registry)
result = agent.run("帮我分析这个项目")
```

> 也支持从环境变量加载：`LLM_MODEL_ID` / `LLM_API_KEY` / `LLM_BASE_URL`。

## 统一装配入口

Symphony 提供统一的 `Components` 门面来装配和替换横切组件：

```python
from agentorchestra.components import Components

# 读取（默认回退现有全局实现）
store = Components.state_store()            # CheckpointStore
tracer = Components.tracer()                # Tracer
collector = Components.metrics_collector()  # 指标收集器

# 替换（可插拔）
Components.register_state_store(my_store)
Components.enable_prometheus()              # 开启 Prometheus 指标
Components.enable_otel_trace()            # 开启 OTLP 导出
```

## 架构

```
应用层
  └─ Agent 层（agents + core）        决策 / 工具调用 / 上下文 / 事件
       └─ Tool 契约（tools + context）  schema / 执行 / 注入
            └─ 业务语义层（ontology）   对象/动作/函数/接口 + 治理 + 编排
                 └─ 数据层             数据库 / 文件 / 外部系统 / MCP
```

## 目录

源码按**领域**组织在 `agentorchestra/` 下（经典扁平导入名仍可用）：

```
agentorchestra/
├── runtime/              # 运行时域
│   ├── agents/           # Agent 范式（Simple/ReAct/Reflection/PlanSolve/Loop + 工厂）
│   ├── core/             # 核心运行时（LLM/Config/Message/Agent 基类/可靠性/运维/追踪）
│   └── context/          # 上下文工程（历史/Token 计数/GSSC/截断）
├── capability/           # 能力域
│   ├── tools/            # 工具系统（Tool 基类/注册表/内置工具/子代理过滤）
│   ├── skills/           # Skills 知识外化（SkillLoader/Skill）
│   └── memory/           # 跨会话持久记忆（Manager/混合检索/Summarizer/工具）
├── ontology/             # 企业级 Ontology（语义/动能/存储/治理/流程/工具生成）
├── orchestration/        # 编排域
│   ├── orch/             # Agent 图/DAG 通信（Graph/Inbox/节点/Scheduler）
│   └── state/            # 持久化与恢复（Checkpoint/WAL/Thread/Interrupt/Snapshot）
├── governance/           # 治理域
│   ├── govern/           # 对象身份与权限（Identity/ACL/Permission/CAS/WORM）
│   ├── tx/               # 事务运行时（Coordinator/幂等/补偿/DLQ/乐观锁）
│   └── tenancy/          # 多租户（Tenant 上下文/配额/用量导出）
├── observability/        # 可观测性（TraceLogger + Prometheus 指标 + 可选 OTLP）
├── components.py         # 统一装配门面（唯一推荐的横切装配入口）
├── version.py
└── __init__.py
```

> **兼容性**：经典扁平公共 API 保持不变——`agentorchestra.core.*`、
> `agentorchestra.tools.*`、`agentorchestra.state.*`、`agentorchestra.tx.*`、
> `agentorchestra.orchestration.*` 等导入会自动映射到上面的领域化物理路径
> （见 `agentorchestra/_legacy.py`）。同一模块无论走经典名还是新物理名，
> 得到的是同一个模块对象。

## 公共 API

```python
# 核心组件
from agentorchestra import (
    SymphonyLLM,      # 统一 LLM 客户端
    Config,           # 配置管理
    Message,          # 消息类型
    SymphonyException, # 异常基类

    # Agent 范式
    SimpleAgent,
    ReActAgent,
    ReflectionAgent,
    PlanSolveAgent,

    # 工具系统
    ToolRegistry,
    global_registry,
    CalculatorTool,
    calculate,
)
```

## 文档

| 模块                 | 文档                                                                                        |
| ------------------ | ----------------------------------------------------------------------------------------- |
| core               | [docs/core/README.md](docs/core/README.md)                                                |
| agents             | [docs/agents/README.md](docs/agents/README.md)                                            |
| tools              | [docs/tools/README.md](docs/tools/README.md)                                              |
| context            | [docs/context/README.md](docs/context/README.md)                                          |
| observability      | [docs/observability/README.md](docs/observability/README.md)                              |
| memory             | [docs/memory/README.md](docs/memory/README.md)                                            |
| ontology           | [docs/ontology/README.md](docs/ontology/README.md)                                        |
| state（持久化）         | [docs/state/README.md](docs/state/README.md)                                              |
| tx（事务运行时）          | [docs/tx/README.md](docs/tx/README.md)                                                    |
| orchestration（图通信） | [docs/orchestration/README.md](docs/orchestration/README.md)                              |
| governance（权限）     | [docs/governance/README.md](docs/governance/README.md)                                    |
| tenancy（多租户）       | [docs/tenancy/README.md](docs/tenancy/README.md)                                          |
| 企业级路线图             | [docs/enterprise/README.md](docs/enterprise/README.md) + [specs](docs/superpowers/specs/) |

## 开发

```bash
pip install "agentorchestra[dev]"
pytest              # 运行测试
mypy agentorchestra # 类型检查
ruff check .        # 代码检查
```

## License

MIT
