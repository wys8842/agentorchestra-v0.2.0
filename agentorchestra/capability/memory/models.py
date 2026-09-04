"""记忆数据模型

- MemoryType: 4 种记忆类型（fact/preference/episode/procedure）
- MemoryEntry: 一条记忆的数据模型（content/tags/embedding/元数据）
- now_iso: ISO8601 UTC 时间戳辅助
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def now_iso() -> str:
    """返回 UTC ISO8601 时间戳字符串。"""
    return datetime.now(timezone.utc).isoformat()


def gen_id() -> str:
    """生成记忆条目 ID（32 字符十六进制）。"""
    return uuid.uuid4().hex


class MemoryType(str, Enum):
    """记忆类型（统一条目+类型标签设计）。

    字段值固定为字符串，便于 JSON 序列化与配置切换。
    """

    FACT = "fact"          # 持久事实：用户/项目/环境
    PREFERENCE = "preference"  # 用户偏好与约定
    EPISODE = "episode"    # 情景记忆：做过的任务/事件
    PROCEDURE = "procedure"  # 方法/流程经验（与 skills 互补）


@dataclass
class MemoryEntry:
    """记忆条目数据模型。

    字段语义：
    - id: 唯一标识（uuid4 hex）
    - type: 记忆类型
    - content: 文本内容（必填）
    - tags: 标签列表，便于关键词命中加权
    - importance: 重要性 0~1
    - embedding: 与 content 同步的向量；存于单独表/文件，不参与 to_dict 默认序列化
    - source_session: 来源会话 ID（仅元数据，不参与隔离）
    - source_agent: 来源 Agent 名
    - created_at / updated_at / last_accessed_at: ISO8601 UTC
    - access_count: 被 recall 命中的次数
    """

    id: str = field(default_factory=gen_id)
    type: MemoryType = MemoryType.FACT
    content: str = ""
    tags: List[str] = field(default_factory=list)
    importance: float = 0.5
    embedding: Optional[List[float]] = None
    source_session: str = ""
    source_agent: str = ""
    namespace: str = "default"  # v1.1: 命名空间，默认 "default"（向后兼容）
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    access_count: int = 0
    last_accessed_at: Optional[str] = None

    def touch(self) -> None:
        """更新 updated_at 为当前时间。"""
        self.updated_at = now_iso()

    def accessed(self) -> None:
        """更新访问元数据：计数+1、最近访问时间。"""
        self.access_count += 1
        self.last_accessed_at = now_iso()

    def to_dict(self, include_embedding: bool = False) -> Dict[str, Any]:
        """序列化为字典。

        Args:
            include_embedding: 是否包含 embedding 向量（默认 False，
                因为向量通常单独存储到 embeddings 表/文件）。
        """
        d: Dict[str, Any] = {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, MemoryType) else str(self.type),
            "content": self.content,
            "tags": list(self.tags),
            "importance": float(self.importance),
            "source_session": self.source_session,
            "source_agent": self.source_agent,
            "namespace": self.namespace,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "access_count": int(self.access_count),
            "last_accessed_at": self.last_accessed_at,
        }
        if include_embedding and self.embedding is not None:
            d["embedding"] = list(self.embedding)
        return d

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        embedding: Optional[List[float]] = None,
    ) -> "MemoryEntry":
        """从字典反序列化。embedding 由调用方单独提供（来自 embeddings 存储）。"""
        type_value = data.get("type", MemoryType.FACT.value)
        if isinstance(type_value, MemoryType):
            type_enum = type_value
        else:
            try:
                type_enum = MemoryType(type_value)
            except ValueError:
                type_enum = MemoryType.FACT
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        return cls(
            id=data.get("id") or gen_id(),
            type=type_enum,
            content=data.get("content", "") or "",
            tags=list(tags),
            importance=float(data.get("importance", 0.5) or 0.0),
            embedding=embedding,
            source_session=data.get("source_session", "") or "",
            source_agent=data.get("source_agent", "") or "",
            namespace=data.get("namespace", "") or "default",
            created_at=data.get("created_at") or now_iso(),
            updated_at=data.get("updated_at") or now_iso(),
            access_count=int(data.get("access_count", 0) or 0),
            last_accessed_at=data.get("last_accessed_at"),
        )
