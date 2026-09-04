# core - 核心层

核心运行时：LLM 接口、配置、消息模型、Agent 基类、会话持久化、生命周期事件、流式输出。

## 模块组成

| 文件 | 职责 |
|------|------|
| `llm.py` | `SymphonyLLM`：统一 LLM 接口（同步/异步/流式/工具调用 + 重试 + 观测埋点） |
| `llm_adapters.py` | 多提供商适配（OpenAI / Anthropic / Gemini） |
| `llm_response.py` | `LLMResponse`：响应模型（含 usage / reasoning） |
| `config.py` | `Config`：pydantic 配置模型，所有可调参数 |
| `config_loader.py` | `ConfigLoader`：环境变量/JSON 文件加载 + 密钥脱敏 |
| `hot_config.py` | `ConfigWatch`：配置热更新（文件监听 + 回调） |
| `message.py` | `Message`：消息模型（content/role/timestamp/metadata） |
| `agent.py` | `Agent` 基类：组件挂载 + 工具链路 + 会话 + 子代理 |
| `lifecycle.py` | 生命周期事件系统：`AgentEvent` / `EventType` / 钩子 |
| `streaming.py` | 流式输出：`StreamEvent` / SSE / JSON Lines |
| `session_store.py` | 会话持久化：保存/恢复/一致性检查 |
| `exceptions.py` | 统一异常体系（模块分层 + error_code） |
| `retry.py` | `RetryManager` / `retry_with_backoff`：指数退避重试 |
| `logging.py` | 结构化日志：JSON 格式化 + 全局配置 + 上下文字段 |
| `metrics.py` | Prometheus 指标：LLM/工具/动作埋点（可选依赖） |
| `tracing.py` | 分布式追踪：Trace/Span 上下文 + 导出器（内存/JSONL） |
| `ratelimit.py` | 限流：TokenBucket / SlidingWindow / RateLimiter |
| `health.py` | `HealthCheck`：健康检查（组件状态聚合报告） |
| `monitor.py` | `MonitorServer`：监控 HTTP 端点（/metrics /health /traces） |
| `utils.py` | 通用工具：时长度量 / 工具参数解析 / 序列化辅助 |

> 模块级导出见 [core/__init__.py](../../agentorchestra/runtime/core/__init__.py)，另含 `StreamStats`（`LLMResponse` 的向后兼容别名）。经典导入路径 `agentorchestra.core.*` 由 `_legacy.py` 兼容层映射到 `agentorchestra.runtime.core.*`。

## 核心概念

### 1. SymphonyLLM

统一 LLM 接口，按 `base_url` 自动识别提供商（OpenAI / Anthropic / Gemini 及兼容接口）：

```python
from agentorchestra.core.llm import SymphonyLLM

# 必填：model / api_key / base_url（也可用 LLM_MODEL_ID/LLM_API_KEY/LLM_BASE_URL 环境变量）
llm = SymphonyLLM(
    model="gpt-4o",
    api_key="sk-xxx",
    base_url="https://api.openai.com/v1",
)
response = llm.invoke([{"role": "user", "content": "你好"}])
print(response.content)

# 工具调用（Function Calling）
response = llm.invoke_with_tools(messages, tools=[schema], tool_choice="auto")

# 流式
for chunk in llm.stream_invoke(messages):
    print(chunk, end="")

# 异步
await llm.ainvoke(messages)
async for chunk in llm.astream_invoke(messages):
    print(chunk, end="")
```

### 2. Agent 基类

所有 Agent 的基类，挂载框架组件：

```python
class Agent(ABC):
    def __init__(self, name, llm, system_prompt, config, tool_registry):
        # 上下文工程
        self.history_manager = HistoryManager(...)
        self.token_counter = TokenCounter(...)
        self.truncator = ObservationTruncator(...)
        # 可观测性
        self.trace_logger = TraceLogger(...)
        # 工具
        self.tool_registry = tool_registry
        # 会话
        self.session_store = SessionStore(...)
        # 企业级 Ontology（可选）
        self.ontology_engine = OntologyEngine(...)
```

关键方法：
- `_build_tool_schemas()` — 生成工具 schema 发给 LLM
- `_execute_tool_call()` — 统一执行工具（类型转换 + ToolResponse）
- `add_message()` — 历史写入 + Token 计数 + 压缩检查
- `run_as_subagent()` — 子代理上下文隔离
- `_emit_event()` — 生命周期钩子调度

### 3. 生命周期事件

```python
from agentorchestra.core.lifecycle import EventType, AgentEvent

async def on_finish(event: AgentEvent):
    print(f"Agent 完成: {event.data['result']}")

await agent.arun("问题", on_finish=on_finish)
```

钩子（on_start/on_step/on_tool_call/on_finish/on_error）5 秒超时 + 异常隔离。

### 4. 流式输出

```python
async for event in agent.arun_stream("帮我写冒泡排序"):
    if event.type == StreamEventType.LLM_CHUNK:
        print(event.data["chunk"], end="")
```

### 5. 会话持久化

```python
agent.save_session("session-01")   # 保存历史到 JSON
agent.load_session("session-01")   # 恢复
```

## 配置

```python
from agentorchestra.core.config import Config

config = Config(
    default_model="gpt-4o",
    context_window=128000,
    trace_enabled=True,
    ontology_engine_enabled=True,
)
```

## 运维能力

### 配置加载与热更新

```python
from agentorchestra.core.config import Config
from agentorchestra.core.hot_config import ConfigWatch

# 从环境变量/JSON 加载（优先级：显式 > 文件 > env）
config = Config.from_env("SYMPHONY_")
config = Config.from_file("config.json")

# 配置热更新（文件变更自动重新加载 + 回调）
watch = ConfigWatch(Config, "config.json")
watch.on_change(lambda old_c, new_c: print("配置更新"))
watch.start()
```

### 结构化日志

```python
from agentorchestra.core.logging import setup_logging, get_logger

setup_logging(level="INFO", json_format=True, log_file="logs/agentorchestra.log")
logger = get_logger("core.llm")
logger.info("事件", extra={"session_id": "s-123", "step": 1})  # JSON 输出
```

### 指标与追踪

```python
from agentorchestra.core.metrics import get_metrics
from agentorchestra.core.tracing import get_tracer, JsonlExporter

metrics = get_metrics()
metrics.record_llm_call("gpt-4o", "openai", tokens=100, latency_ms=500)

tracer = get_tracer(JsonlExporter("memory/traces/spans.jsonl"))
with tracer.span("llm.invoke", {"model": "gpt-4o"}):
    pass  # 嵌套 span 自动传播 trace_id
```

### 限流与健康检查

```python
from agentorchestra.core.ratelimit import RateLimiter
from agentorchestra.core.health import HealthCheck
from agentorchestra.core.monitor import MonitorServer

limiter = RateLimiter(default_limit=100, window_seconds=60)
limiter.try_acquire("user_a")  # 按 key 限流

hc = HealthCheck("agentorchestra")
hc.register_basic()

# 监控服务：/metrics /health /traces
server = MonitorServer(port=9090, health_check=hc,
                       metrics_provider=metrics.generate_latest,
                       traces_provider=tracer.export_all)
server.start()
```
