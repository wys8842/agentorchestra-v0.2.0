# Agents 模块

## 概述

Agents 模块提供多种 Agent 范式实现，支持不同场景的智能体需求。

## Agent 类型

### SimpleAgent

简单问答 Agent，适用于基础对话场景。

```python
from agentorchestra.runtime.agents import SimpleAgent

agent = SimpleAgent(name="Assistant", llm=llm)
result = agent.run("你好")
```

### ReActAgent

ReAct 范式 Agent，通过循环调用工具实现复杂任务。

```python
from agentorchestra.runtime.agents import ReActAgent

agent = ReActAgent(
    name="Assistant",
    llm=llm,
    tool_registry=registry,
    max_steps=10
)
result = agent.run("帮我查天气")
```

### ReflectionAgent

反思 Agent，通过迭代改进答案质量。

```python
from agentorchestra.runtime.agents import ReflectionAgent

agent = ReflectionAgent(name="Reflector", llm=llm)
result = agent.run("分析这个问题")
```

### PlanSolveAgent

计划-执行范式，先规划后执行。

```python
from agentorchestra.runtime.agents import PlanSolveAgent

agent = PlanSolveAgent(name="Planner", llm=llm)
result = agent.run("完成这个复杂任务")
```

### LoopAgent

闭环认知 Agent，实现 Plan→Act→Observe→Reflect→Check→Replan 完整认知流程。

```python
from agentorchestra.runtime.agents import LoopAgent

# 简单模式（向后兼容）
agent = LoopAgent(name="Assistant", llm=llm)

# 完整模式（启用认知闭环）
agent = LoopAgent(
    name="Assistant",
    llm=llm,
    enable_reflection=True,   # 启用反思
    enable_replan=True,    # 启用再规划
    max_steps=10,
    max_replans=3,
    max_consecutive_errors=3,
    stuck_threshold=2
)
```

## 核心数据类

### LoopState

循环状态管理：

```python
from agentorchestra.runtime.agents.loop_agent import LoopState, LoopStatus, Budget

state = LoopState(
    goal="用户目标",
    plan=Plan(),
    budget=Budget(max_steps=10, max_replans=3),
    status=LoopStatus.RUNNING
)
```

### Plan

结构化计划：

```python
from agentorchestra.runtime.agents.loop_agent import Plan

plan = Plan(
    steps=["步骤1", "步骤2"],
    current_step=0,
    success_criteria=["标准1", "标准2"]
)
```

### Evidence

工具执行证据：

```python
from agentorchestra.runtime.agents.loop_agent import Evidence

evidence = Evidence(
    tool_name="tool_name",
    tool_call_id="call_123",
    status="success",
    summary="结果摘要"
)
```

### Reflection

反思结果：

```python
from agentorchestra.runtime.agents.loop_agent import Reflection

reflection = Reflection(
    progress=0.5,
    issues=["问题1"],
    should_replan=False
)
```

### TerminationDecision

终止决策：

```python
from agentorchestra.runtime.agents.loop_agent import TerminationDecision

decision = TerminationDecision(
    signal="completed",  # completed/stuck/errors/budget/no_progress/terminate_tool
    action="stop",     # stop/replan/continue
    reason="任务完成"
)
```

## 执行模式

### 同步执行

```python
result = agent.run("问题")
```

### 异步执行

```python
result = await agent.arun("问题")
```

### 流式执行

```python
for chunk in agent.stream_run("问题"):
    print(chunk, end="")
```

### 异步流式

```python
async for event in agent.arun_stream("问题"):
    print(event)
```

## 终止信号

| 信号 | 触发条件 | 动作 |
|------|----------|------|
| terminate_tool | 模型调用 terminate 工具 | stop |
| completed | 业务目标达成 | stop |
| budget | 达到 max_steps | stop |
| errors | 连续错误超限 | stop |
| stuck | 连续重复调用 | replan |
| no_progress | 无工具调用且有证据 | stop |
