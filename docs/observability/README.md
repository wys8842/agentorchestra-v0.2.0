# Observability 模块

## 概述

Observability 模块提供可观测能力：Trace/Metrics/Logging。

## 组件

### TraceLogger

追踪日志：

```python
from agentorchestration.observability import TraceLogger

logger = TraceLogger(output_dir="traces")

# 记录事件
logger.log_event("event_name", {"key": "value"})

# 追踪
with logger.trace("operation"):
    # 操作
    pass

# 最终化
logger.finalize()
```

### Metrics

指标收集：

```python
from agentorchestration.observability import MetricsCollector, enable_prometheus_collector

# 默认收集器
collector = MetricsCollector()

# Prometheus
collector = enable_prometheus()

# 记录指标
collector.increment("requests_total")
collector.gauge("memory_usage", 1024)
collector.observe("request_duration", 0.5)
```

### Prometheus

Prometheus 导出：

```python
from agentorchestration.observability import PrometheusExporter

exporter = PrometheusExporter()
exporter.start(port=9090)
```

### OTLP

OTLP 导出：

```python
from agentorchestration.observability import OTLPHttpJsonExporter

exporter = OTLPHttpJsonExporter(
    endpoint="http://localhost:4318"
)
exporter.enable()
```

## 事件类型

| 事件 | 说明 |
|------|------|
| session_start | 会话开始 |
| session_end | 会会结束 |
| tool_call | 工具调用 |
| tool_result | 工具结果 |
| model_output | 模型输出 |
| error | 错误 |
