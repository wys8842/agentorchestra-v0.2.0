# Context 模块

## 概述

Context 模块提供上下文工程能力：历史管理、Token 计数、上下文构建、截断保护等。

## 组件

### History

对话历史管理：

```python
from agentorchestra.runtime.context import History

history = History(max_length=100)
history.add_message(Message("Hello", "user"))
history.add_message(Message("Hi", "assistant"))
messages = history.get_messages()
```

### TokenCounter

Token 计数：

```python
from agentorchestra.runtime.context import TokenCounter

counter = TokenCounter()
count = counter.count_messages(messages)
count = counter.count_tokens("text content")
```

### ContextBuilder

GSSC 上下文构建器：

```python
from agentorchestra.runtime.context import ContextBuilder, ContextConfig

config = ContextConfig(max_tokens=8000)
builder = ContextBuilder(config=config)

context = builder.build(
    user_input="问题",
    history=history,
    system_prompt="你是一个助手"
)
```

### Truncator

工具输出截断：

```python
from agentorchestra.runtime.context import Truncator

truncator = Truncator(max_lines=2000, max_bytes=51200)
result = truncator.truncate(tool_name="tool", output=large_text)
```

## 设计原理

### 历史压缩

当历史过长时自动压缩：

```python
# 压缩策略
- 保留系统消息
- 保留用户首条消息
- 保留最近 N 轮完整对话
- 中间消息摘要
```

### Token 预算

```python
# 预算分配示例
总预算: 128000
├── 系统提示: 2000
├── 历史消息: 8000
├── 用户输入: 1000
└── 预留空间: 117000
```

### 截断保护

工具输出截断规则：

```python
# 行数限制
max_lines: 2000

# 字节限制
max_bytes: 51200

# 截断方向
truncate_direction: "head"  # 保留开头
```
