"""Function - 函数

动能层：业务逻辑，任意复杂度。
- Function: 带参数和返回值的业务逻辑
- derived_property: 从对象现有属性计算派生属性
"""

from typing import Any, Callable, Dict, List, Optional

from agentorchestra.capability.tools.base import ToolParameter


class Function:
    """函数定义"""

    def __init__(
        self,
        api_name: str,
        impl: Callable,
        arguments: Optional[List[ToolParameter]] = None,
        return_type: str = "string",
        description: str = "",
        display_name: Optional[str] = None,
    ):
        self.api_name = api_name
        self.display_name = display_name or api_name
        self.description = description
        self.return_type = return_type
        self.impl = impl
        self.arguments: Dict[str, ToolParameter] = {}
        if arguments:
            for a in arguments:
                self.arguments[a.name] = a

    def get_arguments(self) -> List[ToolParameter]:
        """列出全部参数定义"""
        return list(self.arguments.values())

    def call(self, args: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Any:
        """调用底层实现并返回结果"""
        return self.impl(args or {}, ctx or {})

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "api_name": self.api_name,
            "display_name": self.display_name,
            "description": self.description,
            "return_type": self.return_type,
            "arguments": [a.to_dict() for a in self.arguments.values()],
        }

    def __repr__(self) -> str:
        return f"Function({self.api_name}) -> {self.return_type}"


def derived_property(api_name: str, impl: Callable, property_type: str = "string",
                     description: str = "") -> "Function":
    """创建派生属性函数

    派生属性 = 从对象现有属性计算出的新属性。
    参数约定：impl 接收对象 dict，返回派生值。
    """

    def _derived_impl(args, ctx):
        obj = args.get("object") or {}
        return impl(obj)

    return Function(
        api_name=api_name,
        impl=_derived_impl,
        arguments=[ToolParameter(name="object", type="object", description="源对象")],
        return_type=property_type,
        description=description,
    )
