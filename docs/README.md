# Symphony 文档

## 模块总览

| 模块 | 说明 |
|------|------|
| [runtime](runtime/README.md) | 运行时域：Agent/核心/上下文/能力 |
| [capability](capability/README.md) | 能力域：工具/技能/记忆 |
| [ontology](ontology/README.md) | 企业级本体 |
| [orchestration](orchestration/README.md) | 编排域：图通信/状态 |
| [governance](governance/README.md) | 治理域：权限/事务/多租户 |
| [observability](observability/README.md) | 可观测性 |

## 架构

```
┌─────────────────────────────────────┐
│           应用层 (Application)            │
├─────────────────────────────────────┤
│  runtime/agents  │ Agent 范式实现            │
├─────────────────────────────────────┤
│  runtime/core    │ LLM/Config/Message/Agent │
├─────────────────────────────────────┤
│  runtime/context │ 上下文工程               │
├─────────────────────────────────────┤
│  capability/    │ Tools/Skills/Memory       │
├─────────────────────────────────────┤
│  ontology/     │ 对象/动作/函数           │
├─────────────────────────────────────┤
│  orchestration/ │ Graph/DAG/Checkpoint     │
├─────────────────────────────────────┤
│  governance/   │ 权限/事务/多租户        │
├─────────────────────────────────────┤
│  observability/ │ Trace/Metrics          │
└─────────────────────────────────────┘
```

## 快速开始

```python
from agentorchestra import ReActAgent, SymphonyLLM, ToolRegistry

llm = SymphonyLLM(model="gpt-4o", api_key="sk-xxx")
registry = ToolRegistry()
agent = ReActAgent(name="Assistant", llm=llm, tool_registry=registry)
result = agent.run("你的问题")
```
