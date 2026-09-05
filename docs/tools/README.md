# Tools 模块

## 概述

Tools 模块提供工具系统：Tool 基类、注册表、内置工具、熔断器等。

## 组件

### ToolRegistry

工具注册表：

```python
from agentorchestra.capability.tools import ToolRegistry

registry = ToolRegistry()

# 注册工具
registry.register_tool(MyTool())

# 列出工具
tools = registry.list_tools()

# 获取工具
tool = registry.get_tool("tool_name")

# 执行工具
response = registry.execute_tool("tool_name", "参数")

# 异步执行
response = await registry.async_execute_tool("tool_name", "参数")
```

### Tool 基类

```python
from agentorchestra.capability.tools.base import Tool, ToolParameter

class MyTool(Tool):
    name = "my_tool"
    description = "我的工具"
    
    parameters = [
        ToolParameter(name="arg1", type="string", required=True)
    ]
    
    def execute(self, arg1):
        return f"结果: {arg1}"
```

### ToolResponse

工具响应：

```python
from agentorchestra.capability.tools import ToolResponse, ToolStatus

response = ToolResponse(
    text="结果文本",
    status=ToolStatus.SUCCESS  # SUCCESS/ERROR/PARTIAL
)
```

### 熔断器

```python
from agentorchestra.capability.tools import CircuitBreaker

breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=60
)

# 自动熔断保护
```

## 内置工具

### Calculator

计算器：

```python
from agentorchestra.capability.tools import calculate

result = calculate("1 + 2 * 3")  # 7
```

### FileTools

文件操作：

```python
from agentorchestra.capability.tools import ReadFileTool, WriteFileTool, ListDirTool
```

### MCP Tools

MCP 协议工具：

```python
from agentorchestra.capability.tools import MCPTool
```

## 工具过滤

```python
# 临时禁用工具
agent.temporary_tool_filter(exclude=["dangerous_tool"])

# 恢复工具
agent.restore_tools()
```
