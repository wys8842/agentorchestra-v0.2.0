"""健康检查

提供组件健康状态报告：
- 基础健康检查（存活/就绪）
- 组件级检查（LLM 配置、存储、会话）
- 聚合健康报告（供 /health 端点）
"""

from typing import Any, Callable, Dict, List


class HealthCheck:
    """健康检查器"""

    def __init__(self, name: str = "agentorchestra"):
        """初始化健康检查

        Args:
            name: 服务名
        """
        self.name = name
        self._checks: List[Callable[[], Dict[str, Any]]] = []

    def register(self, check_fn: Callable[[], Dict[str, Any]]) -> None:
        """注册健康检查函数

        Args:
            check_fn: 返回 {"name", "status", "detail"} 的函数
        """
        self._checks.append(check_fn)

    def check(self) -> Dict[str, Any]:
        """执行所有健康检查

        Returns:
            {"status", "checks": [...]}
        """
        results = []
        all_ok = True
        for check_fn in self._checks:
            try:
                result = check_fn()
                results.append(result)
                if result.get("status") != "ok":
                    all_ok = False
            except Exception as e:
                results.append({"name": getattr(check_fn, "__name__", "unknown"),
                                "status": "error", "detail": str(e)})
                all_ok = False

        return {
            "name": self.name,
            "status": "ok" if all_ok else "degraded",
            "checks": results,
        }

    def register_basic(self) -> None:
        """注册基础检查（进程存活/时间）"""
        self.register(lambda: {"name": "runtime", "status": "ok",
                               "detail": {"uptime_check": "alive"}})

    def register_config_check(self, config) -> None:
        """注册配置检查（关键配置是否存在）"""
        def _check():
            problems = []
            if not getattr(config, "default_model", None):
                problems.append("default_model 未配置")
            return {"name": "config", "status": "ok" if not problems else "degraded",
                    "detail": {"problems": problems}}
        self.register(_check)

    def register_store_check(self, store) -> None:
        """注册存储检查"""
        def _check():
            try:
                stats = store.stats()
                return {"name": "object_store", "status": "ok",
                        "detail": {"types": stats.get("types", 0),
                                   "objects": stats.get("objects", {})}}
            except Exception as e:
                return {"name": "object_store", "status": "error", "detail": str(e)}
        self.register(_check)


