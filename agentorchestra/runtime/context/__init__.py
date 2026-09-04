"""上下文工程模块

为Symphony框架提供上下文工程能力：
- ContextBuilder: GSSC流水线（Gather-Select-Structure-Compress）
- HistoryManager: 历史管理与压缩
- ObservationTruncator: 工具输出截断
- TokenCounter: Token 计数器（缓存 + 增量计算）
- Compactor: 对话压缩整合
- NotesManager: 结构化笔记管理
- ContextObserver: 可观测性与指标追踪
"""

from .builder import ContextBuilder, ContextConfig, ContextPacket
from .history import HistoryManager
from .token_counter import TokenCounter
from .truncator import ObservationTruncator

__all__ = [
    "ContextBuilder",
    "ContextConfig",
    "ContextPacket",
    "HistoryManager",
    "ObservationTruncator",
    "TokenCounter",
]

