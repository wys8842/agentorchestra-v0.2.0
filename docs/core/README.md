# core：核心运行时（配置 / 消息 / LLM / Agent 基类 / 可靠性 / 观测）

> 本模块（`agentorchestra.core`，规范路径 `agentorchestra.runtime.core`）是框架的底座：统一 LLM 接口、Pydantic 配置体系、消息模型、Agent 抽象基类，以及日志/指标/追踪/健康检查等运维能力与重试/限流等可靠性原语。

## 设计动机与原则

1. **按职责拆分子包，顶层只做聚合**。`core` 不再是一个巨型模块，而是按 `agent`（Agent 基类与生命周期）、`config`（配置）、`llm`（LLM 客户端/适配器/Schema/流式）、`message`（消息与会话兼容层）、`reliability`（重试/限流）、`telemetry`（日志/指标/健康/监控/追踪）六个子包物理分组；`runtime/core/__init__.py` 仅做 `__all__` 聚合，用户只需一个 import 面。
2. **向后兼容是显式约束**。重构后 `agentorchestra.core.llm`、`agentorchestra.core.llm_response`、`agentorchestra.core.hot_config` 等"经典扁平路径"仍可用——`_legacy.py` 对 `core` 的深层模块逐一映射到新物理位置（见 `_LEGACY_CORE` 表），且保证经典名与规范名指向**同一模块对象**（类身份一致，不重复加载）。`StreamStats` 作为 `LLMResponse` 的别名保留。
3. **opt-in by default，防隐式副作用**。非核心 feature（trace 文件、session 磁盘、skills 扫描、memory 建库、MCP 连接、ontology 引擎等）全部默认关闭，必须显式 `config.xxx.enabled = True` 才会生效；只有 LLM/配置/消息/熔断/截断这类"核心"默认开。
4. **配置分层：子配置类 + 顶层 facade**。`Config` 由 16+ 个子配置（`LLMConfig/SystemConfig/HistoryConfig/...`）聚合；旧扁平字段名（如 `config.temperature`）经 `_LEGACY_FIELD_MAP` 自动代理到子配置字段，新老写法并存。
5. **LLM 多供应商 = 适配器模式 + base_url 自动路由**。`SymphonyLLM` 持有统一配置（model/api_key/base_url/temperature/timeout/重试），`create_adapter()` 按 `base_url` 关键字（anthropic.com / googleapis.com）自动选 OpenAI/Anthropic/Gemini 适配器，OpenAI 兼容协议（DeepSeek、Qwen、Ollama 等）默认走 OpenAIAdapter。thinking model（o1/deepseek-reasoner）的推理过程自动剥离到 `reasoning_content`。
6. **横切能力内置而非外包**。LLM 每次调用自动套 tracing span、指标埋点与日志；工具执行由 registry 统一收口熔断与观测；Agent 生命周期事件与钩子抽象成 `AgentEvent/EventType/LifecycleHook`。调用方代码几乎无感知。
7. **可靠性原语小而独立**。重试（`retry_with_backoff` / `RetryManager`）与限流（`TokenBucket/SlidingWindow/RateLimiter`）实现无第三方运行时依赖，便于在 LLM 调用、工具调用等不稳定路径外复用。
8. **装配与热更集中到 Components**。`agentorchestra.components.Components` 提供 store/tracer/metrics 的注册-回退默认机制，`ConfigWatch` 检测到配置变更时通过 `Components.on_config_change`/`notify_config_change` 广播，`Agent/SymphonyLLM/RateLimiter` 各自注册回调自动跟随（temperature/max_tokens/timeout/并发上限等）。

## 设计优势

- 统一 import 面：`from agentorchestra.core import Config, SymphonyLLM, Message, get_tracer, RetryManager, ...` 一行拿到全部公共符号，经典路径不破。
- 配置一处建模、多端消费，热更后 Agent/LLM/限流器行为自动刷新，无需重启。
- 换模型供应商只改 `base_url`（或换适配器），业务代码不变。
- 默认零副作用：不开的 feature 不建目录、不扫文件、不连网、不建库。
- 可观测三件套（日志/指标/追踪）是框架内部默认埋点，不是事后补丁；Health/Monitor 提供零外部依赖的运维端点。

## 模块构成

物理路径 | 子模块职责 | 主要公开导出（真实存在）
--- | --- | ---
`runtime/core/__init__.py` | 顶层聚合 | `Agent`、`SymphonyLLM`、`Message`、`Config`、`ConfigLoader`、`ConfigWatch`、`HealthCheck`、`MonitorServer`、`SymphonyException`、`LLMResponse`、`StreamStats`（= `LLMResponse` 别名）、`setup_logging`、`get_logger`、`MetricsCollector`、`get_metrics`、`Tracer`、`Span`、`MemoryExporter`、`JsonlExporter`、`get_tracer`、`RetryManager`、`retry_with_backoff`、`TokenBucket`、`SlidingWindow`、`RateLimiter`
`runtime/core/agent/base.py` | Agent 抽象基类（历史/工具/子代理/会话/checkpoint/并发集成） | `Agent`
`runtime/core/agent/lifecycle.py` | Agent 异步生命周期事件系统 | `EventType`、`AgentEvent`、`ExecutionContext`、`LifecycleHook`（类型别名）
`runtime/core/config/__init__.py` | 分层配置模型 | `Config` + 子配置类 `LLMConfig`/`SystemConfig`/`HistoryConfig`/`SmartCompressionConfig`/`ContextBuilderConfig`/`TraceConfig`/`ToolOutputConfig`/`CircuitBreakerConfig`/`SkillsConfig`/`MCPConfig`/`OntologyConfig`/`SessionConfig`/`StateCheckpointConfig`/`SubAgentConfig`/`TodoWriteConfig`/`DevLogConfig`/`MemoryConfig`
`runtime/core/config/loader.py` | 配置加载/脱敏 | `ConfigLoader`、`SENSITIVE_KEYS`
`runtime/core/config/hot.py` | 配置热更新 | `ConfigWatch`、`register_config_callback`、`unregister_config_callback`、`notify_config_change`、`start_global_hot_reload`、`stop_global_hot_reload`
`runtime/core/llm/__init__.py` | 统一 LLM 客户端 | `SymphonyLLM`（模块还聚合 `BaseLLMAdapter`/`create_adapter`/`LLMResponse`）
`runtime/core/llm/adapters.py` | 供应商适配器 | `BaseLLMAdapter`、`OpenAIAdapter`、`AnthropicAdapter`、`GeminiAdapter`、`create_adapter`
`runtime/core/llm/response.py` | 统一响应对象 | `LLMResponse`（model/content/usage/latency_ms/reasoning_content）
`runtime/core/llm/schema.py` | Schema/错误分类/响应缓存 | `LLMErrorType`、`NormalizedToolCall`、`NormalizedTool`、`NormalizedMessage`、`LLMCache`、`classify_llm_error`
`runtime/core/llm/streaming.py` | 流式事件与 SSE/JSON 转换 | `StreamEventType`、`StreamEvent`、`StreamBuffer`、`stream_to_sse`、`stream_to_json`
`runtime/core/llm/guard.py` | Prompt 注入防护 | `ThreatLevel`、`SanitizeResult`、`PromptSanitizer`（经典路径 `agentorchestra.core.prompt_guard`）
`runtime/core/message/__init__.py` | 消息模型 | `Message`、`MessageRole`
`runtime/core/message/session.py` | 会话持久化兼容层 | `SessionStore`（`_CheckpointedSessionStore` 为未启用开发中实现）
`runtime/core/reliability/retry.py` | 重试原语 | `retry_with_backoff`、`RetryManager`
`runtime/core/reliability/ratelimit.py` | 限流原语 | `TokenBucket`、`SlidingWindow`、`RateLimiter`
`runtime/core/telemetry/logging.py` | 结构化日志 | `setup_logging`、`get_logger`、`log_event`、`JsonFormatter`
`runtime/core/telemetry/metrics.py` | Prometheus 指标收集 | `MetricsCollector`、`get_metrics`
`runtime/core/telemetry/health.py` | 健康检查 | `HealthCheck`
`runtime/core/telemetry/monitor.py` | 运维 HTTP 服务（标准库） | `MonitorServer`
`runtime/core/telemetry/tracing.py` | 轻量分布式追踪 | `Span`、`SpanExporter`、`MemoryExporter`、`JsonlExporter`、`Tracer`、`SpanBatcher`、`get_tracer`
`runtime/core/telemetry/trace_context.py` | W3C TraceContext | `TraceContext`、`TraceContextPropagator`、`get_current_context`、`set_current_context`、`trace_context_from_headers`
`runtime/core/exceptions.py` | 统一异常体系 | `SymphonyException` 及各分层子类（LLM/Config/Agent/Session/Stream/Tool/Context/Observability/Ontology）
`runtime/core/utils.py` | 通用工具函数 | `generate_session_id`、`atomic_write`、`serialize_tool_calls`、`measure_elapsed_ms`、`duration_seconds`、`safe_json_load`、`parse_tool_arguments`、`truncate_text`

## 功能清单

### 1. 配置：Config / ConfigLoader / ConfigWatch（`config/`）

- 是什么：Pydantic v2 分层配置模型 + 文件/环境变量加载 + 文件监听热更新。
- 解决什么：几十个开关/参数的组织、默认值收敛、无重启调参。
- 关键 API：
  - `Config()` 顶层 facade，字段即子配置：`config.llm.default_model`、`config.history.context_window`、`config.trace.enabled`…；旧扁平名代理读写：`config.temperature` ↔ `llm.temperature`、`config.debug` ↔ `system.debug`、`config.min_retain_rounds` ↔ `history.min_retain_rounds` 等（映射表在 `_LEGACY_FIELD_MAP`）。
  - 类方法：`Config.development()`（开 trace/skills/session + DEBUG）、`Config.production()`（全部 opt-in 保持默认）、`Config.from_env(env_prefix="")`、`Config.from_file(path)`；实例方法 `to_dict()`、`sanitized_dict()`（语义同 `ConfigLoader.sanitize`：把顶层敏感键值替换为 `***`，供日志/审计展示）。
  - `ConfigLoader.from_env(env_prefix="")`：读取 `env_prefix + 大写配置键` 的环境变量（键集见 `_known_config_keys`，如 `default_model` 在 `env_prefix="SYMPHONY_"` 时对应 `SYMPHONY_DEFAULT_MODEL`），返回扁平 dict；`ConfigLoader.load` 再与文件/显式参数合并后交给 `Config`。
  - `ConfigLoader.from_file(path)`（用 `safe_json_load`，不存在/坏 JSON 返回 `{}`）、`ConfigLoader.load(config_cls, file_path=None, env_prefix="", **overrides)`（优先级：显式 > 文件 > 环境变量 > 默认）、`ConfigLoader.sanitize(dict)`。
  - `ConfigWatch(config_cls, file_path, poll_interval=5.0, debounce_seconds=0.1)`：`.start()/.stop()/.check_once()/.reload()/.on_change(cb)`；变更时通知本地监听器并触发全局 `notify_config_change`。模块级 `start_global_hot_reload(config_cls, file_path, poll_interval=5.0)` / `stop_global_hot_reload()` 管理单例 watcher，并自动挂到 `Components.notify_config_change`。
- 行为与边界：默认敏感键集合 `SENSITIVE_KEYS`（api_key/secret/token/password/mcp_headers…）；`Config` 无匹配字段时 `__getattr__` 抛 `AttributeError`。

### 2. 消息：Message / SessionStore（`message/`）

- 是什么：一条对话消息的最小模型，与 OpenAI 消息语义对齐，角色由 `MessageRole = Literal["user","assistant","system","tool","summary"]` 限定。
- 解决什么：历史、序列化、上下文构建里的统一消息载体。
- 关键 API：`Message(content, role, **kwargs)`（`timestamp` 默认 `datetime.now()`，`metadata` 默认 `{}`）、`.to_dict()`（含 isoformat 时间戳）、`Message.from_dict(data)`、`.to_text()`、`__str__`。`session.SessionStore(session_dir="memory/sessions")`：`.save(agent_config, history, tool_schema_hash, read_cache, metadata, session_name=None) -> str`（JSON 原子写）、`.load(filepath)`、`.list_sessions()`、`.delete(session_name)`、`.check_config_consistency(...)`、`.check_tool_schema_consistency(...)`。
- 行为与边界：`summary` 角色表示压缩摘要，供上下文组件消费；`SessionStore` 是兼容层（SessionCapability 在 `config.session.enabled` 时装配到 `Agent.session_store`）。

### 3. LLM：SymphonyLLM 与适配器（`llm/`）

- 是什么：统一多供应商 LLM 客户端。`SymphonyLLM(model=None, api_key=None, base_url=None, temperature=0.7, max_tokens=None, timeout=None, max_retries=3, retry_base_delay=1.0, quota_manager=None, usage_recorder=None, **kwargs)`；参数缺省时回落环境变量 `LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL / LLM_TIMEOUT`；三者最终都为空会抛 `SymphonyException`。
- 解决什么：供应商切换、thinking model、重试、配额计费钩子、调用观测的统一封装。
- 关键 API（方法均真实存在）：
  - `invoke(messages, **kwargs) -> LLMResponse`：同步非流式，内部套 `tracer.span("llm.invoke")` + `retry_manager.execute` + 指标埋点。
  - `invoke_with_tools(messages, tools, tool_choice="auto", **kwargs) -> Any`：Function Calling，返回**原生 SDK 响应**（含 `choices[0].message.tool_calls`），`tool_choice` 支持 `"auto"/"none"/"required"/{"type":"function","function":{"name":...}}`。
  - `think(messages, temperature=None) -> Iterator[str]` 与 `stream_invoke(...)`：流式别名；结束后经 `llm.last_call_stats`（`LLMResponse`）拿 usage/耗时。
  - 异步：`ainvoke`、`ainvoke_with_tools`（线程池包装同步）、`astream_invoke`（真异步流，走 adapter 异步实现）。
  - `quota_manager/usage_recorder`：在存在租户上下文时做配额扣减与用量记录（`governance.tenancy`，无租户不计数）。
  - `_register_config_callback()`：热更时自动更新 temperature/max_tokens/timeout/retry。
- 适配器：`BaseLLMAdapter`（抽象，`invoke/stream_invoke/invoke_with_tools` + 可选 `astream_invoke` 队列包装）、`OpenAIAdapter`（默认，含 thinking model 推理提取、真异步客户端）、`AnthropicAdapter`（system 独立参数、`messages.stream` 流式）、`GeminiAdapter`（roles 转换 user/model、`GenerativeModel`）。`create_adapter(api_key, base_url, timeout, model)` 按 URL 关键字路由。
- 响应与 Schema：`LLMResponse(model, content, usage, latency_ms, reasoning_content)`（`usage` 为 dict）；`LLMCache(max_size=256, ttl_seconds=300)`（SHA-256 键 + LRU + TTL + `stats()`）；`classify_llm_error(error) -> LLMErrorType`；`NormalizedToolCall/NormalizedTool/NormalizedMessage` 提供 OpenAI/Anthropic/Gemini 互转（`to_*`/`from_any`）。
- 流式：`StreamEvent(type/timestamp/agent_name/data)`、`StreamEventType`（含 `AGENT_START/AGENT_FINISH/STEP_START/STEP_FINISH/TOOL_CALL_START/TOOL_CALL_FINISH/LLM_CHUNK/THINKING/ERROR`）、`StreamBuffer`（背压丢弃旧事件）、`stream_to_sse`/`stream_to_json`。
- 防护（`llm/guard.py`，未在 core 顶层 re-export）：`PromptSanitizer` 提供注入模式检测与清洗、`ThreatLevel` 分级、`SanitizeResult`。

### 4. Agent 基类与生命周期（`agent/`）

- 是什么：所有 Agent 范式的抽象基类 + 异步生命周期事件系统。基类不实现具体循环，只负责装配与通用能力。
- 解决什么：范式差异之外的 80% 共性——历史/Token/截断装配、工具 schema 与执行、会话持久化、子代理隔离、checkpoint/HITL、并发。
- 关键 API：
  - `Agent(name, llm, system_prompt=None, config=None, tool_registry=None)`（抽象，`run` 必须由子类实现）。构造即：装配 `history_manager/truncator/token_counter`，可选建 GSSC `context_builder`；实例化 `CapabilityContext` 并 `default_capabilities().install_all(ctx)`；随后把 `trace_logger/skill_loader/ontology_engine/session_store/memory_manager/checkpoint_store/thread_manager/snapshot_worker/context_builder` 从 capability 状态回填到 `self`。
  - 历史：`add_message(Message)`（追加 + 增量 Token + 超阈自动压缩 + 可选 auto_save）、`clear_history()`、`get_history()`、属性 `_history`（property 代理到 HistoryManager）；摘要：`_generate_simple_summary` / `_generate_smart_summary`（`enable_smart_compression` 时用轻量 LLM）。
  - 工具公共方法：`_build_tool_schemas(tool_registry=None)`（classmethod，兼容无实例调用）、`_map_parameter_type`（staticmethod）、`_convert_parameter_types`、`_execute_tool_call(tool_name, arguments) -> str`、`_execute_single_tool_call(...) -> Dict`（执行+截断+trace+组装 tool 消息，统一 6 处逻辑）。
  - 会话：`save_session(session_name) -> str`、`load_session(filepath, check_consistency=True)`、`list_sessions()`（无 session_store 时抛 `RuntimeError`）；一致性检查基于配置与工具 schema hash。
  - 子代理：`run_as_subagent(task, tool_filter=None, return_summary=True, max_steps_override=None) -> Dict`（上下文隔离 + 工具过滤 + 摘要返回 + finally 状态恢复；`_apply_tool_filter` 已 deprecated，推荐 `capability.tools.registry.temporary_tool_filter` contextvars 方案）；`_create_light_llm()`。
  - checkpoint/HITL：`async _save_checkpoint(thread_id, state, step=None, metadata=None)`（checkpoint + WAL 双写、ontology WAL 桥接）、`async resume(thread_id, checkpoint_id=None)`、`interrupt(reason, payload=None)`（写 `Interrupt` 后抛 `InterruptPending(token,...)`）、`async resume_with(token, response)`。
  - 并发：`get_subagent_semaphore()`、`async run_subagents_concurrently(tasks)`（信号量限流、保序）、`get_concurrency_info()`。
  - 生命周期事件：`EventType`（AGENT_START/AGENT_FINISH/AGENT_ERROR/STEP_START/STEP_FINISH/LLM_START/LLM_CHUNK/LLM_FINISH/TOOL_CALL/TOOL_RESULT/TOOL_ERROR/THINKING/REFLECTION/PLAN）、`AgentEvent.create(event_type, agent_name, **data)`、`.to_dict()`；`LifecycleHook = Optional[Callable[[AgentEvent], Awaitable[None]]]`；`ExecutionContext`（步骤/Token/元数据）。基类 `arun` 在线程池跑 `run` 并触发钩子；`arun_stream` 默认产出 start/finish/error 事件，子类可覆盖为真流式。
- 行为与边界：`system_prompt` 是 property（记忆自动 recall 前缀由 `_memory_inject_prefix` 拼在最前）；`arun` 在 `config.memory_auto_recall` 且配置了 memory 时自动召回注入，`config.memory_auto_summarize` 时自动总结入库（默认均关）。

### 5. 可靠性：重试与限流（`reliability/`）

- 是什么：两个互相独立的可靠性原语组。
- 解决什么：LLM/外部 API 抖动（重试）、突发流量与配额（限流）。
- 关键 API：
  - `retry_with_backoff(max_retries=3, base_delay=1.0, backoff_factor=2.0, retryable_exceptions=None)`：装饰器；`retryable_exceptions` 默认 `(SymphonyException,)`；`delay = base * factor^attempt`。
  - `RetryManager(max_retries=3, base_delay=1.0, backoff_factor=2.0)`：`.execute(func, *args, **kwargs)`（重试捕获异常并 sleep，最终抛 `SymphonyException` 包装）、`.reset()`、`.retry_count` 统计。
  - `TokenBucket(rate, capacity)`：`.try_acquire(tokens=1.0) -> bool`、`.wait(tokens=1.0, timeout=None) -> bool`（阻塞带超时，线程安全）。
  - `SlidingWindow(max_requests, window_seconds)`：`.try_acquire() -> bool`（惰性清理过期请求）。
  - `RateLimiter(default_limit=100, window_seconds=60.0)`：按 key 维护窗口，`.try_acquire(key) -> bool`、`.set_limit(key, limit)`、`.reset(key=None)`；构造时注册热更回调（随 `system.max_concurrent_tools` 变化）。
- 行为与边界：重试装饰器里 `max_retries=0` 表示不重试；所有限流器均线程安全（`threading.Lock`），可直接跨线程使用。

### 6. 观测：日志 / 指标 / 健康 / 监控 / 追踪（`telemetry/`）

- 是什么：统一观测四件套。日志可结构化 JSON；指标走 Prometheus 文本；健康检查可聚合；监控是零外部依赖的 HTTP 端点；追踪是轻量自实现（对齐 OpenTelemetry 语义，无外部 SDK）。
- 解决什么：调用可查、过程可看、组件可体检、指标可抓。
- 关键 API：
  - 日志：`setup_logging(level="INFO", json_format=False, log_file=None, max_bytes=10MB, backup_count=3)`（每次调用会先清空既有 handler）；`get_logger(name)` 返回 `agentorchestra.<name>` logger；`log_event(logger, event, **fields)`。`JsonFormatter` 额外吸收 `session_id/agent_name/step/event/tool_name/model/duration_ms/error_code` 等 extra 字段。
  - 指标：`MetricsCollector(enabled=True)`，暴露 `symphony_llm_calls_total/symphony_llm_tokens_total/symphony_llm_latency_ms/symphony_tool_calls_total/symphony_tool_errors_total/symphony_action_executions_total/symphony_active_requests`；`record_llm_call(model, provider, tokens, latency_ms)`、`record_tool_call(name, error)`、`record_action_execution(...)`、`request_start/request_end`、`generate_latest() -> str`；`get_metrics()` 返回全局单例。`prometheus_client` 未装时全部 no-op（`is_available=False`）。
  - 健康：`HealthCheck(name)`：`.register(check_fn)`（返回 `{"name","status","detail"}`）、`.check() -> {"name","status","checks"}`（`ok/degraded`）、`.register_basic()`、`.register_config_check(config)`、`.register_store_check(store)`。
  - 监控：`MonitorServer(host="0.0.0.0", port=9090, health_check=None, metrics_provider=None, traces_provider=None)`，`.start()/.stop()/.is_running`；端点 `GET /metrics`（Prometheus 文本）、`/health`、`/traces`、`/`（服务信息）。
  - 追踪：`Tracer(exporter=None, batch_max_size=50, batch_window_ms=1000.0)`：`span(name, attributes)` 上下文管理器、`start_span/end_span`、`current_span()/current_trace_id()`、`export_all()`、`clear()`、`flush()`；`Span`（`.set_attribute/.add_event/.set_error/.end/.to_dict`）；`MemoryExporter`（`.read_all()/.clear()`）、`JsonlExporter(filepath="memory/traces/spans.jsonl")`（线程安全追加 + `.read_all()/.clear()`）；`SpanBatcher` 按 size/时间窗口批量导出；`get_tracer(exporter=None)` 全局单例（首次调用固定 exporter）。默认 exporter 为 `MemoryExporter()`。
  - W3C：`TraceContext`（`.to_traceparent()/.to_headers()/.child_context()`、`from_traceparent(cls,...)`），`TraceContextPropagator`（dict/Kafka headers），`trace_context_from_headers(headers)`。

### 7. 异常与工具（`exceptions.py` / `utils.py`）

- 异常：`SymphonyException(message="", error_code=None)`（`.to_dict()`，`error_code` 默认 `SYMPHONY_ERROR`）；子类分三层 `LLMException/LLMTimeoutException/LLMRateLimitException`、`ConfigException`、`AgentException`、`SessionException`、`StreamException`、`ToolException/ToolNotFoundException/ToolExecutionException`、`ContextException/TokenLimitExceededException`、`ObservabilityException`、`OntologyException/ObjectValidationException/ObjectNotFoundException/PermissionDeniedException/ActionExecutionException`。
- 工具函数：`generate_session_id(suffix_len=4)`、`atomic_write(filepath, data, pretty=False)`（临时文件 + `os.replace`）、`serialize_tool_calls(tool_calls)`、`measure_elapsed_ms(start_time)`、`duration_seconds(start, end=None)`、`safe_json_load(filepath, default=None)`、`parse_tool_arguments(tool_call)`（抛 `JSONDecodeError` 由调用方处理）、`truncate_text(text, max_len, ellipsis=True)`。

## 使用说明

导入（经典名 = 兼容别名，与规范名同一模块对象）：

```python
# 经典扁平名
from agentorchestra.core import (
    Config, SymphonyLLM, Message, ConfigLoader, ConfigWatch,
    HealthCheck, MonitorServer, SymphonyException, LLMResponse, StreamStats,
    setup_logging, get_logger, MetricsCollector, get_metrics,
    Tracer, Span, MemoryExporter, JsonlExporter, get_tracer,
    RetryManager, retry_with_backoff, TokenBucket, SlidingWindow, RateLimiter,
)
# 规范路径
from agentorchestra.runtime.core import Config, SymphonyLLM, Message
from agentorchestra.runtime.core.llm.streaming import StreamEvent, StreamEventType, stream_to_sse
from agentorchestra.runtime.core.agent.lifecycle import AgentEvent, EventType, LifecycleHook
from agentorchestra.runtime.core.telemetry.trace_context import TraceContext, TraceContextPropagator
# 经典深层模块别名（_LEGACY_CORE）：agentorchestra.core.hot_config / llm_response / retry / ...
from agentorchestra.core.llm_response import LLMResponse
```

分场景示例：

```python
# 1) 配置：子配置 + 旧扁平名代理 + 预设
from agentorchestra.core import Config, ConfigLoader

config = Config()
config.llm.temperature = 0.5                 # 新写法（子配置）
config.temperature = 0.4                      # 旧扁平名，等价写入 llm.temperature
print(config.llm.temperature, config.debug)   # 0.4 False
dev = Config.development()                    # trace/skills/session 打开 + DEBUG
print(dev.trace.enabled, dev.session.enabled, dev.system.debug)  # True True True

# 结构化 JSON 加载（子配置名为键）
import json, tempfile, os
path = os.path.join(tempfile.mkdtemp(), "cfg.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump({"llm": {"temperature": 0.1}, "system": {"debug": True}}, f)
cfg = ConfigLoader.load(Config, file_path=path)          # 优先级：显式 > 文件 > 环境变量 > 默认
print(cfg.llm.temperature, cfg.debug)                    # 0.1 True
print(ConfigLoader.sanitize({"api_key": "sk-xxx", "model": "x"}))  # {'api_key': '***', ...}
print(ConfigLoader.from_file("不存在的文件.json"))          # {}

# 2) 消息
from agentorchestra.core import Message
m = Message("你好", "user")
m2 = Message.from_dict(m.to_dict())
print(m2.role, m2.content, m.to_text())       # user 你好 [user] 你好

# 3) 重试 + 限流（纯本地，可运行）
import time
from agentorchestra.core import retry_with_backoff, RetryManager, TokenBucket, RateLimiter

calls = {"n": 0}
@retry_with_backoff(max_retries=3, base_delay=0.01)
def flaky():
    calls["n"] += 1
    if calls["n"] < 2:
        raise SymphonyException("再试一次")
    return "ok"
print(flaky())                                # 第一次失败自动重试后 ok

bucket = TokenBucket(rate=10, capacity=5)
print(bucket.try_acquire(3), bucket.try_acquire(3))   # True False
limiter = RateLimiter(default_limit=3, window_seconds=60)
print([limiter.try_acquire("u1") for _ in range(4)])  # [True, True, True, False]

# 4) 追踪（内存导出）+ 指标
from agentorchestra.core import get_tracer, MemoryExporter, get_metrics
exporter = MemoryExporter()
tracer = get_tracer(exporter)
with tracer.span("demo.op", {"step": 1}) as sp:
    sp.set_attribute("ok", True)
tracer.flush()
print(len(exporter.read_all()), tracer.current_span())  # 1 None
metrics = get_metrics()
metrics.record_llm_call("fake-model", "local", 10, 3.2)   # prometheus_client 未装则 no-op

# 5) 结构化日志 + 健康检查 + 监控端点
from agentorchestra.core import setup_logging, get_logger, HealthCheck, MonitorServer
setup_logging(level="INFO", json_format=False)
logger = get_logger("demo")
logger.info("hello", extra={"event": "demo"})            # 控制台可见

hc = HealthCheck("svc")
hc.register_basic()
hc.register_config_check(Config())
server = MonitorServer(host="127.0.0.1", port=0, health_check=hc,
                       metrics_provider=metrics.generate_latest,
                       traces_provider=tracer.export_all)
server.start()
print(hc.check()["status"], server.is_running)           # ok True
server.stop()
```

LLM 真实调用示例（需网络与凭证，仅演示调用约定；离线开发请用 docs/agents 的 Fake LLM 替身）：

```python
from agentorchestra.core import SymphonyLLM
# 优先级：构造参数 > 环境变量 LLM_MODEL_ID / LLM_API_KEY / LLM_BASE_URL
llm = SymphonyLLM(model="gpt-4o-mini", api_key="...", base_url="https://api.openai.com/v1")
resp = llm.invoke([{"role": "user", "content": "一句话介绍自己"}])
print(resp.content, resp.usage, resp.latency_ms)
# Function Calling：tools 为 OpenAI 风格 JSON Schema 列表
raw = llm.invoke_with_tools([{"role": "user", "content": "天气如何"}],
                            tools=[{"type": "function", "function": {"name": "get_weather", "parameters": {...}}}],
                            tool_choice="auto")
print(raw.choices[0].message.tool_calls)
# 流式：think 与 stream_invoke 等价；结束后取 last_call_stats
for chunk in llm.stream_invoke([{"role": "user", "content": "hi"}], temperature=0.2):
    print(chunk, end="")
print(llm.last_call_stats)
```

Config 常用字段速查（新/旧字段名均可读）：

| 子配置 | 关键字段（含旧扁平名） | 默认 |
| --- | --- | --- |
| `llm` | `default_model`/`default_provider`/`temperature`/`max_tokens`/`max_retries`/`retry_base_delay`/`timeout` | `gpt-3.5-turbo`/`openai`/`0.7`/…/`3`/`1.0`/`60` |
| `system` | `debug`/`log_level`/`max_concurrent_tools`/`max_concurrent_subagents`/`hook_timeout_seconds` | `False`/`WARNING`/`3`/`2`/`5.0` |
| `history` | `max_history_length`/`context_window`/`compression_threshold`/`min_retain_rounds` | `100`/`128000`/`0.8`/`10` |
| `smart_compression` | `enabled`/`summary_llm_provider`/`summary_llm_model`/`summary_max_tokens`/`summary_temperature` | `False`/…（默认走 `deepseek-chat`） |
| `context_builder` | `enabled`/`max_tokens` | `False`/`8000` |
| `trace` | `enabled`/`output_dir`/`sanitize` | `False`/`memory/traces`/`True` |
| `tool_output` | `max_lines`/`max_bytes`/`truncate_direction`/`output_dir` | `2000`/`51200`/`head`/`tool-output` |
| `circuit_breaker` | `enabled`/`failure_threshold`/`recovery_timeout` | `True`/`3`/`300` |
| `session` | `enabled`/`dir`/`auto_save_enabled`/`auto_save_interval` | `False`/`memory/sessions`/`False`/`10` |
| `state_checkpoint` | `enabled`/`persistence_mode`/`db_url`/`wal_snapshot_enabled` | `False`/`sqlite`/`""`/`False` |
| `memory` | `enabled`/`backend`/`namespace`/`auto_recall`/`auto_summarize`/`recall_top_k`/`embedding_enabled` | `False`/`sqlite`/`default`/`True`/`False`/`5`/`True` |
| `skills/mcp/ontology/subagent/todowrite/devlog` | `enabled` 等（opt-in 组） | 全 `False` |

注意事项：
- `SymphonyLLM` 的 `model/api_key/base_url` 三者缺失即构造失败；构造参数为空时会去读环境变量。
- `setup_logging` 每次调用会替换 `agentorchestra` logger 的所有 handler；重复调用会重配而非叠加。
- 指标仅在安装 `prometheus_client` 时真正收集（可选依赖），否则所有 `record_*` 静默 no-op。
- `get_tracer()` 是进程级单例：首次调用决定 exporter，之后再传 exporter 不会更换。
- `MonitorServer` 默认绑定 `0.0.0.0:9090`，示例中请用 `port=0` 随机端口或自行分配，避免占用冲突。

## 与其他模块的关系

- 依赖：`runtime.core` 本身尽量只依赖第三方库与同包模块。`telemetry.tracing` 内部 import `telemetry.logging`；`llm` 依赖 `exceptions`/`utils`/`reliability.retry`/`telemetry.*`；`Agent` 基类运行期依赖 `runtime.context.*`（历史/截断/Token，延迟导入避免环）、`runtime.capabilities`（CapabilityContext + default_capabilities）与 `orchestration.state`（checkpoint/WAL/Interrupt，局部导入）；`SymphonyLLM` 可选依赖 `governance.tenancy`（配额/用量）。
- 被依赖：
  - `runtime.agents.*` 全量基于 `core.agent.Agent`、`core.llm.SymphonyLLM`、`core.llm.streaming.StreamEvent`、`core.message.Message`、`core.config.Config`。
  - `runtime.context.*` 依赖 `core.message.Message` 与 `core.utils`。
  - `capability.tools.registry`（工具执行）依赖 `core.utils`/`core.telemetry`（追踪 span、指标），并在执行后回写熔断记录。
  - 根包 `agentorchestra/__init__.py` re-export `SymphonyLLM/Config/Message/SymphonyException`；`agentorchestra.components.Components` 引用 `core.telemetry.tracing.get_tracer` 与 `core.config.hot.ConfigWatch`。
  - 经典导入兼容：`agentorchestra.core.*` 及 `core.hot_config/config_loader/llm_adapters/llm_response/llm_schema/streaming/prompt_guard/session_store/retry/ratelimit/logging/metrics/monitor/health/tracing/trace_context/lifecycle` 等深层别名经 `_legacy.py._LEGACY_CORE` 映射。
- 环说明：`runtime.core.agent.base` 对 `runtime.context` / `runtime.capabilities` / `orchestration.state` 均采用方法体内延迟导入，因此不存在顶层循环 import。

## 测试

```bash
python -m pytest tests/unit/test_core.py -v         # Config 预设/子配置 + Message + SymphonyException
python -m pytest tests/unit -v                      # 全部单元测试
python -m pytest tests/integration -m integration   # 集成用例（若需联网/真实依赖）
python examples/agent_full_demo.py                  # 端到端演示（含 Runtime Core 基础设施段）
```

`tests/unit/test_core.py` 目前覆盖 Config/Message/异常；Reliability/Telemetry 组件暂无独立单测文件，可参照 `tests/unit/test_observability.py` 的结构补充。
