"""TransactionManager - 事务管理器（动作原子性/补偿）

M1（P1）后：支持两种执行引擎：
- 默认：纯 saga（内存），向后兼容旧 API 行为（无 DB 依赖）
- coordinator 模式：委托给 `agentorchestra.tx.TransactionCoordinator`（幂等 + WAL + 补偿 + DLQ）

补偿模式（Saga）：
  动作A(成功) → 动作B(成功) → 动作C(失败)
    ↓ 回滚
  补偿C(跳过) → 补偿B → 补偿A  → 状态恢复
"""

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


class CompensatingAction:
    """带补偿的动作定义"""

    def __init__(self, name: str, action_fn: Callable, compensate_fn: Optional[Callable]):
        """定义可补偿动作

        Args:
            name: 动作名
            action_fn: 执行函数 fn(params, ctx) -> result
            compensate_fn: 补偿函数 fn(params, ctx)（撤销 action_fn 的效果）
        """
        self.name = name
        self.action_fn = action_fn
        self.compensate_fn = compensate_fn


class TransactionManager:
    """事务管理器"""

    def __init__(self, coordinator: Optional[Any] = None):
        self._actions: Dict[str, CompensatingAction] = {}
        self._tx_log: List[Dict[str, Any]] = []
        self.coordinator = coordinator  # Optional TransactionCoordinator（M1）

    # ==================== 注册 ====================

    def register_action(self, action: CompensatingAction) -> None:
        """注册可补偿动作"""
        self._actions[action.name] = action

    def register(self, name: str, action_fn: Callable,
                 compensate_fn: Optional[Callable] = None) -> CompensatingAction:
        """便捷注册动作"""
        action = CompensatingAction(name, action_fn, compensate_fn)
        self._actions[name] = action
        return action

    def get_action(self, name: str) -> Optional[CompensatingAction]:
        """按名称获取已注册动作"""
        return self._actions.get(name)

    def set_coordinator(self, coordinator: Any) -> None:
        """启用 coordinator 引擎（M1）。

        之后 execute() 委托给 coordinator（幂等 + WAL + 补偿 + DLQ）。
        """
        self.coordinator = coordinator
        # 把已注册动作同步到 coordinator
        for name, action in self._actions.items():
            self._sync_action_to_coordinator(name, action)

    def _sync_action_to_coordinator(self, name: str, action: CompensatingAction) -> None:
        if self.coordinator is None:
            return
        self.coordinator.register_action(
            name,
            execute_fn=lambda p, _tx, _a=action: _a.action_fn(p, {}),
            compensate_fn=(
                (lambda p, _tx, _a=action: _a.compensate_fn(p, {}))
                if action.compensate_fn is not None
                else None
            ),
        )

    # ==================== 事务执行 ====================

    def execute(self, steps: List[Dict[str, Any]],
                ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """执行事务（Saga 补偿）

        - 若启用了 coordinator：委托给新运行时（sync 桥接）。
        - 否则：旧纯内存 saga 逻辑（默认，向后兼容）。

        Args:
            steps: [{"action": "扣库存", "params": {...}}, ...]
            ctx: 执行上下文

        Returns:
            {"success", "completed": [动作名], "failed": 失败动作,
             "compensated": [已补偿动作名], "errors"}
        """
        if self.coordinator is not None:
            from agentorchestra.governance.tx.sync import run_sync
            return run_sync(lambda: self._execute_via_coordinator(steps))

        return self._execute_saga(steps, ctx)

    # ---- coordinator 引擎路径 ----

    async def _execute_via_coordinator(self,
                                       steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """委托 coordinator（async 核心）。返回与旧 execute 相同的 dict。"""
        assert self.coordinator is not None
        # 确保 coordinator 已注册当前动作集
        for name, action in self._actions.items():
            if self.coordinator.get_action(name) is None:
                self._sync_action_to_coordinator(name, action)

        # 校验动作是否都注册
        unregistered = [
            s.get("action") for s in steps
            if self.coordinator.get_action(s.get("action")) is None
        ]
        if unregistered:
            # 无成功动作，无需补偿；直接返回失败（与旧语义一致）
            return {
                "success": False,
                "completed": [],
                "failed": unregistered[0],
                "compensated": [],
                "errors": [f"动作未注册: {unregistered[0]}"],
                "engine": "coordinator",
            }

        result: Dict[str, Any] = {
            "success": False,
            "completed": [],
            "failed": None,
            "compensated": [],
            "errors": [],
            "engine": "coordinator",
        }
        tx = None
        try:
            async with self.coordinator.transaction() as tx:
                for step in steps:
                    await tx.execute(step.get("action"), step.get("params", {}))
                result["success"] = True
                result["completed"] = list(tx.completed)
                result["errors"] = []
                return result
        except Exception as e:
            # 事务失败，coordinator 已自动逆序补偿。
            # tx.completed = 曾成功（现被补偿）的动作
            result["success"] = False
            result["failed"] = getattr(e, "name", None) or type(e).__name__
            result["compensated"] = list(tx.completed) if tx is not None else []
            result["errors"].append(f"事务失败: {e}")
            return result

    # ---- 旧 saga 引擎路径（默认） ----

    def _execute_saga(self, steps: List[Dict[str, Any]],
                      ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """旧版纯内存 Saga（向后兼容）。"""
        ctx = ctx or {}
        completed: List[str] = []
        completed_params: Dict[str, Dict] = {}
        tx_record: Dict[str, Any] = {
            "started_at": datetime.now().isoformat(),
            "steps": [s.get("action") for s in steps],
            "completed": [],
            "failed": None,
            "compensated": [],
            "errors": [],
        }

        # ① 正序执行
        for step in steps:
            action_name = step.get("action")
            if not isinstance(action_name, str):
                tx_record["failed"] = action_name
                tx_record["errors"].append(f"动作名无效: {action_name}")
                break
            action = self._actions.get(action_name)
            if not action:
                tx_record["failed"] = action_name
                tx_record["errors"].append(f"动作未注册: {action_name}")
                break

            step_params = step.get("params", {})
            try:
                action.action_fn(step_params, ctx)
                completed.append(action_name)
                completed_params[action_name] = step_params
                tx_record["completed"].append(action_name)
            except Exception as e:
                tx_record["failed"] = action_name
                tx_record["errors"].append(f"动作 '{action_name}' 失败: {e}")
                break

        # ② 若失败，逆序补偿
        if tx_record["failed"]:
            for name in reversed(completed):
                action = self._actions.get(name)
                if action and action.compensate_fn:
                    try:
                        action.compensate_fn(completed_params.get(name, {}), ctx)
                        tx_record["compensated"].append(name)
                    except Exception as e:
                        tx_record["errors"].append(f"补偿 '{name}' 失败: {e}")
                elif action and not action.compensate_fn:
                    tx_record["errors"].append(f"动作 '{name}' 无补偿，无法回滚")

        tx_record["success"] = not tx_record["failed"]
        tx_record["ended_at"] = datetime.now().isoformat()
        self._tx_log.append(tx_record)
        return tx_record

    # ==================== 保存点 ====================

    def savepoint(self, name: str, store) -> Dict[str, Any]:
        """创建保存点（快照当前状态）"""
        from ..storage.object_store import ObjectStore
        snapshot: Dict[str, Any] = {"objects": {}}
        if isinstance(store, ObjectStore):
            for t in store.list_types():
                snapshot["objects"][t] = list(store.list_objects(t))
        return {"name": name, "snapshot": snapshot}

    def rollback_to(self, savepoint: Dict[str, Any], store) -> bool:
        """回滚到保存点"""
        from ..storage.object_store import ObjectStore
        if not isinstance(store, ObjectStore):
            return False
        try:
            for t in store.list_types():
                obj_type = store.get_type(t)
                if obj_type is None:
                    continue
                for obj in store.list_objects(t):
                    pk = obj.get(obj_type.primary_key)
                    if pk is not None:
                        store.delete(t, str(pk))
            for t, objs in savepoint.get("snapshot", {}).get("objects", {}).items():
                for obj in objs:
                    try:
                        store.insert(t, dict(obj))
                    except Exception:
                        pass
            return True
        except Exception:
            return False

    # ==================== 查询 ====================

    def get_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """查询事务日志"""
        return list(reversed(self._tx_log))[:limit]

    def clear_log(self) -> None:
        """清空事务日志"""
        self._tx_log.clear()
