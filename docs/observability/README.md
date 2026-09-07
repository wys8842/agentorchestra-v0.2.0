# Observability 模块

> 零依赖的可观测性实现：本地双格式执行轨迹（JSONL+HTML）、Prometheus 文本指标收集与渲染、OTLP HTTP/JSON trace 导出（可选、默认关）、业务 SLO 指标定义。包路径即 `agentorchestra.observability.*`。

## 设计动机与原则

1. **开发态与生产态分离**：本地排查用 `TraceLogger` 落地 `trace-<session>.jsonl`（可 jq）与 `trace-<session>.html`（浏览器可视化）；对接监控后端走 Prometheus 文本 / OTLP exporter，两条路互不干扰。
2. **零第三方依赖**：Prometheus text exposition 渲染（`Counter/Gauge/Histogram`）与 OTLP HTTP JSON 发送都用纯标准库实现（`urllib`、`http.server`），不强制安装 `prometheus_client`/`opentelemetry-*`。
3. **默认不打扰**：默认指标收集器是 `NoOpCollector`（埋点调用零开销丢弃）；OTLP exporter 默认 `enabled=False`，显式 `enable()` 才发数据——避免误发到并不存在的后端。
4. **协议对齐、便于替换**：接口命名对齐 Prometheus/OpenTelemetry 概念（`increment/observe/gauge`、`render()`、`SpanExporter`），未来需要重量级 SDK 时可平滑替换。
5. **业务指标先行（SLO）**：`slo.py` 把事务回滚率/时长/补偿触发/Agent 召回命中率定义为数据类 + 指标名常量，业务埋点与监控查询共用同一套命名，减少"埋点名漂移"。
6. **与运行时 telemetry 各司其职**：`runtime/core/telemetry/` 负责 Agent 进程内运行时的结构化日志、Span/Tracer、HTTP 监控端点；`observability/` 提供可装配的收集器/渲染器/exporter 与面向会话的执行轨迹记录器，并通过装配门面 `Components` 与默认收集器回退打通两者。

## 设计优势

- 本地调试 Agent 时打开 HTML 轨迹即可看到每步事件、Token、工具调用与错误统计，不用部署任何中间件。
- 指标暴露只需 `get_default_collector().render()`，可被 `MonitorServer` 的 `/metrics` 或任何 HTTP 框架消费。
- 想接入 Jaeger/Tempo：一个 `OTLPHttpJsonExporter().enable()` 即可，无需改动埋点代码。
- 测试友好：`reset_default_collector()` 还原 NoOp，`TraceLogger` 可写临时目录。

## 模块构成

| 路径 / 子模块 | 职责 | 主要公开导出 |
|---|---|---|
| `observability/__init__.py` | 聚合导出（精简公共面） | 见下方 `使用说明`，含 `TraceLogger`、指标收集器函数、`Counter/Gauge/Histogram`、OTLP exporter 函数、SLO 定义与常量 |
| `observability/trace_logger.py` | 双格式（JSONL+HTML）执行轨迹 | `TraceLogger` |
| `observability/metrics.py` | 指标收集器抽象 + Prometheus 文本收集器 | `MetricsCollector`、`NoOpCollector`、`PrometheusTextCollector`、`get_default_collector`、`set_default_collector`、`enable_prometheus_collector`、`reset_default_collector`，以及 SLO 指标名常量 `SLO_TX_ROLLBACK_RATE`/`SLO_TX_DURATION_SECONDS`/`SLO_TX_COMPENSATION_TRIGGERED`/`SLO_AGENT_RECALL_HIT_RATE` |
| `observability/prometheus.py` | Prometheus 文本渲染器（零依赖） | `Counter`、`Gauge`、`Histogram` |
| `observability/otel_exporter.py` | Span → OTLP/HTTP JSON 导出（默认关） | `OTLPHttpJsonExporter`、`get_default_exporter`、`set_default_exporter` |
| `observability/slo.py` | 业务 SLO 指标定义 | `SLOType`、`SLODefinition`、`SLO_DEFINITIONS` |

## 功能清单

### 1. TraceLogger（trace_logger.py）——双格式执行轨迹

构造：`TraceLogger(output_dir="memory/traces", sanitize=True, html_include_raw_response=False)`。

- 生成会话内唯一 `session_id`，同时打开两个文件：
  - `trace-<session_id>.jsonl`：事件流式追加（每行一个事件 JSON，可 jq 分析）；
  - `trace-<session_id>.html`：增量写入可视片段 + 统计面板（总步数/总 Token/总成本/时长/模型调用次数/工具调用统计表/错误列表）。
- `log_event(event, payload, step=None)`：线程安全（RLock）；事件含 `ts/session_id/step/event/payload` 与单调递增 `event_id`。
- 脱敏（`sanitize=True` 时递归生效）：`sk-xxxx` → `sk-***`、`Bearer xxx` → `Bearer ***`、`/Users/`、`/home/`、`C:\Users\` 下的用户名路径打码。
- 内存保护：事件缓存为 `deque(maxlen=50000)`（防长会话 OOM；溢出仅告警一次，JSONL 仍完整）；JSONL 单文件超 50MB 自动 rotate（`trace-<id>.<n>.jsonl`）。
- `finalize()`：计算统计、写入 HTML 尾部并关闭文件；此后 `log_event` 静默丢弃。也支持 `with TraceLogger(...) as t` 上下文（异常时自动记 `error` 事件并在退出时 finalize）。
- Agent 集成：各 Agent 运行时持有 `self.trace_logger` 并记录 `session_start/tool_call/tool_result/model_output/error/session_end` 等事件（见 `runtime/agents/*` 与 `runtime/core/agent/base.py`）。

### 2. 指标收集器抽象与默认装配（metrics.py）

- `MetricsCollector(ABC)`：三个抽象方法 `increment(name, value=1.0, labels=None)`、`observe(name, value, labels=None)`、`gauge(name, value, labels=None)`，附默认空 `render()`。
- `NoOpCollector`：全丢弃（默认值，保证未启用时零影响）。
- `PrometheusTextCollector`：纯内存 + `render()` 生成 Prometheus 文本。`increment`→counter、`observe`→histogram（预置桶 `[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]`）、`gauge`→gauge；`describe(name, help_text)` 注册 HELP。
- 全局装配函数：
  - `get_default_collector()`：懒加载，未启用时返回 NoOp 单例；
  - `set_default_collector(c)` / `reset_default_collector()`：替换 / 还原（测试用）；
  - `enable_prometheus_collector()`：把默认收集器设为 `PrometheusTextCollector`（幂等返回单例）。

### 3. Prometheus 文本渲染器（prometheus.py）

`Counter`（`inc`/`get`）、`Gauge`（`set`/`inc`/`dec`）、`Histogram`（`observe`，构造可自定义 `buckets`）。每个实例持有 `family`（内部 `_MetricFamily`），`family.render()` 输出 `# HELP / # TYPE` 与样本行（含 histogram 的 `_bucket/_sum/_count`）。label 值做转义、键排序保证输出稳定。

### 4. OTLP HTTP/JSON exporter（otel_exporter.py）——默认关

`OTLPHttpJsonExporter(endpoint="http://localhost:4318", service_name="agentorchestra", timeout=5.0)`，是 `runtime.core.telemetry.tracing.SpanExporter` 的实现：

- 把轻量 `core.tracing.Span`（含嵌套 `parent_id`、attributes、带 wall-clock 的 events）桥接成 OTLP `resourceSpans/scopeSpans` JSON，POST 到 `<endpoint>/v1/traces`（Jaeger/Tempo 等 OTLP collector）。纯 `urllib.request`。
- 生命周期：`enable()` / `disable()`（链式）；`enabled=False` 时 `export` 直接丢弃。`sent` / `failed` 只读计数。
- `export(span)` 单条、`export_batch(spans)` 批量（单次 POST 多条 span）。
- 全局默认：`get_default_exporter()` 懒加载默认关闭的实例；`set_default_exporter(ex)` 替换。
- 接入方式（assembly 语义）：`agentorchestra.components.Components.enable_otel_trace(endpoint=..., service_name=...)` 会创建 exporter、`enable()` 并挂到全局 `Tracer`。

### 5. SLO 定义（slo.py）

`SLOType`（`COUNTER/HISTOGRAM/GAUGE`）与 `SLODefinition(name, slo_type, description, unit, labels)`（frozen dataclass）。`SLO_DEFINITIONS` 含四条业务指标：`tx_rollback_rate`（GAUGE，ratio）、`tx_duration_seconds`（HISTOGRAM）、`tx_compensation_triggered_total`（COUNTER）、`agent_recall_hit_rate`（GAUGE）。`metrics.py` 中的 `SLO_*` 常量与这里的 `name` 一一对应，是埋点与告警共用的规范命名。

### 6. 与 `runtime/core/telemetry` 的区别与配合

| | `runtime/core/telemetry/`（进程内运行时） | `observability/`（横切可装配层） |
|---|---|---|
| 结构化日志 | `logging.py`：`setup_logging`/`JsonFormatter`/`get_logger`/`log_event` | — |
| 追踪 | `tracing.py`：`Span`/`Tracer`/`SpanBatcher`/内存与 JSONL exporter/`get_tracer()` | `trace_logger.py`：会话级双格式记录；`otel_exporter.py`：把 `tracing.Span` 发到 OTLP |
| 指标 | `metrics.py`：框架级收集器（需可选 `prometheus_client`），工具/动作执行埋点 | `metrics.py`：抽象 + 纯内存 Prometheus 收集器（零依赖）；`monitor` 的 `/metrics` 未配置 provider 时回退到这里的默认收集器 |
| HTTP 端点 | `monitor.py`：`MonitorServer`（`/metrics /health /traces`） | —（只提供 `render()`/文本） |
| 健康 | `health.py`：`HealthCheck` | — |

**配合点**：工具注册表 `execute_tool` 走 `tracing.get_tracer().start_span("tool.<name>")` 与 `telemetry.metrics.get_metrics().record_tool_call`；`MonitorServer._get_metrics` 在未注入 `metrics_provider` 时 fallback 到 `observability.metrics.get_default_collector().render()`；OTLP exporter 消费的是 telemetry 的 Span。两边通过 `agentorchestra.components.Components`（`tracer()/metrics_collector()/otel_exporter()/enable_prometheus()/enable_otel_trace()`）做统一装配与替换。

## 使用说明

### import 路径

```python
# 本包即顶层包，直接导入
from agentorchestra.observability import (
    TraceLogger,
    MetricsCollector, NoOpCollector, PrometheusTextCollector,
    get_default_collector, set_default_collector, enable_prometheus_collector,
    reset_default_collector,
    Counter, Gauge, Histogram,
    OTLPHttpJsonExporter, get_default_exporter, set_default_exporter,
    SLO_DEFINITIONS, SLODefinition, SLOType,
    SLO_TX_ROLLBACK_RATE, SLO_TX_DURATION_SECONDS,
    SLO_TX_COMPENSATION_TRIGGERED, SLO_AGENT_RECALL_HIT_RATE,
)
# 经典顶层也始终等于此包
import agentorchestra.observability as obs
```

### 场景 1：记录一次会话轨迹（双格式）

```python
from agentorchestra.observability import TraceLogger

logger = TraceLogger(output_dir="memory/traces")   # sanitize 默认 True
logger.log_event("session_start", {"agent_name": "demo"})
logger.log_event("tool_call", {"tool_name": "Read"}, step=1)
logger.log_event("tool_result", {"tool_name": "Read"}, step=1)
logger.log_event("error", {"error_type": "ValueError", "message": "boom"}, step=2)
logger.log_event("session_end", {})
logger.finalize()
print(logger.jsonl_path)   # memory/traces/trace-<session>.jsonl
print(logger.html_path)    # memory/traces/trace-<session>.html
```

### 场景 2：Prometheus 指标收集与渲染

```python
from agentorchestra.observability import (
    enable_prometheus_collector, get_default_collector, reset_default_collector,
)

collector = enable_prometheus_collector()          # 替换默认为 Prometheus 文本收集器
collector.describe("tx_compensation_triggered_total", "补偿动作触发次数")
collector.increment("tx_compensation_triggered_total", 1, {"reason": "abort"})
collector.observe("tx_duration_seconds", 1.2, {"result": "committed"})
collector.gauge("agent_recall_hit_rate", 0.92)

text = get_default_collector().render()            # Prometheus text exposition
# /metrics 端点直接返回该文本即可
print(text)
reset_default_collector()                          # 测试/清理：还原 NoOp
```

也可绕过收集器直接用渲染器：

```python
from agentorchestra.observability import Counter, Histogram

c = Counter("demo_requests", "demo counter")
c.inc(2, {"method": "GET"})
c.inc(3, {"method": "GET"})
print(c.family.render())   # 含 # HELP/# TYPE 与样本行
```

### 场景 3：OTLP trace 导出（默认关，仅装配演示）

```python
from agentorchestra.observability import OTLPHttpJsonExporter

exporter = OTLPHttpJsonExporter(endpoint="http://localhost:4318", service_name="agentorchestra")
print(exporter.enabled)            # False，默认不发送
exporter.enable()                  # 显式开启后才 POST /v1/traces
exporter.disable()
```

> 注：需要真实发送时，请确保 endpoint 可达；否则 `export`/`export_batch` 只会把计数记入 `failed`。生产集成推荐 `Components.enable_otel_trace(endpoint, service_name)`。

### 场景 4：查看 SLO 定义

```python
from agentorchestra.observability import SLO_DEFINITIONS, SLOType

d = SLO_DEFINITIONS["tx_duration_seconds"]
print(d.name, d.slo_type.value, d.labels)   # tx_duration_seconds histogram ['tenant_id', 'result']
```

### 关键配置 / 常量

| 项目 | 默认值 | 说明 |
|---|---|---|
| `TraceLogger(output_dir=...)` | `memory/traces` | 输出目录（构造即建目录并开文件） |
| `TraceLogger(sanitize=...)` | `True` | 递归脱敏 api-key / Bearer / 用户名路径 |
| `TraceLogger(html_include_raw_response)` | `False` | HTML 是否含原始响应 |
| 事件缓存上限 / JSONL rotate | 50 000 条 / 50 MB | deque bounded + 自动 rotate |
| 默认指标收集器 | `NoOpCollector` | `enable_prometheus_collector()` 可切换 |
| `Histogram` 默认桶 | `[0.005..10]` | 同上默认集合 |
| `OTLPHttpJsonExporter` | `enabled=False`、endpoint `http://localhost:4318`、timeout 5s | 需 `enable()` |
| `MonitorServer` 端点 | `/metrics /health /traces` | 在 `runtime.core.telemetry.monitor` |
| Config trace 段 | `trace_enabled=False`、`trace_dir="memory/traces"`、`trace_sanitize=True` | `TraceCapability` 按此装配 Agent 自带 logger |

## 与其他模块的关系

- **runtime.core.utils**：`TraceLogger` 用 `generate_session_id()` 与 `duration_seconds()`。
- **runtime.core.telemetry.tracing**：`otel_exporter.OTLPHttpJsonExporter` 继承其 `SpanExporter`，消费 `Span`（含 `start/end_wall_ns`、`events[].wall_ns`）；`execute_tool` 的 `tool.*` span 由此导出。
- **runtime.core.telemetry.metrics / monitor**：工具/动作埋点由 telemetry 收集；monitor `/metrics` 无 provider 时回退本包默认收集器；`health.py` 供 `/health`。
- **runtime.agents 与 runtime.core.agent.base**：Agent 持有 `trace_logger`（由 `runtime.capabilities.builtins.TraceCapability` 按 `config.trace.*` 装配），并记录 `session_start/tool_call/tool_result/model_output/error/session_end` 事件。
- **agentorchestra.components**：`Components.metrics_collector()/otel_exporter()/tracer()/enable_prometheus()/enable_otel_trace()` 是本包的统一装配门面（回退到本包默认单例）。
- **capability.tools.registry**：工具执行是 telemetry 埋点的生产者（事件源），本身不直接 import 本包。

## 测试

```bash
# 单元测试（冒烟：验证模块可导入）
python -m pytest tests/unit/test_observability.py -v

# 全量
python -m pytest tests
```

`tests/unit/test_observability.py` 目前覆盖：`observability.metrics.MetricsCollector`、`observability.trace_logger.TraceLogger`、`runtime.core.telemetry.tracing.Span` 的存在性检查。将上方"场景 1/2"保存为脚本运行即可验证双格式落盘与 Prometheus 文本输出。
