"""指标收集（Prometheus 指标）

提供框架级指标：
- llm_calls_total: LLM 调用次数（按模型/提供商）
- llm_tokens_total: LLM Token 消耗（按模型）
- llm_latency_ms: LLM 调用耗时
- tool_calls_total: 工具调用次数（按工具名）
- tool_errors_total: 工具错误次数
- action_executions_total: Ontology 动作执行次数
- active_requests: 活跃请求数

依赖 prometheus_client（可选）。未安装时指标收集静默降级。
"""

import threading
from typing import Any, Dict, Optional


class MetricsCollector:
    """指标收集器（Prometheus 后端）"""

    def __init__(self, enabled: bool = True):
        """初始化指标收集器

        Args:
            enabled: 是否启用（False 时所有方法为 no-op）
        """
        self.enabled = enabled
        self._registry: Dict[str, Any] = {}
        self._available = False

        if enabled:
            self._init_prometheus()

    def _init_prometheus(self) -> None:
        """初始化 Prometheus 指标"""
        try:
            from prometheus_client import Counter, Gauge, Histogram

            self._registry = {
                "llm_calls_total": Counter(
                    "symphony_llm_calls_total", "LLM 调用次数",
                    ["model", "provider"]),
                "llm_tokens_total": Counter(
                    "symphony_llm_tokens_total", "LLM Token 消耗",
                    ["model"]),
                "llm_latency_ms": Histogram(
                    "symphony_llm_latency_ms", "LLM 调用耗时(ms)"),
                "tool_calls_total": Counter(
                    "symphony_tool_calls_total", "工具调用次数",
                    ["tool"]),
                "tool_errors_total": Counter(
                    "symphony_tool_errors_total", "工具错误次数",
                    ["tool"]),
                "action_executions_total": Counter(
                    "symphony_action_executions_total", "Ontology 动作执行次数",
                    ["action"]),
                "active_requests": Gauge(
                    "symphony_active_requests", "活跃请求数"),
            }
            self._available = True
        except ImportError:
            # prometheus_client 未安装，降级为 no-op
            self._available = False

    @property
    def is_available(self) -> bool:
        """prometheus_client 是否可用"""
        return self._available

    # ==================== LLM 指标 ====================

    def record_llm_call(self, model: str, provider: str = "",
                        tokens: int = 0, latency_ms: float = 0.0) -> None:
        """记录 LLM 调用"""
        if not self._available:
            return
        self._registry["llm_calls_total"].labels(model=model, provider=provider).inc()
        if tokens:
            self._registry["llm_tokens_total"].labels(model=model).inc(tokens)
        if latency_ms:
            self._registry["llm_latency_ms"].observe(latency_ms)

    # ==================== 工具指标 ====================

    def record_tool_call(self, tool_name: str, error: bool = False) -> None:
        """记录工具调用"""
        if not self._available:
            return
        self._registry["tool_calls_total"].labels(tool=tool_name).inc()
        if error:
            self._registry["tool_errors_total"].labels(tool=tool_name).inc()

    # ==================== Ontology 指标 ====================

    def record_action_execution(self, action_name: str, error: bool = False) -> None:
        """记录 Ontology 动作执行"""
        if not self._available:
            return
        self._registry["action_executions_total"].labels(action=action_name).inc()

    # ==================== 请求指标 ====================

    def request_start(self) -> None:
        """请求开始（活跃数+1）"""
        if not self._available:
            return
        self._registry["active_requests"].inc()

    def request_end(self) -> None:
        """请求结束（活跃数-1）"""
        if not self._available:
            return
        self._registry["active_requests"].dec()

    # ==================== 导出 ====================

    def generate_latest(self) -> str:
        """生成 Prometheus 文本格式（用于 /metrics 端点）"""
        if not self._available:
            return "# prometheus_client 未安装，指标不可用"
        from prometheus_client import generate_latest as _generate
        return _generate().decode("utf-8")


# 全局指标收集器（懒加载，线程安全）
_global_metrics: Optional[MetricsCollector] = None
_metrics_lock = threading.Lock()


def get_metrics() -> MetricsCollector:
    """获取全局指标收集器"""
    global _global_metrics
    if _global_metrics is None:
        with _metrics_lock:
            if _global_metrics is None:
                _global_metrics = MetricsCollector()
    return _global_metrics
