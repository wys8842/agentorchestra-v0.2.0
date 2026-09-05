"""Tools 模块单元测试"""

import pytest
from agentorchestra.capability.tools.registry import ToolRegistry
from agentorchestra.capability.tools.base import Tool
from agentorchestra.capability.tools.response import ToolResponse, ToolStatus


class TestToolRegistry:
    """工具注册表测试"""

    def test_registry_creation(self):
        """测试注册表创建"""
        registry = ToolRegistry()
        assert registry is not None
        assert len(registry.list_tools()) == 0

    def test_register_tool(self):
        """测试工具注册"""
        registry = ToolRegistry()

        class TestTool(Tool):
            name = "test_tool"
            description = "A test tool"

            def execute(self, **kwargs):
                return "test result"

        registry.register_tool(TestTool())
        tools = registry.list_tools()
        assert len(tools) > 0

    def test_get_tool(self):
        """测试获取工具"""
        registry = ToolRegistry()

        class TestTool(Tool):
            name = "get_test"
            description = "A test tool"

            def execute(self, **kwargs):
                return "result"

        registry.register_tool(TestTool())
        tool = registry.get_tool("get_test")
        assert tool is not None
        assert tool.name == "get_test"


class TestToolResponse:
    """工具响应测试"""

    def test_tool_response_success(self):
        """测试成功响应"""
        response = ToolResponse(text="Success result", status=ToolStatus.SUCCESS)
        assert response.status == ToolStatus.SUCCESS
        assert response.text == "Success result"

    def test_tool_response_error(self):
        """测试错误响应"""
        response = ToolResponse(
            text="Error occurred",
            status=ToolStatus.ERROR,
            error_info={"code": "TEST_ERROR"}
        )
        assert response.status == ToolStatus.ERROR
        assert response.error_info["code"] == "TEST_ERROR"

    def test_tool_response_partial(self):
        """测试部分成功响应"""
        response = ToolResponse(text="Partial result", status=ToolStatus.PARTIAL)
        assert response.status == ToolStatus.PARTIAL
