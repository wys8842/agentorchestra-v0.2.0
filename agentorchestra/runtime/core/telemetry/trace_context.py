"""W3C TraceContext - 分布式追踪上下文

实现 W3C Trace Context 标准：
- traceparent: 00-{trace_id}-{parent_span_id}-{flags}
- tracestate: 厂商特定状态

支持跨服务/跨进程传递追踪上下文。
"""

from __future__ import annotations

import re
import secrets
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional

# W3C TraceContext 规范
TRACEPARENT_FORMAT = "00-{trace_id}-{parent_id}-{flags}"
TRACE_ID_HEX_LENGTH = 32  # 16 bytes = 32 hex chars
SPAN_ID_HEX_LENGTH = 16   # 8 bytes = 16 hex chars
FLAGS_LENGTH = 2          # 1 byte = 2 hex chars

VALID_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
VALID_SPAN_ID = re.compile(r"^[0-9a-f]{16}$")
VALID_FLAGS = re.compile(r"^[0-9a-f]{2}$")


def _generate_trace_id() -> str:
    """生成 16 字节 trace ID（32 hex chars）"""
    return secrets.token_hex(16)


def _generate_span_id() -> str:
    """生成 8 字节 span ID（16 hex chars）"""
    return secrets.token_hex(8)


@dataclass
class TraceContext:
    """W3C TraceContext 数据

    Attributes:
        trace_id: 全局追踪 ID（32 hex chars）
        span_id: 当前 span ID（16 hex chars）
        parent_span_id: 父 span ID（可选）
        flags: 追踪标志（采样/调试）
        tracestate: 厂商状态字典
    """

    trace_id: str = field(default_factory=_generate_trace_id)
    span_id: str = field(default_factory=_generate_span_id)
    parent_span_id: Optional[str] = None
    flags: str = "01"  # 01 表示已采样
    tracestate: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not VALID_TRACE_ID.match(self.trace_id):
            raise ValueError(f"invalid trace_id: {self.trace_id}")
        if not VALID_SPAN_ID.match(self.span_id):
            raise ValueError(f"invalid span_id: {self.span_id}")
        if self.parent_span_id and not VALID_SPAN_ID.match(self.parent_span_id):
            raise ValueError(f"invalid parent_span_id: {self.parent_span_id}")

    def to_traceparent(self) -> str:
        """序列化为 W3C traceparent header"""
        return f"00-{self.trace_id}-{self.span_id}-{self.flags}"

    def to_tracestate(self) -> str:
        """序列化为 W3C tracestate header"""
        if not self.tracestate:
            return ""
        return ",".join(f"{k}={v}" for k, v in self.tracestate.items())

    def to_headers(self) -> Dict[str, str]:
        """导出为 HTTP headers"""
        headers = {"traceparent": self.to_traceparent()}
        ts = self.to_tracestate()
        if ts:
            headers["tracestate"] = ts
        return headers

    def child_context(self) -> "TraceContext":
        """创建子 span 的上下文"""
        return TraceContext(
            trace_id=self.trace_id,
            span_id=_generate_span_id(),
            parent_span_id=self.span_id,
            flags=self.flags,
            tracestate=dict(self.tracestate),
        )

    @classmethod
    def from_traceparent(
        cls, traceparent: str, tracestate: str = ""
    ) -> "TraceContext":
        """从 W3C header 解析

        Args:
            traceparent: 00-{trace_id}-{parent_id}-{flags}
            tracestate: 厂商状态

        Returns:
            TraceContext 实例
        """
        if not traceparent:
            return cls()

        parts = traceparent.split("-")
        if len(parts) != 4:
            raise ValueError(f"invalid traceparent: {traceparent}")
        version, trace_id, parent_id, flags = parts

        if not VALID_TRACE_ID.match(trace_id):
            raise ValueError(f"invalid trace_id in traceparent: {trace_id}")
        if not VALID_SPAN_ID.match(parent_id):
            raise ValueError(f"invalid parent_span_id: {parent_id}")

        ts_dict = {}
        if tracestate:
            for item in tracestate.split(","):
                if "=" in item:
                    k, v = item.split("=", 1)
                    ts_dict[k.strip()] = v.strip()

        return cls(
            trace_id=trace_id,
            span_id=_generate_span_id(),
            parent_span_id=parent_id,
            flags=flags,
            tracestate=ts_dict,
        )


# 当前活跃上下文（线程/任务本地）
_local = threading.local()


def get_current_context() -> Optional[TraceContext]:
    """获取当前线程/任务的追踪上下文"""
    return getattr(_local, "context", None)


def set_current_context(ctx: Optional[TraceContext]) -> Optional[TraceContext]:
    """设置当前线程/任务的追踪上下文"""
    old = getattr(_local, "context", None)
    _local.context = ctx
    return old


class TraceContextPropagator:
    """追踪上下文传播器（支持多种媒介）"""

    @staticmethod
    def to_dict(ctx: TraceContext) -> Dict[str, str]:
        """传播到 dict（适用于 HTTP headers、消息队列）"""
        return ctx.to_headers()

    @staticmethod
    def from_dict(data: Dict[str, str]) -> TraceContext:
        """从 dict 还原"""
        traceparent = data.get("traceparent", "")
        tracestate = data.get("tracestate", "")
        return TraceContext.from_traceparent(traceparent, tracestate)

    @staticmethod
    def to_kafka_headers(ctx: TraceContext) -> list:
        """传播到 Kafka headers"""
        return [(k, v.encode("utf-8")) for k, v in ctx.to_headers().items()]

    @staticmethod
    def from_kafka_headers(headers: list) -> TraceContext:
        """从 Kafka headers 还原"""
        data = {k: v.decode("utf-8") for k, v in headers}
        return TraceContextPropagator.from_dict(data)


def trace_context_from_headers(headers: Dict[str, str]) -> TraceContext:
    """从 HTTP headers 创建追踪上下文（便利函数）"""
    return TraceContextPropagator.from_dict(headers)
