"""components — 框架统一装配门面（组件化 / 可插拔）。

目标：让框架的横切组件（存储、LLM、追踪、指标、trace 导出、身份/租户上下文、
事务协调器、Inbox 存储）通过**一个入口**装配与替换，避免在各业务包散落全局单例。

设计要点：
- 全部懒加载：访问 `Components.xxx` 时才构建/查询，未显式注册则回退既有全局单例（向后兼容）。
- 可插拔：通过 `Components.register_<kind>(impl)` 覆盖默认实现；`reset()` 还原（测试用）。
- 无新增依赖：本模块只做聚合/委托，不复制各组件职责。

用法：
    from agentorchestra.components import Components

    # 读取（默认回退现有全局实现）
    store = Components.state_store()            # CheckpointStore（state.get_default_store）
    tracer = Components.tracer()                # Tracer（core.tracing.get_tracer）
    collector = Components.metrics_collector()  # observability.metrics.get_default_collector

    # 替换（可插拔）：注入自定义实现
    Components.register_state_store(my_store)
    Components.register_metrics_collector(my_collector)

    # 装配常用组合（幂等）
    Components.enable_prometheus()              # 开启 Prometheus 文本指标收集器
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional


class _Components:
    """装配注册表（模块级单例）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # 用户显式注册的实现（None = 未注册，回退默认）
        self._state_store: Optional[Callable[[], Any]] = None
        self._metrics_collector: Optional[Callable[[], Any]] = None
        self._otel_exporter: Optional[Callable[[], Any]] = None
        self._tracer: Optional[Callable[[], Any]] = None

    # ---------------- 存储 ----------------

    def register_state_store(self, factory: Callable[[], Any]) -> None:
        """注册 CheckpointStore 工厂（P0 持久化后端）。"""
        with self._lock:
            self._state_store = factory

    def state_store(self) -> Any:
        """获取持久化 store：显式注册优先；否则回退 state.get_default_store()。"""
        with self._lock:
            if self._state_store is not None:
                return self._state_store()
        from agentorchestra.orchestration.state import get_default_store
        return get_default_store()

    # ---------------- 可观测性 ----------------

    def register_metrics_collector(self, factory: Callable[[], Any]) -> None:
        """注册指标收集器工厂（NoOp / Prometheus / 自定义）。"""
        with self._lock:
            self._metrics_collector = factory

    def metrics_collector(self) -> Any:
        """获取指标收集器（SLO 指标）"""
        with self._lock:
            if self._metrics_collector is not None:
                return self._metrics_collector()
        from .observability.metrics import get_default_collector
        return get_default_collector()

    def register_otel_exporter(self, factory: Callable[[], Any]) -> None:
        """注册 OTLP trace exporter 工厂。"""
        with self._lock:
            self._otel_exporter = factory

    def otel_exporter(self) -> Any:
        """获取 OTLP trace exporter。"""
        with self._lock:
            if self._otel_exporter is not None:
                return self._otel_exporter()
        from .observability.otel_exporter import get_default_exporter
        return get_default_exporter()

    def register_tracer(self, factory: Callable[[], Any]) -> None:
        """注册 Tracer 工厂（core.tracing）。"""
        with self._lock:
            self._tracer = factory

    def tracer(self) -> Any:
        """获取 Tracer（分布式追踪）。"""
        with self._lock:
            if self._tracer is not None:
                return self._tracer()
        from agentorchestra.runtime.core.tracing import get_tracer
        return get_tracer()

    # ---------------- 装配组合 ----------------

    def enable_prometheus(self) -> Any:
        """启用 Prometheus 文本指标收集器为默认（幂等）。"""
        from .observability.metrics import enable_prometheus_collector
        with self._lock:
            self._metrics_collector = enable_prometheus_collector
        return self._metrics_collector()

    def enable_otel_trace(self, endpoint: str = "http://localhost:4318",
                          service_name: str = "agentorchestra") -> Any:
        """启用 OTLP trace 导出（端点必须可达；默认关，调用即开启）。"""
        from .observability.otel_exporter import OTLPHttpJsonExporter

        exporter = OTLPHttpJsonExporter(endpoint=endpoint,
                                        service_name=service_name).enable()
        with self._lock:
            self._otel_exporter = lambda: exporter
        # 接入全局 Tracer
        from agentorchestra.runtime.core.tracing import get_tracer
        get_tracer(exporter=exporter)
        return exporter

    def reset(self) -> None:
        """还原所有默认（测试 / 装配清理用）。"""
        with self._lock:
            self._state_store = None
            self._metrics_collector = None
            self._otel_exporter = None
            self._tracer = None
        # 还原 observability/metrics 默认（NoOp）
        from .observability.metrics import reset_default_collector
        reset_default_collector()


# 全局装配实例
Components: _Components = _Components()


__all__ = ["Components"]
