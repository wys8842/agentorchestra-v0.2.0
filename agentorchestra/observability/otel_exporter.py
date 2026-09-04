"""OTLP HTTP/JSON exporter（M5，可选，默认关，纯标准库）。

将现有轻量 core.tracing.Span 桥接为 OTLP/HTTP JSON 载荷，POST 到 Jaeger/Tempo
等 OTLP collector。零第三方依赖（urllib.request）。

默认关（enabled=False），避免误发；需接入企业后端时显式 enable。

设计见 docs/superpowers/specs/2026-09-04-m5-observability-design.md §4.3
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any, Dict, Optional

from agentorchestra.runtime.core.tracing import Span, SpanExporter

logger = logging.getLogger("agentorchestra.observability.otel_exporter")

_OTLP_HTTP_TRACES_PATH = "/v1/traces"


class OTLPHttpJsonExporter(SpanExporter):
    """core.tracing.Span → OTLP/HTTP JSON（Jaeger/Tempo）。

    Attributes:
        enabled: 是否发送（默认 False）。enable() 开启。
        endpoint: OTLP collector base（默认 http://localhost:4318）
        service_name: resource.service.name
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:4318",
        service_name: str = "agentorchestra",
        timeout: float = 5.0,
    ):
        self.enabled = False
        self.endpoint = endpoint.rstrip("/")
        self.service_name = service_name
        self.timeout = timeout
        self._sent = 0
        self._failed = 0

    # ---------------- 生命周期 ----------------

    def enable(self) -> "OTLPHttpJsonExporter":
        """开启发送（链式返回自身）。"""
        self.enabled = True
        return self

    def disable(self) -> "OTLPHttpJsonExporter":
        """关闭发送（链式返回自身）。"""
        self.enabled = False
        return self

    @property
    def sent(self) -> int:
        """成功发送次数。"""
        return self._sent

    @property
    def failed(self) -> int:
        """发送失败次数。"""
        return self._failed

    # ---------------- 桥接 ----------------

    def export(self, span: Span) -> None:
        """桥接：core Span → OTLP JSON → POST（enabled=False 时丢弃）。"""
        if not self.enabled:
            return
        payload = self._build_payload(span)
        try:
            self._post(payload)
            self._sent += 1
        except Exception as e:
            self._failed += 1
            logger.warning(f"OTLP export 失败: {e}")

    # ---------------- 载荷构建 ----------------

    def _build_payload(self, span: Span) -> Dict[str, Any]:
        """构造 OTLP/HTTP JSON resourceSpans 载荷。"""
        attributes = []
        for k, v in (span.attributes or {}).items():
            attributes.append({"key": k, "value": self._attr_value(v)})

        status: Dict[str, Any] = {"code": 1}  # OK
        if span.status == "ERROR":
            status = {"code": 2}

        otel_span = {
            "traceId": self._to_hex_trace_id(span.trace_id),
            "spanId": self._to_hex_span_id(span.span_id),
            "name": span.name,
            "kind": 2,  # SPAN_KIND_INTERNAL
            "startTimeUnixNano": self._start_ns(span),
            "endTimeUnixNano": self._end_ns(span),
            "attributes": attributes,
            "status": status,
            "events": [
                {
                    "timeUnixNano": self._monotonic_to_ns(int(e["timestamp"] * 1e9)),
                    "name": e["name"],
                    "attributes": [
                        {"key": k, "value": self._attr_value(v)}
                        for k, v in (e.get("attributes") or {}).items()
                    ],
                }
                for e in span.events
            ],
        }
        if span.parent_id:
            otel_span["parentSpanId"] = self._to_hex_span_id(span.parent_id)

        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name",
                             "value": {"stringValue": self.service_name}},
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "agentorchestra"},
                            "spans": [otel_span],
                        }
                    ],
                }
            ]
        }

    @staticmethod
    def _attr_value(v: Any) -> Dict[str, Any]:
        if isinstance(v, bool):
            return {"boolValue": v}
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return {"intValue": str(int(v)) if float(v).is_integer() else str(float(v))}
        if isinstance(v, (list, dict)):
            return {"stringValue": json.dumps(v, ensure_ascii=False, default=str)}
        return {"stringValue": str(v)}

    # ---------------- id / 时间转换 ----------------

    @staticmethod
    def _to_hex_trace_id(trace_id: str) -> str:
        """trace_id 对齐到 16-byte hex（32 字符）；短则左补 0。"""
        return trace_id.replace("-", "").zfill(32)[:32]

    @staticmethod
    def _to_hex_span_id(span_id: str) -> str:
        """span_id 对齐到 8-byte hex（16 字符）。"""
        return span_id.replace("-", "").zfill(16)[:16]

    @staticmethod
    def _start_ns(span: Span) -> int:
        # 粗略：用当前墙钟 - 剩余时长 近似 start（事件单调时钟转墙钟困难）
        return int((time.time() - span.duration_ms / 1000.0) * 1e9)

    @staticmethod
    def _end_ns(span: Span) -> int:
        return int(time.time() * 1e9)

    @staticmethod
    def _monotonic_to_ns(monotonic_ns: int) -> int:
        return monotonic_ns

    # ---------------- 发送 ----------------

    def _post(self, payload: Dict[str, Any]) -> None:
        url = self.endpoint + _OTLP_HTTP_TRACES_PATH
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "agentorchestra/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            resp.read()


# 默认 exporter（NoOp 等价：enabled=False）
_default_exporter: Optional[OTLPHttpJsonExporter] = None


def get_default_exporter() -> OTLPHttpJsonExporter:
    """获取全局 OTLP exporter（懒加载，默认关闭）。"""
    global _default_exporter
    if _default_exporter is None:
        _default_exporter = OTLPHttpJsonExporter()
    return _default_exporter


def set_default_exporter(exporter: OTLPHttpJsonExporter) -> None:
    """替换全局 exporter（测试/装配）。"""
    global _default_exporter
    _default_exporter = exporter
