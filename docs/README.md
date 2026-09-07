# Symphony 文档索引

**Symphony - 多智能体应用编排框架**的完整文档。所有模块文档统一采用固定骨架编写：
定位 → **设计动机与原则** → **好处** → **模块构成** → **功能清单** → **使用说明** → **模块关系** → **测试**，
方便你快速理解“每个模块提供哪些功能、为什么这样设计、如何使用”。

## 从哪里开始

1. 先读 [architecture/README.md](architecture/README.md)：整体分层、领域目录、依赖方向、装配门面与兼容层。
2. 再按“运行时 → 能力 → 语义 → 编排 → 治理 → 可观测”的顺序读各模块文档。
3. 需要横切能力（多租户/安全/事务/持久化/可观测）的上线与启用建议，读 [enterprise/README.md](enterprise/README.md)。

## 按领域索引

### 运行时 runtime
| 文档 | 内容 |
|------|------|
| [runtime](runtime/README.md) | 运行时域总览：agents / core / context / capabilities 的分工与 Capability 扩展机制 |
| [agents](agents/README.md) | Agent 五种范式（Simple/ReAct/Reflection/PlanSolve/Loop）、工厂、子代理、执行器 |
| [core](core/README.md) | 核心运行时：Agent 基类、配置体系、LLM 统一客户端、消息与会话、可靠性、telemetry |
| [context](context/README.md) | 上下文工程：历史管理、Token 预算、截断、GSSC 上下文构建 |

### 能力 capability
| 文档 | 内容 |
|------|------|
| [tools](tools/README.md) | 工具系统：Tool 协议、注册表统一执行、熔断、过滤、内置工具 |
| [memory](memory/README.md) | 跨会话持久记忆：模型、检索、衰减、Summarizer、Agent 集成工具 |

### 业务语义
| 文档 | 内容 |
|------|------|
| [ontology](ontology/README.md) | 对象/链接/接口/动作/函数 + 存储索引 + 治理 + 流程编排 + 查询 + 自动生成 Tool |

### 编排 orchestration
| 文档 | 内容 |
|------|------|
| [orchestration](orchestration/README.md) | Agent 图 / DAG 通信：Graph、Inbox、Delivery、Scheduler、事件 |
| [state](state/README.md) | 持久化与恢复：Checkpoint、WAL、Thread、Interrupt、Snapshot 与多后端 |

### 治理 governance
| 文档 | 内容 |
|------|------|
| [governance](governance/README.md) | 对象身份与权限：Identity、RBAC、行级 ACL、CAS |
| [tx](tx/README.md) | 事务运行时：Coordinator、幂等、补偿、DLQ、锁、隔离 |
| [tenancy](tenancy/README.md) | 多租户：Tenant 上下文、配额、用量计费 |

### 可观测
| 文档 | 内容 |
|------|------|
| [observability](observability/README.md) | TraceLogger 轨迹、Prometheus 文本指标、OTLP 导出、SLO |

### 架构与横切
| 文档 | 内容 |
|------|------|
| [architecture](architecture/README.md) | 架构总览：分层/依赖方向/装配门面 components/兼容层 _legacy |
| [enterprise](enterprise/README.md) | 跨模块横切能力清单、逐步启用路线与上线检查 |

## 文档写作约定

- 物理源码按领域目录组织（`runtime/`、`capability/`、`ontology/`、`orchestration/`、`governance/`、`observability/`）。
- 代码示例优先使用**经典扁平导入名**（`agentorchestra.core.llm`、`agentorchestra.state`…），它们经 `agentorchestra/_legacy.py`
  自动映射到新物理路径且与规范路径共享同一模块对象；各文档会同时注明规范路径，方便阅读源码。
- 每篇文档的功能清单、导出名与示例都以真实源码为准，新增/改动 API 后请同步更新对应文档。

## 运行验证

```bash
pip install -e ".[dev]"
pytest tests                 # 单元/集成测试
ruff check agentorchestra    # 代码检查
```
