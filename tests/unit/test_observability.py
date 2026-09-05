"""Observability 模块单元测试"""

import pytest


class TestMetricsCollector:
    """指标收集器测试"""

    def test_metrics_collector_exists(self):
        """测试指标收集器存在"""
        from agentorchestra.observability.metrics import MetricsCollector
        assert MetricsCollector is not None


class TestTraceLogger:
    """追踪日志器测试"""

    def test_trace_logger_exists(self):
        """测试追踪日志器存在"""
        from agentorchestra.observability.trace_logger import TraceLogger
        assert TraceLogger is not None


class TestSpan:
    """Span 测试"""

    def test_span_class_exists(self):
        """测试 Span 类存在"""
        from agentorchestra.runtime.core.tracing import Span
        assert Span is not None
