"""ObjectType - 对象类型定义

语义层核心：把数据源映射为业务对象。
- ObjectType: 业务对象类型（客户/订单/设备），含主键、属性、链接
- 属性统一使用框架的 ToolParameter（与工具体系参数一致）
- 派生属性通过 derived_properties 集合标记（值由 Function 计算）
- 对象间关系见 LinkType（已拆分到 link_type.py）
"""

from typing import Any, Dict, List, Optional, Set

from agentorchestra.capability.tools.base import ToolParameter

from .link_type import LinkType


class ObjectType:
    """对象类型定义"""

    # M3（P3）：系统保留字段（由 ObjectStore 自动注入/维护，不属业务属性）
    SYSTEM_FIELDS = {"version", "created_tx", "last_modified_tx"}

    def __init__(
        self,
        api_name: str,
        primary_key: str,
        properties: Optional[List[ToolParameter]] = None,
        link_types: Optional[List[LinkType]] = None,
        display_name: Optional[str] = None,
        description: str = "",
        parent_type: Optional[str] = None,
        derived_properties: Optional[List[str]] = None,
    ):
        self.api_name = api_name
        self.primary_key = primary_key
        self.display_name = display_name or api_name
        self.description = description
        self.parent_type = parent_type  # 父对象类型（类层次）

        # 派生属性名集合（值由 Function 计算，不可直接写）
        self.derived_properties: Set[str] = set(derived_properties or [])

        # 属性索引（name -> ToolParameter）
        self.properties: Dict[str, ToolParameter] = {}
        if properties:
            for p in properties:
                self.properties[p.name] = p

        # 链接索引
        self.link_types: Dict[str, LinkType] = {}
        if link_types:
            for link in link_types:
                self.link_types[link.name] = link

    # ==================== 属性 ====================

    def add_property(self, prop: ToolParameter) -> "ObjectType":
        """添加属性"""
        self.properties[prop.name] = prop
        return self

    def get_property(self, name: str) -> Optional[ToolParameter]:
        """按名称获取属性定义"""
        return self.properties.get(name)

    def get_properties(self) -> List[ToolParameter]:
        """列出全部属性定义"""
        return list(self.properties.values())

    def required_properties(self) -> List[ToolParameter]:
        """必填属性（有默认值的不算必填）"""
        return [p for p in self.properties.values()
                if p.required and p.default is None]

    def writable_properties(self) -> List[ToolParameter]:
        """非派生属性（可写）"""
        return [p for p in self.properties.values()
                if p.name not in self.derived_properties]

    def is_derived(self, prop_name: str) -> bool:
        """判断属性是否为派生属性"""
        return prop_name in self.derived_properties

    def add_derived_property(self, name: str) -> "ObjectType":
        """将属性标记为派生（值由 Function 计算，不可直接写）"""
        self.derived_properties.add(name)
        return self

    # ==================== 链接 ====================

    def add_link_type(self, link: LinkType) -> "ObjectType":
        """添加链接类型"""
        self.link_types[link.name] = link
        return self

    def get_link_type(self, name: str) -> Optional[LinkType]:
        """按名称获取链接类型"""
        return self.link_types.get(name)

    def get_link_types(self) -> List[LinkType]:
        """列出全部链接类型"""
        return list(self.link_types.values())

    # ==================== 校验 ====================

    def validate_object(self, obj: Dict[str, Any]) -> List[str]:
        """校验对象数据：主键 + 必填 + 类型 + 未声明属性"""
        errors = []

        if self.primary_key not in obj or obj[self.primary_key] in (None, ""):
            errors.append(f"缺少主键: {self.primary_key}")

        for p in self.required_properties():
            if p.name not in obj or obj[p.name] in (None, ""):
                errors.append(f"缺少必填属性: {p.name}")

        for p in self.get_properties():
            if p.name in obj and obj[p.name] is not None:
                if not self._valid_type(p.type, obj[p.name]):
                    errors.append(f"属性 '{p.name}' 类型错误，期望 {p.type}")

        # 统一词汇强制：拒绝未声明的属性（豁免系统保留字段）
        for key in obj:
            if key not in self.properties and key not in self.SYSTEM_FIELDS:
                errors.append(f"属性 '{key}' 未在对象类型 '{self.api_name}' 中定义")

        return errors

    def unknown_properties(self, obj: Dict[str, Any]) -> List[str]:
        """返回对象中未声明的属性名（统一词汇校验；豁免系统保留字段）"""
        return [k for k in obj if k not in self.properties
                and k not in self.SYSTEM_FIELDS]

    # ==================== 类层次 ====================

    def is_subclass_of(self, other_type: str, type_registry: Optional[Dict[str, "ObjectType"]] = None) -> bool:
        """判断当前类型是否为指定类型的子类（含多级）"""
        current_name = self.parent_type
        visited = set()
        while current_name:
            if current_name in visited:
                return False  # 防循环
            visited.add(current_name)
            if current_name == other_type:
                return True
            parent = (type_registry or {}).get(current_name)
            current_name = parent.parent_type if parent else None
        return False

    def _valid_type(self, prop_type: str, value: Any) -> bool:
        type_map: Dict[str, type | tuple[type, ...]] = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
        }
        expected = type_map.get(prop_type)
        if expected is None:
            return True  # datetime/array/object 宽松
        return isinstance(value, expected)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "api_name": self.api_name,
            "display_name": self.display_name,
            "primary_key": self.primary_key,
            "description": self.description,
            "parent_type": self.parent_type,
            "properties": [p.model_dump() if hasattr(p, 'model_dump') else {
                "name": p.name, "type": p.type, "description": p.description,
                "required": p.required, "default": p.default
            } for p in self.properties.values()],
            "link_types": [link.to_dict() for link in self.link_types.values()],
        }

    def __repr__(self) -> str:
        return f"ObjectType({self.api_name}, pk={self.primary_key})"
