"""分布式追踪（轻量实现，兼容 OpenTelemetry 语义）

提供：
- Trace/Span 上下文（trace_id/span_id/parent）
- span 创建/结束（耗时/属性/状态）
- 层级嵌套（父 span → 子 span）
- 导出器接口（内存/JSONL/可扩展 OTLP）
- 上下文传播（跨调用传递 trace 上下文）

设计为轻量自实现（无外部依赖），接口对齐 OpenTelemetry 概念，
后续可替换为官方 OpenTelemetry SDK。
"""

import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional


class Span:
    """追踪跨度（一次操作的时间段）"""

    def __init__(self, name: str, trace_id: str, span_id: str,
                 parent_id: Optional[str] = None,
                 attributes: Optional[Dict[str, Any]] = None):
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_id = parent_id
        self.attributes = attributes or {}
        self.status: str = "OK"  # OK / ERROR
        self.start_time = time.monotonic()
        self.end_time: Optional[float] = None
        self.events: List[Dict[str, Any]] = []

    def set_attribute(self, key: str, value: Any) -> None:
        """设置 span 属性"""
        self.attributes[key] = value

    def add_event(self, name: str, attributes: Optional[Dict] = None) -> None:
        """添加事件（span 内的时间点）"""
        self.events.append({
            "name": name,
            "timestamp": time.monotonic(),
            "attributes": attributes or {},
        })

    def set_error(self) -> None:
        """标记为错误状态"""
        self.status = "ERROR"

    def end(self) -> None:
        """结束 span"""
        if self.end_time is None:
            self.end_time = time.monotonic()

    @property
    def duration_ms(self) -> float:
        """span 耗时（毫秒）"""
        end = self.end_time if self.end_time is not None else time.monotonic()
        return (end - self.start_time) * 1000

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（导出用）"""
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 2),
            "start": datetime.now().isoformat(),
            "attributes": self.attributes,
            "events": self.events,
        }


class SpanExporter:
    """span 导出器接口"""

    def export(self, span: Span) -> None:
        """导出单个 span"""
        raise NotImplementedError


class MemoryExporter(SpanExporter):
    """内存导出器（测试/调试）"""

    def __init__(self):
        self.spans: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def export(self, span: Span) -> None:
        """导出单个 span 到内存列表（线程安全）"""
        with self._lock:
            self.spans.append(span.to_dict())

    def read_all(self) -> List[Dict[str, Any]]:
        """读取全部 span（返回副本，线程安全）"""
        with self._lock:
            return list(self.spans)

    def clear(self) -> None:
        """清空已导出的 span（线程安全）"""
        with self._lock:
            self.spans.clear()


class JsonlExporter(SpanExporter):
    """JSONL 文件导出器（线程安全写入，可回读）"""

    def __init__(self, filepath: str = "memory/traces/spans.jsonl"):
        import os
        if os.path.dirname(filepath):
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self.filepath = filepath
        self._lock = threading.Lock()

    def export(self, span: Span) -> None:
        """追加导出单个 span 到 JSONL 文件（线程安全）"""
        with self._lock:
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(span.to_dict(), ensure_ascii=False) + "\n")

    def read_all(self) -> List[Dict[str, Any]]:
        """读取全部 span（供 monitor /traces 消费）"""
        import os
        if not os.path.exists(self.filepath):
            return []
        with self._lock:
            spans = []
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            spans.append(json.loads(line))
            except (IOError, json.JSONDecodeError):
                return []
            return spans

    def clear(self) -> None:
        """删除 JSONL 文件以清空已导出的 span"""
        import os
        with self._lock:
            if os.path.exists(self.filepath):
                try:
                    os.remove(self.filepath)
                except OSError:
                    pass


class Tracer:
    """追踪器（线程安全，管理 trace 上下文）"""

    def __init__(self, exporter: Optional[SpanExporter] = None):
        self.exporter = exporter or MemoryExporter()
        # 线程级 span 栈（支持嵌套）
        self._local = threading.local()

    # ==================== 上下文 ====================

    def _span_stack(self) -> List[Span]:
        """获取当前线程的 span 栈"""
        if not hasattr(self._local, "span_stack"):
            self._local.span_stack = []
        return self._local.span_stack

    def current_span(self) -> Optional[Span]:
        """当前活跃 span"""
        stack = self._span_stack()
        return stack[-1] if stack else None

    def current_trace_id(self) -> Optional[str]:
        """当前 trace_id"""
        span = self.current_span()
        return span.trace_id if span else None

    # ==================== span 操作 ====================

    def start_span(self, name: str, attributes: Optional[Dict] = None) -> Span:
        """启动新 span（继承当前上下文）"""
        parent = self.current_span()
        trace_id = parent.trace_id if parent else uuid.uuid4().hex[:16]
        span = Span(
            name=name,
            trace_id=trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_id=parent.span_id if parent else None,
            attributes=attributes,
        )
        self._span_stack().append(span)
        return span

    def end_span(self, span: Span) -> None:
        """结束 span 并导出"""
        span.end()
        stack = self._span_stack()
        if stack and stack[-1] is span:
            stack.pop()
        try:
            self.exporter.export(span)
        except Exception:
            pass

    @contextmanager
    def span(self, name: str, attributes: Optional[Dict] = None) -> Iterator[Span]:
        """上下文管理器：自动 start/end span

        Example:
            with tracer.span("llm_call", {"model": "gpt-4"}) as sp:
                result = llm.invoke(messages)
        """
        span = self.start_span(name, attributes)
        try:
            yield span
        except Exception as e:
            span.set_error()
            span.set_attribute("error", str(e))
            raise
        finally:
            self.end_span(span)

    # ==================== 导出 ====================

    def export_all(self) -> List[Dict[str, Any]]:
        """导出所有已结束 span（支持 Memory/Jsonl exporter）"""
        if hasattr(self.exporter, "read_all"):
            return self.exporter.read_all()
        return []

    def clear(self) -> None:
        """清空导出的 span"""
        if hasattr(self.exporter, "clear"):
            self.exporter.clear()


# 全局追踪器
_global_tracer: Optional[Tracer] = None
_tracer_lock = threading.Lock()


def get_tracer(exporter: Optional[SpanExporter] = None) -> Tracer:
    """获取全局追踪器（线程安全）

    首次调用时创建；后续调用忽略 exporter 参数（保持单例一致性）。
    """
    global _global_tracer
    if _global_tracer is None:
        with _tracer_lock:
            if _global_tracer is None:
                _global_tracer = Tracer(exporter)
    return _global_tracer
