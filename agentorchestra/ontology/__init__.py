"""ontology - 业务语义与对象操作框架

为 agentorchestra 提供本体与知识图谱能力，作为 Agent 的外部大脑。

分层架构：
- semantic: 语义层（ObjectType/LinkType/Interface）
- kinetic: 动能层（ActionType/Function）
- storage: 存储层（ObjectStore/GraphStore/索引/物化）
- governance: 治理层（安全/审计/分支）
- query_engine: 查询引擎（跨对象查询）
- engine: OntologyEngine 统一入口（解耦挂载到任意 Agent）
- tool_generator: 对象/动作/函数 → Tool 自动生成

用法：
    1. 定义对象类型/动作/函数/接口
    2. 注册到 OntologyEngine
    3. engine.mount(agent.tool_registry) → 自动生成 Tool，任何 Agent 可用
"""

from .engine import OntologyEngine
from .governance import (
    AuditManager,
    BranchManager,
    PermissionRule,
    SecurityContext,
    SecurityManager,
)
from .kinetic import ActionType, Function, derived_property
from .process import (
    CompensatingAction,
    ConditionNode,
    ParallelNode,
    ScheduledTask,
    Scheduler,
    StepNode,
    TransactionManager,
    Workflow,
    WorkflowEngine,
)
from .query_engine import QueryEngine
from .semantic import Interface, LinkType, ObjectType, VocabularyValidator
from .storage import (
    BaseStorageBackend,
    GraphStore,
    MaterializationManager,
    MaterializationTarget,
    MemoryBackend,
    ObjectIndex,
    ObjectStore,
    SQLiteBackend,
)
from .tool_generator import FunctionCallTool, ObjectActionTool, ObjectQueryTool, ToolGenerator

__all__ = [
    # semantic
    "ObjectType", "LinkType", "Interface", "VocabularyValidator",
    # kinetic
    "ActionType", "Function", "derived_property",
    # storage
    "ObjectStore", "GraphStore", "ObjectIndex", "MaterializationManager",
    "MaterializationTarget",
    "BaseStorageBackend", "MemoryBackend", "SQLiteBackend",
    # governance
    "SecurityContext", "SecurityManager", "PermissionRule", "AuditManager",
    "BranchManager",
    # query
    "QueryEngine",
    # process（流程编排/调度/事务）
    "WorkflowEngine", "Workflow", "StepNode", "ConditionNode", "ParallelNode",
    "Scheduler", "ScheduledTask",
    "TransactionManager", "CompensatingAction",
    # tools
    "ToolGenerator", "ObjectQueryTool", "ObjectActionTool", "FunctionCallTool",
    # engine
    "OntologyEngine",
]
