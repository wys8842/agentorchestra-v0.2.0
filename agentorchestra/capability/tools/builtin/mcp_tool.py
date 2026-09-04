"""MCP Tool - Model Context Protocol 工具适配

通过 MCP (Model Context Protocol) 将外部能力（本地进程/远程服务）封装为
标准 Tool，供 Agent 通过 Function Calling 调用。

特性：
- 支持 stdio 传输（本地子进程）和 http 传输（远程服务）
- 自动从 mcp.json 配置读取 server 连接信息
- 每个 MCP server 的工具自动注册为独立 Tool 实例
- server 名前缀防止工具重名冲突
- 连接常驻后台事件循环，多次调用复用同一 session
- 延迟导入 mcp 依赖（未安装时不影响框架其他功能）

使用示例：
    >>> from agentorchestra.tools.builtin.mcp_tool import MCPServerManager
    >>> manager = MCPServerManager(config_file="mcp.json")
    >>> tools = manager.connect_all()
    >>> for tool in tools:
    ...     registry.register_tool(tool)
"""

import asyncio
import threading
from typing import Any, Dict, List, Optional

from agentorchestra.runtime.core.utils import safe_json_load

from ..base import Tool, ToolParameter
from ..errors import ToolErrorCode
from ..response import ToolResponse


def _require_mcp():
    """延迟导入 mcp SDK，未安装时抛出明确错误"""
    try:
        import mcp
        return mcp
    except ImportError as e:
        raise ImportError(
            "使用 MCP 工具需要安装 mcp 库: pip install mcp"
        ) from e


class MCPToolAdapter(Tool):
    """将 MCP Server 的单个工具适配为框架的 Tool

    参数由 MCP 的 inputSchema（JSON Schema）转换而来，
    执行时通过 MCP 协议调用 tools/call 转发给远端。
    同步 run() 通过后台事件循环桥接异步调用。
    """

    def __init__(
        self,
        session: Any,
        server_name: str,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        loop: Optional[asyncio.AbstractEventLoop] = None
    ):
        """初始化 MCP 工具适配器

        Args:
            session: mcp ClientSession 实例
            server_name: 所属 MCP server 名称（用于前缀防冲突）
            name: MCP 工具名称
            description: 工具描述
            input_schema: MCP 工具的 inputSchema（JSON Schema）
            loop: 承载 MCP session 的事件循环（同步调用桥接用）
        """
        super().__init__(
            name=f"{server_name}_{name}" if server_name else name,
            description=description,
            expandable=False
        )
        self._session = session
        self._server_name = server_name
        self._tool_name = name
        self._input_schema = input_schema or {}
        self._loop = loop

    def get_parameters(self) -> List[ToolParameter]:
        """从 MCP inputSchema 转换参数定义"""
        params = []
        properties = self._input_schema.get("properties", {})
        required = set(self._input_schema.get("required", []))

        for name, prop in properties.items():
            param_type = prop.get("type", "string")
            params.append(ToolParameter(
                name=name,
                type=param_type,
                description=prop.get("description", ""),
                required=name in required,
                default=prop.get("default")
            ))

        return params

    def run(self, parameters: Dict[str, Any]) -> ToolResponse:
        """执行 MCP 工具调用（同步入口）

        mcp SDK 为异步 API，这里通过后台事件循环桥接。
        """
        try:
            result = self._call_sync(parameters)
            return self._build_success(result)
        except Exception as e:
            return self._build_error(e, parameters)

    async def arun(self, parameters: Dict[str, Any]) -> ToolResponse:
        """异步执行 MCP 工具调用（真正的异步路径）"""
        try:
            result = await self._session.call_tool(self._tool_name, parameters)
            return self._build_success(result)
        except Exception as e:
            return self._build_error(e, parameters)

    def _call_sync(self, parameters: Dict[str, Any]) -> Any:
        """在同步上下文执行异步调用

        若提供了后台 loop，则通过 run_coroutine_threadsafe 提交；
        否则兜底用 asyncio.run。
        """
        coro = self._session.call_tool(self._tool_name, parameters)

        if self._loop is not None and self._loop.is_running():
            loop: asyncio.AbstractEventLoop = self._loop
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=120)

        return asyncio.run(coro)

    def _build_success(self, result: Any) -> ToolResponse:
        """构造成功响应"""
        return ToolResponse.success(
            text=self._extract_text(result),
            data={
                "server": self._server_name,
                "tool": self._tool_name,
                "is_error": getattr(result, "isError", False)
            },
            context={"server_name": self._server_name, "tool_name": self._tool_name}
        )

    def _build_error(self, e: Exception, parameters: Dict[str, Any]) -> ToolResponse:
        """构造错误响应"""
        return ToolResponse.error(
            code=ToolErrorCode.EXECUTION_ERROR,
            message=f"MCP 工具 '{self._server_name}.{self._tool_name}' 调用失败: {str(e)}",
            context={
                "server_name": self._server_name,
                "tool_name": self._tool_name,
                "params_input": parameters
            }
        )

    @staticmethod
    def _extract_text(result: Any) -> str:
        """从 MCP CallToolResult 提取文本内容

        支持 text 内容块，以及结构化内容（image/resource 等）的兜底。
        """
        parts = []
        content = getattr(result, "content", None) or []

        for block in content:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                parts.append(block.text)
            elif hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))

        text = "\n".join(parts) if parts else ""

        if not text:
            text = f"[MCP 工具返回无文本内容] structuredContent={getattr(result, 'structuredContent', None)}"
        return text


class MCPServerManager:
    """MCP Server 配置加载与管理

    职责：
    - 解析 mcp.json 配置
    - 为每个 server 建立 mcp 客户端连接（常驻后台事件循环）
    - 拉取工具清单 (tools/list) 并生成 MCPToolAdapter 列表
    - 管理连接生命周期（close_all）
    """

    def __init__(self, config_file: str = "mcp.json"):
        """初始化 MCP Server 管理器

        Args:
            config_file: mcp 配置文件路径（默认 "mcp.json"）
        """
        self.config_file = config_file
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connections: List[Any] = []  # (server_name, client_ctx, read, write, session)

    # ==================== 公共接口 ====================

    def connect_all(self) -> List[MCPToolAdapter]:
        """连接所有配置的 MCP server 并生成工具适配器

        单个 server 连接失败不阻塞其他 server（记录警告继续）。

        Returns:
            MCPToolAdapter 列表

        Raises:
            ImportError: mcp SDK 未安装时抛出（由上层降级处理）
        """
        # 提前检查依赖，未安装时同步抛出 ImportError（避免启动后台循环）
        _require_mcp()

        config = self.load_config()
        servers = config.get("mcpServers", {})

        if not servers:
            return []

        # 确保后台事件循环已启动（连接常驻保活）
        self._ensure_loop()

        # 在后台循环中建立连接并拉取工具
        future = asyncio.run_coroutine_threadsafe(
            self._connect_all_async(servers), self._loop  # type: ignore[arg-type]
        )
        return future.result(timeout=60)

    async def _connect_all_async(self, servers: Dict[str, Any]) -> List[MCPToolAdapter]:
        """异步建立所有连接（在后台循环内运行）"""
        adapters: List[MCPToolAdapter] = []

        for server_name, server_cfg in servers.items():
            try:
                connection = await self._open_session(server_name, server_cfg)
                session = connection["session"]
                tools_result = await session.list_tools()

                self._connections.append(connection)

                for tool in tools_result.tools:
                    adapters.append(MCPToolAdapter(
                        session=session,
                        server_name=server_name,
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=tool.inputSchema or {},
                        loop=self._loop
                    ))

                print(f"[MCP] server '{server_name}' connected, registered {len(tools_result.tools)} tools")
            except Exception as e:
                print(f"[MCP] WARNING: server '{server_name}' connect failed: {e}")

        return adapters

    async def _open_session(self, server_name: str, server_cfg: Dict[str, Any]) -> Dict[str, Any]:
        """建立单个 MCP server 连接（保持打开，不随 async with 退出关闭）

        Args:
            server_name: server 名称
            server_cfg: server 配置（command/args/env 或 url/headers）

        Returns:
            连接字典: {"server_name", "client_ctx", "read", "write", "session"}
        """
        mcp = _require_mcp()

        if "url" in server_cfg:
            from mcp.client.streamable_http import streamablehttp_client
            kwargs = {"url": server_cfg["url"]}
            if server_cfg.get("headers"):
                kwargs["headers"] = server_cfg["headers"]
            client_ctx = streamablehttp_client(**kwargs)
        else:
            from mcp.client.stdio import stdio_client
            params = mcp.StdioServerParameters(
                command=server_cfg.get("command", ""),
                args=server_cfg.get("args", []),
                env=server_cfg.get("env"),
            )
            client_ctx = stdio_client(params)

        # 手动进入上下文管理器，保持连接常驻
        read, write = await client_ctx.__aenter__()
        session = mcp.ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()

        return {
            "server_name": server_name,
            "client_ctx": client_ctx,
            "read": read,
            "write": write,
            "session": session,
        }

    def close_all(self):
        """关闭所有 MCP server 连接"""
        if self._loop is None:
            return

        async def _close():
            for conn in self._connections:
                try:
                    session = conn.get("session")
                    if session:
                        await session.__aexit__(None, None, None)
                except Exception:
                    pass
                try:
                    client_ctx = conn.get("client_ctx")
                    if client_ctx:
                        await client_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
            self._connections.clear()

        future = asyncio.run_coroutine_threadsafe(_close(), self._loop)
        try:
            future.result(timeout=10)
        except Exception:
            pass

        # 停止后台循环
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=10)
        self._loop = None
        self._thread = None

    def load_config(self) -> Dict[str, Any]:
        """读取 mcp.json 配置

        Returns:
            配置字典: {"mcpServers": {name: config}}
        """
        from pathlib import Path

        path = Path(self.config_file)
        if not path.exists():
            return {"mcpServers": {}}

        return safe_json_load(path, default={"mcpServers": {}})

    # ==================== 内部工具 ====================

    def _ensure_loop(self):
        """确保后台事件循环已启动（守护线程，连接常驻）"""
        if self._loop is not None and self._loop.is_running():
            return

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(self._loop,),
            daemon=True,
            name="mcp-client-loop"
        )
        self._thread.start()

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop):
        """后台线程入口：运行事件循环"""
        asyncio.set_event_loop(loop)
        loop.run_forever()
