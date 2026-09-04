"""监控 HTTP 端点（标准库实现）

提供运维 HTTP 服务：
- GET /metrics   Prometheus 指标（Prometheus 抓取）
- GET /health    健康检查报告
- GET /traces    最近追踪 span（JSON）
- GET /          服务信息

使用 Python 标准库 http.server，无外部依赖。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional


class MonitorServer:
    """监控 HTTP 服务"""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9090,
        health_check: Any = None,
        metrics_provider: Optional[Callable[[], str]] = None,
        traces_provider: Optional[Callable[[], List[Dict]]] = None,
    ):
        """初始化监控服务

        Args:
            host: 绑定地址
            port: 端口
            health_check: HealthCheck 实例
            metrics_provider: 返回 Prometheus 文本的 callable
            traces_provider: 返回 span 列表的 callable
        """
        self.host = host
        self.port = port
        self.health_check = health_check
        self.metrics_provider = metrics_provider
        self.traces_provider = traces_provider
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ==================== 服务控制 ====================

    def start(self) -> None:
        """启动监控服务（后台线程）"""
        if self._server is not None:
            return

        handler = self._make_handler()
        self._server = HTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="monitor-server")
        self._thread.start()

    def stop(self) -> None:
        """停止监控服务"""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_running(self) -> bool:
        """监控服务是否已启动"""
        return self._server is not None

    # ==================== 内部 ====================

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            """HTTP 请求处理器：按路径分发 /metrics /health /traces"""

            def do_GET(self):
                """处理 GET 请求，按路径返回指标/健康/追踪/服务信息"""
                if self.path == "/metrics":
                    self._send_text(server._get_metrics())
                elif self.path == "/health":
                    self._send_json(server._get_health())
                elif self.path == "/traces":
                    self._send_json(server._get_traces())
                elif self.path in ("/", ""):
                    self._send_json({
                        "service": "symphony-monitor",
                        "endpoints": ["/metrics", "/health", "/traces"],
                    })
                else:
                    self.send_error(404)

            def _send_text(self, text: str):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(text.encode("utf-8"))

            def _send_json(self, data):
                body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                """静默访问日志，避免污染 stdout"""
                pass

        return Handler

    def _get_metrics(self) -> str:
        if self.metrics_provider:
            try:
                return self.metrics_provider()
            except Exception as e:
                return f"# 指标生成失败: {e}"
        # M5：回退到 observability SLO collector（Prometheus 文本；NoOp 则空）
        try:
            from agentorchestra.observability.metrics import get_default_collector
            text = get_default_collector().render()
            if text:
                return text
        except Exception:
            pass
        return "# 未配置 metrics_provider"

    def _get_health(self) -> Dict:
        if self.health_check:
            try:
                return self.health_check.check()
            except Exception as e:
                return {"status": "error", "detail": str(e)}
        return {"status": "ok", "detail": "未配置 health_check"}

    def _get_traces(self) -> Dict:
        if self.traces_provider:
            try:
                spans = self.traces_provider()
                return {"count": len(spans), "spans": spans}
            except Exception as e:
                return {"error": str(e)}
        return {"count": 0, "spans": []}
