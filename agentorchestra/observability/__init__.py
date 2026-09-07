"""可观测性模块 — 本地轨迹记录 + 指标/trace 导出（M5，零依赖）。

公共 API：
- TraceLogger：双格式（JSONL + HTML）执行轨迹记录（开发态默认）
- metrics：PrometheusTextCollector / NoOpCollector / enable_prometheus_collector() / SLO 常量
- prometheus：零依赖 Prometheus 文本渲染器（Counter/Gauge/Histogram）
- otel_exporter：OTLPHttpJsonExporter（轻量 Span → Jaeger/Tempo，默认关）
- slo：业务 SLO 指标定义
"""

from .metrics import (
    SLO_AGENT_RECALL_HIT_RATE,
    SLO_TX_COMPENSATION_TRIGGERED,
    SLO_TX_DURATION_SECONDS,
    SLO_TX_ROLLBACK_RATE,
    MetricsCollector,
    NoOpCollector,
    PrometheusTextCollector,
    enable_prometheus_collector,
    get_default_collector,
    reset_default_collector,
    set_default_collector,
)
from .otel_exporter import OTLPHttpJsonExporter, get_default_exporter, set_default_exporter
from .prometheus import Counter, Gauge, Histogram
from .slo import SLO_DEFINITIONS, SLODefinition, SLOType
from .trace_logger import TraceLogger

__all__ = [
    # 本地轨迹
    "TraceLogger",
    # 指标抽象
    "MetricsCollector",
    "NoOpCollector",
    "PrometheusTextCollector",
    "get_default_collector",
    "set_default_collector",
    "reset_default_collector",
    "enable_prometheus_collector",
    # Prometheus 渲染器
    "Counter",
    "Gauge",
    "Histogram",
    # OTLP trace
    "OTLPHttpJsonExporter",
    "get_default_exporter",
    "set_default_exporter",
    # SLO 定义与常量
    "SLO_DEFINITIONS",
    "SLODefinition",
    "SLOType",
    "SLO_TX_ROLLBACK_RATE",
    "SLO_TX_DURATION_SECONDS",
    "SLO_TX_COMPENSATION_TRIGGERED",
    "SLO_AGENT_RECALL_HIT_RATE",
]
