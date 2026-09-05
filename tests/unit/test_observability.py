"""Observability 模块单元测试"""

import pytest
import tempfile
import os
from agentorchestra.observability.trace_logger import TraceLogger
from agentorchestra.observability.metrics import MetricsCollector
from agentorchestra.runtime.core.tracing import Span, SpanExporter


class TestTraceLogger:
    """追踪日志器测试"""

    def test_trace_logger_creation(self):
        """测试日志器创建"""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = TraceLogger(output_dir=tmpdir)
            assert logger is not None

    def test_log_event(self):
        """测试记录事件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = TraceLogger(output_dir=tmpdir)
            logger.log_event("test_event", {"key": "value"})
            # 验证事件被记录
            assert logger._event_count > 0

    def test_trace_logger_context(self):
        """测试日志器上下文"""
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = TraceLogger(output_dir=tmpdir)
            with logger.trace("test_trace"):
                logger.log_event("inner_event", {"data": "test"})
            assert logger._event_count > 0


class TestMetricsCollector:
    """指标收集器测试"""

    def test_metrics_collector_creation(self):
        """测试收集器创建"""
        collector = MetricsCollector()
        assert collector is not None

    def test_record_metric(self):
        """测试记录指标"""
        collector = MetricsCollector()
        collector.record("test_metric", 1.0)
        assert "test_metric" in collector._metrics

    def test_get_metric(self):
        """测试获取指标"""
        collector = MetricsCollector()
        collector.record("get_test", 42.0)
        value = collector.get("get_test")
        assert value == 42.0


class TestSpan:
    """Span 测试"""

    def test_span_creation(self):
        """测试 Span 创建"""
        span = Span(name="test_span")
        assert span.name == "test_span"
        assert span.status == "unset"

    def test_span_set_status(self):
        """测试设置状态"""
        span = Span(name="test")
        span.set_status("ok")
        assert span.status == "ok"

    def test_span_set_attribute(self):
        """测试设置属性"""
        span = Span(name="test")
        span.set_attribute("key", "value")
        assert span.attributes["key"] == "value"

    def test_span_add_event(self):
        """测试添加事件"""
        span = Span(name="test")
        span.add_event("test_event", {"data": "value"})
        assert len(span.events) > 0
