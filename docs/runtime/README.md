# Runtime 模块

## 概述

Runtime 模块是 Symphony 框架的核心运行时，包含 Agent 实现、LLM 客户端、配置管理、消息处理等核心功能。

## 目录结构

```
runtime/
├── agents/       # Agent 范式实现
├── core/        # 核心运行时
├── context/      # 上下文工程
└── capabilities/ # 能力组件
```

## Agents

### Agent 类型

| Agent 类型 | 说明 | 适用场景 |
|-----------|------|----------|
| SimpleAgent | 简单问答 | 基础对话 |
| ReActAgent | ReAct 循环 | 工具调用 |
| ReflectionAgent | 反思迭代 | 质量改进 |
| PlanSolveAgent | 计划-执行 | 复杂任务 |
| LoopAgent | 闭环认知 | Plan→Act→Observe→Reflect→Check→Replan |

### LoopAgent 闭环认知

```python
from agentorchestra import LoopAgent

# 启用反思和再规划
agent = LoopAgent(
    name="Assistant",
    llm=llm,
    enable_reflection=True,  # 启用反思
    enable_replan=True,     # 启用再规划
    max_steps=10,
    max_replans=3
)
```

### 核心方法

- `run()` - 同步执行
- `arun()` - 异步执行
- `stream_run()` - 流式输出
- `arun_stream()` - 异步流式

## Core

### SymphonyLLM

统一 LLM 客户端，自动识别 Provider：

```python
from agentorchestra import SymphonyLLM

# OpenAI
llm = SymphonyLLM(model="gpt-4o", api_key="sk-xxx")

# Anthropic
llm = SymphonyLLM(model="claude-3", api_key="sk-xxx", provider="anthropic")

# Gemini
llm = SymphonyLLM(model="gemini-pro", api_key="xxx", provider="gemini")
```

### Config

配置管理：

```python
from agentorchestra import Config

# 开发配置
config = Config.development()

# 生产配置
config = Config.production()

# 自定义配置
config.llm.temperature = 0.7
```

### Message

消息类型：

```python
from agentorchestra import Message

msg = Message("Hello", "user")
```

## Context

### 历史管理

```python
# 自动保存对话历史
agent.add_message(Message("content", "user"))
```

### Token 计数

```python
from agentorchestra.runtime.context import TokenCounter

counter = TokenCounter()
count = counter.count_messages(messages)
```

### GSSC 上下文

```python
from agentorchestra.runtime.context import ContextBuilder

builder = ContextBuilder()
context = builder.build(user_input, history, system_prompt)
```

## Capabilities

内置能力组件：

```python
from agentorchestra.runtime.capabilities import TraceLogger, MemoryManager

# TraceLogger - 追踪日志
logger = TraceLogger(output_dir="traces")

# MemoryManager - 跨会话记忆
manager = MemoryManager()
```
