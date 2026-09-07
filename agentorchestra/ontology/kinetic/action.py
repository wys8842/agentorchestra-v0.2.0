"""ActionType - 动作类型

动能层：定义组织的写操作能力。
- 参数定义
- 提交前规则校验（rules）
- 执行逻辑
- 副作用（通知/webhook/触发调度）
- 执行审计
"""

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from agentorchestra.capability.tools.base import ToolParameter


class ActionType:
    """动作类型定义"""

    def __init__(
        self,
        api_name: str,
        parameters: Optional[List[ToolParameter]] = None,
        description: str = "",
        execute_fn: Optional[Callable] = None,
        rules: Optional[List[Callable]] = None,
        side_effects: Optional[List[Callable]] = None,
        display_name: Optional[str] = None,
        idempotent: bool = True,
    ):
        self.api_name = api_name
        self.display_name = display_name or api_name
        self.description = description
        self.parameters: Dict[str, ToolParameter] = {}
        if parameters:
            for p in parameters:
                self.parameters[p.name] = p
        self.execute_fn = execute_fn
        self.rules = rules or []
        self.side_effects = side_effects or []
        self._audit: List[Dict[str, Any]] = []
        # M1：动作级重放安全标记（事务引擎据此决定幂等）
        self.idempotent = idempotent

    def add_parameter(self, prop: ToolParameter) -> "ActionType":
        """添加参数"""
        self.parameters[prop.name] = prop
        return self

    def get_parameters(self) -> List[ToolParameter]:
        """列出全部参数定义"""
        return list(self.parameters.values())

    def execute(self, params: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        """执行动作：参数校验 → 规则校验 → 执行 → 副作用 → 审计"""
        errors = []

        # ① 参数必填校验
        for p in self.parameters.values():
            if p.required and (p.name not in params or params[p.name] in (None, "")):
                errors.append(f"缺少必填参数: {p.name}")

        # ② 规则校验
        if not errors:
            for rule in self.rules:
                try:
                    rule_error = rule(params, ctx)
                    if rule_error:
                        errors.append(str(rule_error))
                except Exception as e:
                    errors.append(f"规则校验异常: {e}")

        if errors:
            return {"success": False, "result": None, "errors": errors}

        # ③ 执行
        try:
            if not self.execute_fn:
                raise ValueError(f"动作 '{self.api_name}' 未定义 execute_fn")
            result = self.execute_fn(params, ctx)
        except Exception as e:
            errors.append(f"动作执行失败: {e}")
            self._record_audit(params, ctx, False, errors)
            return {"success": False, "result": None, "errors": errors}

        # ④ 副作用
        side_effect_errors = []
        for effect in self.side_effects:
            try:
                effect(result, ctx)
            except Exception as e:
                side_effect_errors.append(f"副作用异常: {e}")
        errors.extend(side_effect_errors)

        # ⑤ 审计（优先写 ctx 注入的审计管理器，避免治理层重复记录）
        # 主执行成功即算成功，副作用异常仅记录不改变主结果
        self._record_audit(params, ctx, True, errors)

        # 观测埋点：动作指标
        try:
            from agentorchestra.runtime.core.telemetry.metrics import get_metrics
            get_metrics().record_action_execution(self.api_name, error=bool(errors))
        except Exception:
            pass

        return {"success": True, "result": result, "errors": errors}

    def _record_audit(self, params, ctx, success, errors):
        # 若 ctx 注入了治理层审计管理器，写入它（统一审计入口）
        audit_mgr = (ctx or {}).get("audit")
        if audit_mgr is not None:
            try:
                principal = (ctx or {}).get("principal", "unknown")
                audit_mgr.log(
                    principal=principal,
                    resource=self.api_name,
                    action="execute",
                    detail={"params": params, "errors": list(errors)},
                    success=success
                )
                return
            except Exception:
                pass
        # 否则记录到动作自身
        self._audit.append({
            "action": self.api_name,
            "timestamp": datetime.now().isoformat(),
            "params": params,
            "success": success,
            "errors": list(errors),
            "principal": (ctx or {}).get("principal", "unknown"),
        })

    def get_audit(self) -> List[Dict[str, Any]]:
        """返回动作自身的审计记录"""
        return list(self._audit)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "api_name": self.api_name,
            "display_name": self.display_name,
            "description": self.description,
            "parameters": [p.to_dict() for p in self.parameters.values()],
            "rules_count": len(self.rules),
            "side_effects_count": len(self.side_effects),
            "audit_count": len(self._audit),
            "idempotent": self.idempotent,
        }

    def __repr__(self) -> str:
        return f"ActionType({self.api_name})"
