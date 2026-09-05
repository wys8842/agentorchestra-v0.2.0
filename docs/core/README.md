# Core 模块

## 概述

Core 模块提供框架的核心功能：LLM 客户端、配置管理、消息类型、异常体系等。

## 组件

### SymphonyLLM

统一 LLM 客户端，支持多 Provider：

```python
from agentorchestra.runtime.core import SymphonyLLM

# OpenAI
llm = SymphonyLLM(
    model="gpt-4o",
    api_key="sk-xxx",
    base_url="https://api.openai.com/v1"
)

# Anthropic
llm = SymphonyLLM(
    model="claude-3-opus",
    api_key="sk-ant-xxx",
    provider="anthropic"
)

# Gemini
llm = SymphonyLLM(
    model="gemini-pro",
    api_key="xxx",
    provider="gemini"
)

# DeepSeek
llm = SymphonyLLM(
    model="deepseek-chat",
    api_key="sk-xxx",
    provider="deepseek"
)
```

### Config

配置管理：

```python
from agentorchestra.runtime.core import Config

# 默认配置
config = Config()

# 开发配置
config = Config.development()

# 生产配置
config = Config.production()

# 环境变量加载
config = Config.from_env()

# 文件加载
config = Config.from_file("config.json")
```

配置结构：

```python
Config/
├── llm/          # LLM 配置
├── system/       # 系统配置
├── history/      # 历史管理
├── trace/        # 追踪配置
├── skills/       # Skills 配置
├── mcp/          # MCP 配置
├── ontology/      # Ontology 配置
├── session/      # 会话配置
├── subagent/      # 子代理配置
└── memory/       # 记忆配置
```

### Message

消息类型：

```python
from agentorchestra.runtime.core import Message

# 创建消息
msg = Message("内容", "user")  # user/assistant/system/tool

# 序列化
msg.to_dict()

# 反序列化
msg = Message.from_dict({"content": "x", "role": "user"})
```

### Exception

统一异常体系：

```python
from agentorchestra.runtime.core import SymphonyException
from agentorchestra.runtime.core.exceptions import (
    LLMException,
    AgentException,
    ToolException,
    ConfigException
)

# 使用
raise SymphonyException("错误信息", error_code="ERROR_CODE")
```

## 核心功能

### LifecycleHook

生命周期钩子：

```python
from agentorchestra.runtime.core.lifecycle import LifecycleHook, EventType

def on_start(input_text):
    print(f"开始: {input_text}")

hook = LifecycleHook(on_start=on_start)
```

### StreamEvent

流式事件：

```python
from agentorchestra.runtime.core.streaming import StreamEvent, StreamEventType

event = StreamEvent.create(
    StreamEventType.AGENT_START,
    "agent_name",
    input_text="问题"
)
```

### Retry

重试机制：

```python
from agentorchestra.runtime.core.retry import retry, RetryConfig

@retry(max_attempts=3, backoff=2.0)
def unstable_function():
    pass
```

### RateLimit

限流：

```python
from agentorchestra.runtime.core.ratelimit import RateLimiter

limiter = RateLimiter(max_calls=10, period=60)
limiter.acquire()
```

### Health

健康检查：

```python
from agentorchestra.runtime.core.health import HealthChecker

checker = HealthChecker()
status = checker.check()
```
