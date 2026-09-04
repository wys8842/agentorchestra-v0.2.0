"""Materialization - 物化

把对象编辑结果回写到数据源，形成"编辑 → 物化"闭环。
支持注册自定义数据源回写函数。
"""

from typing import Any, Callable, Dict, List, Optional


class MaterializationTarget:
    """物化目标（数据源回写目标）"""

    def __init__(self, name: str, write_fn: Callable):
        """定义物化目标

        Args:
            name: 目标名（如 "postgres_orders"）
            write_fn: 回写函数 fn(operation, type_name, obj, patch) -> bool
                operation: "insert"/"update"/"delete"
        """
        self.name = name
        self.write_fn = write_fn

    def write(self, operation: str, type_name: str,
              obj: Dict[str, Any], patch: Optional[Dict] = None) -> bool:
        """执行回写函数，返回是否成功"""
        return self.write_fn(operation, type_name, obj, patch)

    def to_tx_action(self) -> Any:
        """把物化目标包装成事务动作（M1）。

        返回 `agentorchestra.tx.TxAction`：执行 = write，补偿 = write 反向操作。
        反向操作映射：insert→delete、update→重写 before、delete→insert before。
        对象数据从 write_fn 的业务侧持久化上下文取（operation/obj/patch）。

        注意：此方法提供"物化动作包成 TxAction"的能力；默认业务路径不强制使用
        （保留现有 materialize 直写语义，向后兼容）。
        """
        from agentorchestra.governance.tx.context import TxAction

        def _execute_fn(params, _tx_ctx=None) -> bool:
            op = params.get("operation", "update")
            return self.write(
                op,
                params.get("type_name", ""),
                params.get("obj", {}),
                params.get("patch"),
            )

        def _compensate_fn(params, _tx_ctx=None) -> bool:
            # 反向写（Saga 补偿）：用 before 快照复原
            reverse_op = {"insert": "delete", "update": "update",
                          "delete": "insert"}.get(
                              params.get("operation", "update"), "update")
            reverse_params = dict(params)
            reverse_params["operation"] = reverse_op
            if "before" in params and reverse_op == "update":
                reverse_params["obj"] = params["before"]
            return self.write(
                reverse_op,
                params.get("type_name", ""),
                params.get("obj", {}),
                params.get("patch"),
            )

        return TxAction(
            name=f"materialize:{self.name}",
            execute_fn=_execute_fn,
            compensate_fn=_compensate_fn,
        )


class MaterializationManager:
    """物化管理器"""

    def __init__(self):
        self._targets: Dict[str, MaterializationTarget] = {}
        self._log: List[Dict[str, Any]] = []

    def register_target(self, target: MaterializationTarget) -> None:
        """注册物化目标"""
        self._targets[target.name] = target

    def materialize(self, operation: str, type_name: str,
                    obj: Dict[str, Any], patch: Optional[Dict] = None,
                    target_name: Optional[str] = None) -> List[bool]:
        """执行物化（写回目标数据源）"""
        results = []
        targets = ([self._targets[target_name]] if target_name
                   else list(self._targets.values()))

        for target in targets:
            try:
                ok = target.write(operation, type_name, obj, patch)
                self._log.append({
                    "target": target.name,
                    "operation": operation,
                    "type": type_name,
                    "pk": obj.get("pk") if obj else None,
                    "success": ok,
                })
                results.append(ok)
            except Exception as e:
                self._log.append({
                    "target": target.name,
                    "operation": operation,
                    "type": type_name,
                    "success": False,
                    "error": str(e),
                })
                results.append(False)

        return results

    def get_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """查询物化日志（最新在前）"""
        return list(reversed(self._log))[:limit]

    def clear_log(self) -> None:
        """清空物化日志"""
        self._log.clear()
