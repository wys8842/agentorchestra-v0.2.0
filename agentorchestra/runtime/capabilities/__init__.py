"""capabilities - Agent 可插拔能力（Phase 2：拆分上帝对象）

设计：
- 每项 feature（trace / skills / mcp / ontology / session / memory / subagent / todowrite /
  devlog / checkpoint / snapshot / smart_compression / context_builder）实现为独立 Capability
- Agent.__init__ 仅做依赖注入与 capability 编排，业务逻辑下放到各 Capability
- capability 无需修改 Agent 类
"""

from .base import Capability, CapabilityContext
from .registry import CapabilityRegistry

__all__ = ["Capability", "CapabilityContext", "CapabilityRegistry"]
