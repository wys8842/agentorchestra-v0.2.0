"""Memory 工具：MemorySaveTool / MemoryRecallTool

设计：与 SkillTool 等其他内置工具同接口（继承 Tool），
可注册到任意 ToolRegistry，Agent 自动可用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..tools.base import Tool, ToolParameter
from ..tools.errors import ToolErrorCode
from ..tools.response import ToolResponse
from .manager import MemoryManager
from .models import MemoryEntry, MemoryType


class MemorySaveTool(Tool):
    """把一条信息记入长期记忆。

    参数:
        content: 要记的内容（必填）
        type: 类型（fact/preference/episode/procedure），默认 fact
        tags: 逗号分隔的标签，可选
        importance: 0~1，默认 0.5
    """

    name = "memory_save"
    description = (
        "把一条信息记入长期记忆（跨会话持久化）。"
        "适用于用户偏好、项目事实、过往事件、方法经验。"
    )
    expandable = False

    def __init__(self, manager: MemoryManager) -> None:
        super().__init__(name=self.name, description=self.description, expandable=False, read_only=False)
        self.manager = manager

    def get_parameters(self) -> List[ToolParameter]:
        """声明工具参数（content/type/tags/importance/namespace）。"""
        return [
            ToolParameter(
                name="content", type="string",
                description="要记的内容", required=True,
            ),
            ToolParameter(
                name="type", type="string",
                description="记忆类型：fact/preference/episode/procedure",
                required=False, default="fact",
            ),
            ToolParameter(
                name="tags", type="string",
                description="逗号分隔的标签（如: 'Python,Symphony'）",
                required=False, default="",
            ),
            ToolParameter(
                name="importance", type="number",
                description="重要性 0~1，默认 0.5",
                required=False, default="0.5",
            ),
            ToolParameter(
                name="namespace", type="string",
                description="命名空间（默认 'default'），用于隔离不同用户/Agent 的记忆",
                required=False, default="default",
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """执行记忆保存（校验参数后调用 manager.remember）。"""
        content = parameters.get("content")
        if not content or not str(content).strip():
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="content 不能为空",
            )
        type_str = parameters.get("type", "fact") or "fact"
        try:
            type_enum = MemoryType(type_str)
        except ValueError:
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message=f"未知的记忆类型: {type_str}（应为 fact/preference/episode/procedure）",
            )

        tags_raw = parameters.get("tags", "") or ""
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

        try:
            importance = float(parameters.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        importance = max(0.0, min(1.0, importance))

        ns = str(parameters.get("namespace", "") or "") or "default"

        try:
            entry_id = self.manager.remember(
                content=str(content).strip(),
                type=type_enum,
                tags=tags,
                importance=importance,
                namespace=ns,
            )
        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=f"记忆保存失败: {e}",
            )

        return ToolResponse.success(
            text=f"记忆已保存（type={type_enum.value}, namespace={ns}）：id={entry_id[:8]}",
            data={
                "entry_id": entry_id,
                "type": type_enum.value,
                "namespace": ns,
                "tags": tags,
                "importance": importance,
            },
        )


class MemoryRecallTool(Tool):
    """从长期记忆中按 query 检索相关条目。

    参数:
        query: 查询文本（必填）
        type: 类型过滤，可选（fact/preference/episode/procedure，空表示全部）
        top_k: 返回数量，默认 5
    """

    name = "memory_recall"
    description = (
        "从长期记忆（跨会话持久化）中按 query 检索。"
        "可返回用户偏好、项目事实、过往事件、方法经验等。"
    )
    expandable = False

    def __init__(self, manager: MemoryManager) -> None:
        super().__init__(name=self.name, description=self.description, expandable=False, read_only=True)
        self.manager = manager

    def get_parameters(self) -> List[ToolParameter]:
        """声明工具参数（query/type/top_k/namespace）。"""
        return [
            ToolParameter(
                name="query", type="string",
                description="查询文本", required=True,
            ),
            ToolParameter(
                name="type", type="string",
                description="类型过滤（fact/preference/episode/procedure），空表示全部",
                required=False, default="",
            ),
            ToolParameter(
                name="top_k", type="number",
                description="返回数量，默认 5",
                required=False, default="5",
            ),
            ToolParameter(
                name="namespace", type="string",
                description="命名空间（默认 'default'），用于隔离不同用户/Agent 的记忆",
                required=False, default="default",
            ),
        ]

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """执行记忆检索（校验参数后调用 manager.recall）。"""
        query = parameters.get("query")
        if not query or not str(query).strip():
            return ToolResponse.error(
                code=ToolErrorCode.INVALID_PARAM,
                message="query 不能为空",
            )

        type_filter: Optional[List[MemoryType]] = None
        type_str = parameters.get("type", "") or ""
        if type_str:
            try:
                type_filter = [MemoryType(type_str)]
            except ValueError:
                return ToolResponse.error(
                    code=ToolErrorCode.INVALID_PARAM,
                    message=f"未知的记忆类型: {type_str}",
                )

        try:
            top_k = int(parameters.get("top_k", 5))
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(50, top_k))

        ns = str(parameters.get("namespace", "") or "") or "default"

        try:
            entries: List[MemoryEntry] = self.manager.recall(
                query=str(query).strip(),
                top_k=top_k,
                types=type_filter,
                namespace=ns,
            )
        except Exception as e:
            return ToolResponse.error(
                code=ToolErrorCode.EXECUTION_ERROR,
                message=f"记忆检索失败: {e}",
            )

        if not entries:
            return ToolResponse.success(
                text="（无相关记忆）",
                data={"count": 0, "entries": []},
            )

        lines = [f"找到 {len(entries)} 条相关记忆："]
        for i, entry_obj in enumerate(entries, 1):
            entry_type = entry_obj.type
            type_val = entry_type.value if isinstance(entry_type, MemoryType) else str(entry_type)
            tag_list = entry_obj.tags
            tag_str = ", ".join(tag_list) if tag_list else "-"
            lines.append(
                f"{i}. [{type_val}] (importance={entry_obj.importance:.1f}, tags=[{tag_str}]) {entry_obj.content}"
            )
        text = "\n".join(lines)
        return ToolResponse.success(
            text=text,
            data={
                "count": len(entries),
                "entries": [e.to_dict() for e in entries],
            },
        )
