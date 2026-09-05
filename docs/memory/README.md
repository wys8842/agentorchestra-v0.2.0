# Memory 模块

## 概述

Memory 模块提供跨会话持久记忆能力，支持短期/长期/工作记忆，混合检索。

## 组件

### MemoryManager

记忆管理器：

```python
from agentorchestra.capability.memory import MemoryManager

manager = MemoryManager()

# 存储记忆
manager.remember(
    content="重要信息",
    record_type="fact",
    namespace="default"
)

# 检索记忆
results = manager.recall("查询内容", top_k=5)

# 摘要
summary = manager.summarize(namespace="default")
```

### Embedder

向量嵌入：

```python
from agentorchestra.capability.memory import Embedder

embedder = Embedder()
vector = embedder.embed("文本")
```

### Index

记忆索引：

```python
from agentorchestra.capability.memory import MemoryIndex

index = MemoryIndex()
index.add("id", "content", vector)
results = index.search(query_vector, top_k=5)
```

### Storage

存储后端：

```python
from agentorchestra.capability.memory import MemoryStorage

# SQLite 存储
storage = MemoryStorage(backend="sqlite", path="memory.db")

# 内存存储
storage = MemoryStorage(backend="memory")
```

## 记忆类型

| 类型 | 说明 | 持久化 |
|------|------|----------|
| fact | 事实性记忆 | 是 |
| conversation | 对话记忆 | 是 |
| context | 上下文记忆 | 否 |
| working | 工作记忆 | 否 |
