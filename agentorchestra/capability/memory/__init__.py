"""Memory 子系统 - 跨会话持久记忆

按设计 docs/superpowers/specs/2026-09-03-memory-system-design.md 实现。

模块：
- models: MemoryType 枚举 + MemoryEntry 数据模型
- storage: 存储后端（InMemory / Jsonl / SQLite）+ MemoryStore 包装
- embedder: 复用 SymphonyLLM 的 embedding 封装（失败降级）
- index: 关键词倒排索引 + 混合检索器
- manager: MemoryManager 统一入口（Agent 挂载点）
- tools: MemorySaveTool / MemoryRecallTool
- summarizer: 会话总结 → 候选记忆条目
"""

from .manager import MemoryManager
from .models import MemoryEntry, MemoryType

__all__ = ["MemoryEntry", "MemoryType", "MemoryManager"]
