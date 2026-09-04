"""ToolGenerator - 对象/动作/函数 → Tool 生成器

与 Agent 解耦的核心：生成标准 Tool，注册进 ToolRegistry 后，
任何 Agent（ReAct/Simple/Reflection/PlanSolve）及子代理自动获得能力。
"""

from typing import Any, Dict, List, Optional

from agentorchestra.capability.tools.base import Tool, ToolParameter
from agentorchestra.capability.tools.errors import ToolErrorCode
from agentorchestra.capability.tools.response import ToolResponse

from .kinetic.action import ActionType
from .kinetic.function import Function
from .semantic.object_type import ObjectType


class ObjectQueryTool(Tool):
    """对象类型查询工具"""

    def __init__(self, object_type: ObjectType, store: Any,
                 security: Any = None, security_ctx: Any = None,
                 audit: Any = None, query_engine: Any = None,
                 name: Optional[str] = None):
        self.object_type = object_type
        self.store = store
        self.security = security
        self.security_ctx = security_ctx
        self.audit = audit
        self.query_engine = query_engine

        super().__init__(
            name=name or f"Query{self._cap(object_type.api_name)}",
            description=self._build_desc(),
            expandable=False,
            read_only=True
        )

    def _build_desc(self) -> str:
        props = ", ".join(p.name for p in self.object_type.get_properties())
        links = ", ".join(link.name for link in self.object_type.get_link_types())
        return (f"查询{self.object_type.display_name}对象。"
                f"模式: get/search/filter/list/aggregate/links。"
                f"属性: {props}。链接: {links or '无'}")

    @staticmethod
    def _cap(name: str) -> str:
        return name[0].upper() + name[1:] if name else ""

    def get_parameters(self) -> List[ToolParameter]:
        """返回查询参数定义"""
        return [
            ToolParameter(name="mode", type="string", description="查询模式: get/search/filter/list/aggregate/links", required=True),
            ToolParameter(name="pk", type="string", description="主键值（get/links 用）", required=False),
            ToolParameter(name="query", type="string", description="搜索关键词（search）", required=False),
            ToolParameter(name="conditions", type="string", description="过滤条件 JSON（filter）", required=False),
            ToolParameter(name="group_by", type="string", description="分组字段（aggregate）", required=False),
            ToolParameter(name="agg", type="string", description="聚合函数（aggregate）", required=False),
            ToolParameter(name="link_name", type="string", description="链接名（links）", required=False),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """按 mode 分发执行对象查询"""
        mode = parameters.get("mode", "list").lower()
        type_name = self.object_type.api_name

        if self.security and self.security_ctx:
            if not self.security.check(type_name, "read", self.security_ctx):
                self._audit("query", parameters, success=False, reason="access_denied")
                return ToolResponse.error(
                    code=ToolErrorCode.ACCESS_DENIED, message=f"无权限读取 {type_name}")

        self._audit("query", parameters, success=True)

        try:
            if mode == "get":
                obj = self.store.get(type_name, parameters.get("pk", ""))
                return ToolResponse.success(
                    text=str(obj) if obj else f"{type_name} 不存在", data=obj or {})
            elif mode == "search":
                rs = self.store.search(type_name, parameters.get("query", ""))
                return ToolResponse.success(text=self._fmt(rs), data={"results": rs})
            elif mode == "filter":
                import json
                conds = json.loads(parameters.get("conditions", "{}"))
                # 委托 QueryEngine（统一过滤/排序/分页语义）
                if self.query_engine is not None:
                    rs = self.query_engine.object_set(
                        type_name, conditions=conds or None, limit=100)
                    objs = rs.get("objects", [])
                else:
                    objs = self.store.filter(type_name, conds)
                return ToolResponse.success(text=self._fmt(objs), data={"results": objs})
            elif mode == "aggregate":
                rs = self.store.aggregate(type_name, parameters.get("group_by", ""),
                                          parameters.get("agg", "count"))
                return ToolResponse.success(text=str(rs), data=rs)
            elif mode == "links":
                rs = self.store.get_links(type_name, parameters.get("pk", ""),
                                          parameters.get("link_name"))
                lines = [f"[{r['link_name']}] -> {r['to_type']}/{r['to_pk']}" for r in rs]
                return ToolResponse.success(text="\n".join(lines) or "无链接", data={"links": rs})
            else:
                # 委托 QueryEngine 统一对象集合语义
                if self.query_engine is not None:
                    rs = self.query_engine.object_set(type_name, limit=100)
                    objs = rs.get("objects", [])
                else:
                    objs = self.store.list_objects(type_name)
                return ToolResponse.success(text=self._fmt(objs), data={"results": objs})
        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=f"查询失败: {str(e)}", context={"type": type_name})

    def _audit(self, action: str, params: Dict[str, Any],
               success: bool, reason: Optional[str] = None):
        """写入审计日志（若装配了 AuditManager）"""
        if self.audit is None:
            return
        principal = self.security_ctx.principal if self.security_ctx else "anonymous"
        try:
            self.audit.log(
                principal=principal,
                resource=self.object_type.api_name,
                action=action,
                detail={"mode": params.get("mode", "list"),
                        **({"reason": reason} if reason else {})},
                success=success
            )
        except Exception:
            pass

    def _fmt(self, objs: List[Dict]) -> str:
        if not objs:
            return "无数据"
        pk = self.object_type.primary_key
        lines = [f"{self.object_type.display_name} ({len(objs)} 条):"]
        for o in objs:
            lines.append(f"  [{o.get(pk, '?')}] {dict(list(o.items())[:3])}")
        return "\n".join(lines)


class ObjectActionTool(Tool):
    """动作类型工具"""

    def __init__(self, action: ActionType, store: Any,
                 security: Any = None, security_ctx: Any = None,
                 audit: Any = None, name: Optional[str] = None):
        self.action = action
        self.store = store
        self.security = security
        self.security_ctx = security_ctx
        self.audit = audit
        super().__init__(
            name=name or action.api_name,
            description=f"执行动作: {action.display_name}。参数: "
                        f"{', '.join(p.name for p in action.get_parameters())}。",
            expandable=False
        )

    def get_parameters(self) -> List[ToolParameter]:
        """返回动作参数定义"""
        return [ToolParameter(name=p.name, type=p.type, description=p.description or p.name,
                              required=p.required, default=p.default)
                for p in self.action.get_parameters()]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """执行动作并返回结果"""
        if self.security and self.security_ctx:
            if not self.security.check(self.action.api_name, "write", self.security_ctx):
                self._audit("execute", parameters, success=False, reason="access_denied")
                return ToolResponse.error(
                    code=ToolErrorCode.ACCESS_DENIED,
                    message=f"无权限执行 {self.action.api_name}")

        ctx = {
            "object_store": self.store,
            "security": self.security_ctx,
            "principal": self.security_ctx.principal if self.security_ctx else "anonymous",
            # 注入审计管理器：由 ActionType.execute 单点记录，避免重复审计
            "audit": self.audit,
        }
        result = self.action.execute(parameters, ctx)

        if not result["success"]:
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR, message="; ".join(result["errors"]))
        return ToolResponse.success(
            text=str(result["result"]),
            data={"action": self.action.api_name, "result": result["result"]})

    def _audit(self, action: str, params: Dict[str, Any],
               success: bool, reason: Optional[str] = None):
        """写入审计日志（若装配了 AuditManager）"""
        if self.audit is None:
            return
        principal = self.security_ctx.principal if self.security_ctx else "anonymous"
        try:
            self.audit.log(
                principal=principal,
                resource=self.action.api_name,
                action=action,
                detail={"params": params, **({"reason": reason} if reason else {})},
                success=success
            )
        except Exception:
            pass


class FunctionCallTool(Tool):
    """函数调用工具"""

    def __init__(self, function: Function, store: Any = None, name: Optional[str] = None):
        self.function = function
        self.store = store
        super().__init__(
            name=name or f"Call{self._camelize(function.api_name)}",
            description=f"调用函数: {function.display_name}。返回 {function.return_type}。",
            expandable=False,
            read_only=True
        )

    @staticmethod
    def _camelize(name: str) -> str:
        return "".join(p[0].upper() + p[1:] for p in name.split("_") if p)

    def get_parameters(self) -> List[ToolParameter]:
        """返回函数参数定义"""
        return [ToolParameter(name=a.name, type=a.type, description=a.description or a.name,
                              required=a.required, default=a.default)
                for a in self.function.get_arguments()]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """调用函数并返回结果"""
        try:
            result = self.function.call(parameters, {"object_store": self.store})
            return ToolResponse.success(
                text=str(result), data={"function": self.function.api_name, "result": result})
        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR, message=f"函数调用失败: {str(e)}")


class ToolGenerator:
    """工具生成器"""

    def __init__(self, store: Any = None, security: Any = None,
                 security_ctx: Any = None, audit: Any = None,
                 query_engine: Any = None):
        self.store = store
        self.security = security
        self.security_ctx = security_ctx
        self.audit = audit
        self.query_engine = query_engine

    def object_query_tool(self, object_type: ObjectType) -> Tool:
        """生成对象类型查询 Tool"""
        return ObjectQueryTool(object_type, self.store, self.security,
                               self.security_ctx, self.audit, self.query_engine)

    def action_tool(self, action: ActionType) -> Tool:
        """生成动作执行 Tool"""
        return ObjectActionTool(action, self.store, self.security,
                                self.security_ctx, self.audit)

    def function_tool(self, function: Function) -> Tool:
        """生成函数调用 Tool"""
        return FunctionCallTool(function, self.store)
