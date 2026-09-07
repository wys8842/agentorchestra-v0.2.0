"""OntologyEngine - Ontology 统一入口

组织运营语义层：
- 注册对象类型 / 动作 / 函数 / 接口
- 管理对象存储（ObjectStore + ObjectIndex + GraphStore）
- 治理（安全 + 审计 + 分支 + 物化）
- 查询引擎（跨对象查询）
- 自动生成 Tool（ToolGenerator）
- 解耦挂载：mount(registry) 接受任意 ToolRegistry，不依赖任何 Agent 类型
"""

from typing import Any, Dict, List, Optional

from agentorchestra.capability.tools.base import Tool
from agentorchestra.capability.tools.registry import ToolRegistry

from .governance.audit import AuditManager
from .governance.branching import BranchManager
from .governance.security import SecurityContext, SecurityManager
from .kinetic.action import ActionType
from .kinetic.function import Function
from .query_engine import QueryEngine
from .semantic.interface import Interface
from .semantic.object_type import ObjectType
from .storage.materialization import MaterializationManager, MaterializationTarget
from .storage.object_store import ObjectStore
from .tool_generator import ToolGenerator


class OntologyEngine:
    """Ontology 引擎（统一注册/存储/治理/查询/工具生成）"""

    def __init__(
        self,
        object_store: Optional[ObjectStore] = None,
        security_ctx: Optional[SecurityContext] = None,
    ):
        # 治理层
        self.security = SecurityManager()
        self.audit = AuditManager()
        self.branching = BranchManager()
        self.materialization = MaterializationManager()

        # 存储层（注入物化管理器，写操作触发物化回写）
        self.object_store = object_store or ObjectStore(
            materializer=self.materialization)
        # 调用方传入的 store 也补挂物化（若尚未配置）
        if getattr(self.object_store, 'materializer', None) is None:
            self.object_store.materializer = self.materialization
        self.graph_store = self.object_store.graph
        self.security_ctx = security_ctx or SecurityContext()

        # 语义注册表
        self.object_types: Dict[str, ObjectType] = {}
        self.actions: Dict[str, ActionType] = {}
        self.functions: Dict[str, Function] = {}
        self.interfaces: Dict[str, Interface] = {}

        # 查询引擎
        self.query = QueryEngine(self.object_store)

        # 执行编排层（流程/调度/事务）
        from .process.scheduler import Scheduler
        from .process.transaction import TransactionManager
        from .process.workflow import WorkflowEngine
        self.workflow = WorkflowEngine(self.actions)
        self.scheduler = Scheduler()
        self.transaction = TransactionManager()

        # 统一词汇校验器
        from .semantic.vocabulary import VocabularyValidator
        self.vocabulary = VocabularyValidator(self.object_types)

        # 工具生成器（注入审计 + 查询引擎，使工具执行产生审计并复用统一查询语义）
        self.tool_generator = ToolGenerator(
            store=self.object_store,
            security=self.security,
            security_ctx=self.security_ctx,
            audit=self.audit,
            query_engine=self.query,
        )

    # ==================== 语义注册 ====================

    def register_object_type(self, object_type: ObjectType) -> "OntologyEngine":
        """注册对象类型（对象存储 + 词汇校验器同步）"""
        self.object_types[object_type.api_name] = object_type
        self.object_store.register_type(object_type)
        # 同步词汇校验器（引用同一注册表，直接更新）
        self.vocabulary.object_types = self.object_types
        return self

    # ==================== 统一词汇校验 ====================

    def validate_triple(self, from_type: str, link_name: str, to_type: str) -> bool:
        """校验三元组"""
        return self.vocabulary.validate_link(from_type, link_name, to_type)

    def unknown_properties(self, type_name: str, obj: Dict[str, Any]) -> List[str]:
        """返回未声明属性（统一词汇强制）"""
        return self.vocabulary.unknown_properties(type_name, obj)

    # ==================== 类层次 ====================

    def get_subclasses(self, type_name: str, transitive: bool = True) -> List[str]:
        """获取子类型（类层次， get_subclasses）"""
        return self.object_store.get_subclasses(type_name, transitive)

    def get_superclasses(self, type_name: str) -> List[str]:
        """获取父类型链"""
        return self.object_store.get_superclasses(type_name)

    def register_action(self, action: ActionType) -> "OntologyEngine":
        """注册动作（同步到工作流引擎）"""
        self.actions[action.api_name] = action
        # 同步到流程引擎（工作流可引用该动作）
        self.workflow.register_action(action)
        return self

    def register_function(self, function: Function) -> "OntologyEngine":
        """注册函数"""
        self.functions[function.api_name] = function
        return self

    def register_interface(self, interface: Interface) -> "OntologyEngine":
        """注册接口"""
        self.interfaces[interface.api_name] = interface
        return self

    def implement_interface(self, interface_name: str, object_type_name: str) -> None:
        """让对象类型实现接口（属性不满足则报错）"""
        interface = self.interfaces.get(interface_name)
        obj_type = self.object_types.get(object_type_name)
        if not interface or not obj_type:
            raise ValueError("接口或对象类型不存在")
        if not interface.check_implements(obj_type):
            missing = set(interface.required_properties) - set(obj_type.properties.keys())
            raise ValueError(f"对象类型 {object_type_name} 缺少接口属性: {missing}")
        interface.register_implementation(object_type_name)

    # ==================== 治理 ====================

    def allow(self, roles: List[str], resource: str = "*", action: str = "*") -> None:
        """配置安全规则：允许角色对资源执行动作"""
        self.security.allow(roles, resource, action)

    def register_materialization(self, target: MaterializationTarget) -> None:
        """注册物化目标"""
        self.materialization.register_target(target)

    def snapshot_branch(self, name: str) -> bool:
        """创建分支快照并返回是否成功"""
        self.branching.create_branch(name, self.object_store)
        return True

    def switch_branch(self, name: str) -> bool:
        """切换到指定分支"""
        return self.branching.switch_to(name, self.object_store)

    # ==================== 工具生成与挂载（解耦核心） ====================

    def build_tools(self) -> List[Tool]:
        """为全部对象类型/动作/函数生成 Tool"""
        tools: List[Tool] = []
        for obj_type in self.object_types.values():
            tools.append(self.tool_generator.object_query_tool(obj_type))
        for action in self.actions.values():
            tools.append(self.tool_generator.action_tool(action))
        for function in self.functions.values():
            tools.append(self.tool_generator.function_tool(function))
        return tools

    def mount(self, registry: ToolRegistry) -> List[str]:
        """挂载到任意 ToolRegistry（与 Agent 类型解耦）"""
        tools = self.build_tools()
        for tool in tools:
            registry.register_tool(tool)
        return [t.name for t in tools]

    # ==================== 工具 ====================

    def describe(self) -> str:
        """返回引擎能力清单文本"""
        return (f"OntologyEngine 能力清单:\n"
                f"  对象类型 ({len(self.object_types)}): {', '.join(self.object_types) or '无'}\n"
                f"  动作 ({len(self.actions)}): {', '.join(self.actions) or '无'}\n"
                f"  函数 ({len(self.functions)}): {', '.join(self.functions) or '无'}\n"
                f"  接口 ({len(self.interfaces)}): {', '.join(self.interfaces) or '无'}")

    def stats(self) -> Dict[str, Any]:
        """返回引擎统计信息"""
        return {
            "object_types": len(self.object_types),
            "actions": len(self.actions),
            "functions": len(self.functions),
            "interfaces": len(self.interfaces),
            "tools": len(self.object_types) + len(self.actions) + len(self.functions),
            "storage": self.object_store.stats(),
            "audit_entries": self.audit.count(),
        }

    def get_health(self) -> Dict[str, Any]:
        """健康检查报告"""
        try:
            storage_stats = self.object_store.stats()
        except Exception as e:
            storage_stats = {"error": str(e)}

        return {
            "name": "ontology_engine",
            "status": "ok",
            "checks": [
                {"name": "object_types", "status": "ok",
                 "detail": {"count": len(self.object_types)}},
                {"name": "actions", "status": "ok",
                 "detail": {"count": len(self.actions)}},
                {"name": "functions", "status": "ok",
                 "detail": {"count": len(self.functions)}},
                {"name": "storage", "status": "ok",
                 "detail": storage_stats},
            ],
        }
