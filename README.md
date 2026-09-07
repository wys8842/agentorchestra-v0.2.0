# Symphony

**Symphony - 多智能体应用编排框架**：让 Agent 负责思考与决策、Ontology 承载业务语义与对象操作，
而编排、事务、持久化、治理、多租户、可观测等横切能力以独立组件提供——既可开箱即用，也能按需插拔替换。

> 当前版本 **0.2.0** · Python ≥ 3.10 · MIT License

## 特性

### 运行时域 `runtime/`

| 能力 | 说明 |
|------|------|
| **Agent 五种范式** | `Simple / ReAct / Reflection / PlanSolve / Loop` + 工厂 `create_agent()` 与子代理机制 |
| **核心运行时** | LLM 统一客户端、配置体系、消息模型、Agent 基类、可靠性、可观测 telemetry（详见 [core](docs/core/README.md)） |
| **上下文工程** | 历史管理、Token 预算、压缩、工具输出截断、GSSC 上下文构建（详见 [context](docs/context/README.md)） |
| **能力扩展** | `CapabilityContext / Capability` 注册表：13 个内置能力（工具、Ontology、记忆、技能、MCP、DevLog 等）可按配置启用 |

### 能力域 `capability/`

| 能力 | 说明 |
|------|------|
| **工具系统** | `Tool` 协议 + `ToolRegistry` 统一执行管道（熔断/观测/记录）、内置工具（计算/文件/子代理/技能/MCP/DevLog/TodoWrite）（详见 [tools](docs/tools/README.md)） |
| **Skills 知识外化** | 渐进式披露：元数据 + 按需加载 SKILL.md body，节省 Token |
| **跨会话持久记忆** | `MemoryManager` 混合检索（关键词 + 向量）、自动注入/回忆、Summarizer（详见 [memory](docs/memory/README.md)） |

### 业务语义 `ontology/`

对象类型 / 链接 / 接口 / 动作 / 函数 + 对象存储与索引 + 治理（安全/审计/分支）+ 流程编排（工作流/调度/事务）+ 查询引擎 +
自动生成 Tool 挂载到任意 Agent（详见 [ontology](docs/ontology/README.md)）。

### 编排域 `orchestration/`

- `orch`：Agent 图 / DAG 通信（Graph、Inbox、DeliveryManager、Scheduler、事件）
- `state`：持久化与恢复（Checkpoint / WAL / Thread / Interrupt / Snapshot，多后端）
（详见 [orchestration](docs/orchestration/README.md)、[state](docs/state/README.md)）

### 治理域 `governance/`

- `govern`：对象身份与权限（Identity / RBAC / 行级 ACL / CAS）
- `tx`：事务运行时（Coordinator / 幂等 / 补偿 / DLQ / 锁 / 隔离）
- `tenancy`：多租户（Tenant 上下文 / 配额 / 用量计费）
（详见 [governance](docs/governance/README.md)、[tx](docs/tx/README.md)、[tenancy](docs/tenancy/README.md)）

### 可观测 `observability/`

TraceLogger 双格式轨迹（JSONL + HTML）、零依赖 Prometheus 文本指标、可选 OTLP trace 导出、SLO 常量
（详见 [observability](docs/observability/README.md)；与 `runtime/core/telemetry` 的配合见该文）。

### 装配与可插拔 `components.py`

统一装配门面：存储 / 追踪 / 指标 / trace 导出均可 `register_*` 替换、`Components.reset()` 还原，
业务代码不必依赖具体实现（详见 [architecture](docs/architecture/README.md)）。

## 安装

```bash
pip install agentorchestra            # 核心
pip install "agentorchestra[all]"     # 全部可选依赖（Anthropic/Gemini/MCP/Neo4j/YAML/Postgres/OTel）
pip install "agentorchestra[dev]"     # 开发：pytest / ruff / mypy
```

## 快速开始

```python
from agentorchestra.core.llm import SymphonyLLM
from agentorchestra.core.config import Config
from agentorchestra.tools.registry import ToolRegistry
from agentorchestra.agents.react_agent import ReActAgent

# 统一 LLM 客户端：按 base_url 自动识别提供商（OpenAI/Anthropic/Gemini 及兼容接口）
llm = SymphonyLLM(model="gpt-4o", api_key="sk-xxx", base_url="https://api.openai.com/v1")

registry = ToolRegistry()
agent = ReActAgent(name="Assistant", llm=llm, tool_registry=registry)
result = agent.run("帮我分析这个项目")
```

> 也支持环境变量：`LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL`。
> 完整示例见 `examples/agent_full_demo.py`（可离线跑，自带 Mock 演示），压力测试脚本在 `tests/` 下。

## 架构

```
应用层（examples / 业务代码）
 └─ components.py            装配门面（唯一推荐的横切装配入口）
  └─ runtime/                运行时域：agents · core · context · capabilities
   └─ capability/            能力域：tools · skills · memory
    └─ ontology/             业务语义：对象/动作/函数/接口 + 存储 + 治理 + 流程
     └─ orchestration/       编排域：orch（图/DAG）· state（持久化与恢复）
     └─ governance/          治理域：govern · tx · tenancy
     └─ observability/       可观测：trace / metrics / otlp
```

- 依赖方向自上而下：上层可 import 下层，下层不反向依赖上层。
- 横切组件（governance/observability/state 等）彼此独立或只依赖更底层。
- 唯一“什么都知道”的装配点是 `components.py`；业务包不应自行持有全局组件。

### 导入路径兼容

源码按领域组织，但**经典扁平导入名仍然有效**，二者解析到同一模块对象（不重复加载）：

| 经典导入名（推荐在示例/文档中使用） | 新物理路径 |
|------------------------------------|-----------|
| `agentorchestra.agents.*` | `agentorchestra.runtime.agents.*` |
| `agentorchestra.core.*` | `agentorchestra.runtime.core.*` |
| `agentorchestra.context.*` | `agentorchestra.runtime.context.*` |
| `agentorchestra.tools.*` / `skills.*` / `memory.*` | `agentorchestra.capability.*` |
| `agentorchestra.orchestration.*` | `agentorchestra.orchestration.orch.*` |
| `agentorchestra.state.*` | `agentorchestra.orchestration.state.*` |
| `agentorchestra.tx.*` / `tenancy.*` | `agentorchestra.governance.tx.*` / `tenancy.*` |
| `agentorchestra.governance.*` | `agentorchestra.governance.govern.*` |
| `agentorchestra.observability.*` / `ontology.*` | 不变 |

机制见 `agentorchestra/_legacy.py` 与 [architecture](docs/architecture/README.md)。

## 目录

```
agentorchestra/
├── runtime/            # 运行时域
│   ├── agents/         # Agent 范式 + 工厂 + 子代理
│   ├── context/        # 上下文工程（历史/Token/GSSC/截断）
│   ├── core/           # 核心运行时
│   │   ├── agent/      #   Agent 基类 + 生命周期
│   │   ├── config/     #   Config / 加载器 / 热更新
│   │   ├── llm/        #   SymphonyLLM + 适配器 + Schema + 流式 + 防护
│   │   ├── message/    #   Message + 会话持久化
│   │   ├── reliability/#   retry / ratelimit
│   │   └── telemetry/  #   logging / metrics / monitor / health / tracing
│   └── capabilities/   # Capability 注册表 + 内置能力
├── capability/         # 能力域
│   ├── tools/          # 工具系统（含 builtin 内置工具）
│   ├── skills/         # Skills 知识外化
│   └── memory/         # 跨会话持久记忆
├── ontology/           # 业务语义（semantic/kinetic/storage/governance/process）
├── orchestration/      # 编排域
│   ├── orch/           # Agent 图/DAG 通信
│   └── state/          # Checkpoint/WAL/Thread/Interrupt/Snapshot
├── governance/         # 治理域
│   ├── govern/         # 身份与权限（Identity/ACL/Permission/CAS）
│   ├── tx/             # 事务运行时
│   └── tenancy/        # 多租户
├── observability/      # 可观测（trace/metrics/OTLP/SLO）
├── components.py       # 装配门面
├── _legacy.py          # 经典导入名 → 领域路径兼容层
└── version.py
```

## 模块文档

每个模块文档都按固定骨架编写：**定位 → 设计动机与原则 → 好处 → 模块构成 → 功能清单 → 使用说明 → 模块关系 → 测试**。

| 领域 | 模块 | 文档 |
|------|------|------|
| 总览 | 架构设计 | [architecture](docs/architecture/README.md) |
| 运行时 | runtime 域 | [runtime](docs/runtime/README.md) |
| | agents | [agents](docs/agents/README.md) |
| | core | [core](docs/core/README.md) |
| | context | [context](docs/context/README.md) |
| 能力 | tools | [tools](docs/tools/README.md) |
| | memory | [memory](docs/memory/README.md) |
| 语义 | ontology | [ontology](docs/ontology/README.md) |
| 编排 | orchestration | [orchestration](docs/orchestration/README.md) |
| | state（持久化） | [state](docs/state/README.md) |
| 治理 | governance | [governance](docs/governance/README.md) |
| | tx（事务） | [tx](docs/tx/README.md) |
| | tenancy（多租户） | [tenancy](docs/tenancy/README.md) |
| 可观测 | observability | [observability](docs/observability/README.md) |
| 横切 | 横切能力与演进指南 | [enterprise](docs/enterprise/README.md) |

完整的模块索引与阅读顺序见 [docs/README.md](docs/README.md)。

## 开发与质量

```bash
pip install -e ".[dev]"
pytest tests                # 单元/集成测试
ruff check agentorchestra   # 代码检查
python tests/stress_test.py            # 压力测试（存储/并发/工作流/调度/工具）
python tests/stress_report.py          # 全链路压力 + 生成 docs/test_report.json
```

## License

MIT
