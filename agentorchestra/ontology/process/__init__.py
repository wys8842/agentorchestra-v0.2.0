"""process - 执行编排层（流程/调度/事务）

把单个动作提升为可编排、可回滚的执行能力：
- WorkflowEngine: 多动作组合（顺序/条件/并行）
- Scheduler: 定时触发动作/工作流
- TransactionManager: 动作原子性/补偿（Saga）
"""

from .scheduler import ScheduledTask, Scheduler
from .transaction import CompensatingAction, TransactionManager
from .workflow import ConditionNode, ParallelNode, StepNode, Workflow, WorkflowEngine

__all__ = [
    "WorkflowEngine", "Workflow", "StepNode", "ConditionNode", "ParallelNode",
    "Scheduler", "ScheduledTask",
    "TransactionManager", "CompensatingAction",
]
