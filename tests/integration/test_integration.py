"""集成测试"""

import pytest
import asyncio
from agentorchestra.runtime.agents.simple_agent import SimpleAgent
from agentorchestra.runtime.core.llm import SymphonyLLM
from agentorchestra.capability.tools.registry import ToolRegistry
from agentorchestra.capability.tools.builtin.calculator import CalculatorTool
from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore
from agentorchestra.orchestration.orch.scheduler import GraphScheduler
from agentorchestra.orchestration.orch.graph import Graph, Node


class MockLLM:
    """模拟 LLM 用于集成测试"""

    def __init__(self, response: str = "Test response"):
        self.response = response

    def invoke(self, messages, **kwargs):
        class Response:
            def __init__(self, text):
                self.choices = [type('obj', (object,), {
                    'message': type('obj', (object,), {'content': text})()
                })()]
                self.usage = type('obj', (object,), {'total_tokens': 10})()
        return Response(self.response)

    async def ainvoke(self, messages, **kwargs):
        return self.invoke(messages, **kwargs)

    def invoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke(messages, **kwargs)

    async def ainvoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke(messages, **kwargs)


class TestAgentToolIntegration:
    """Agent 和工具集成测试"""

    def test_agent_with_calculator_tool(self):
        """测试 Agent 使用计算器工具"""
        llm = MockLLM("10")
        agent = SimpleAgent(name="CalcAgent", llm=llm)

        registry = ToolRegistry()
        calc_tool = CalculatorTool()
        registry.register_tool(calc_tool)

        # 注册工具
        agent.tool_registry = registry

        # 运行
        result = agent.run("What is 5 + 5?")
        assert result is not None

    def test_agent_with_multiple_tools(self):
        """测试 Agent 使用多个工具"""
        llm = MockLLM("Result")
        agent = SimpleAgent(name="MultiToolAgent", llm=llm)

        registry = ToolRegistry()
        registry.register_tool(CalculatorTool())

        agent.tool_registry = registry
        result = agent.run("Calculate 10 * 10")

        assert result is not None


@pytest.mark.asyncio
class TestSchedulerIntegration:
    """调度器集成测试"""

    async def test_simple_graph_execution(self):
        """测试简单图执行"""
        scheduler = GraphScheduler()

        # 创建简单图
        graph = Graph()

        def mock_run(content, ctx):
            return {"result": f"processed: {content}"}

        node1 = Node(id="start", run=mock_run)
        node2 = Node(id="end", run=mock_run)

        graph.add_node(node1)
        graph.add_node(node2)
        graph.add_edge("start", "end")

        # 执行
        result = await scheduler.execute(
            graph=graph,
            initial_message={"data": "test"},
            thread_id="test-thread"
        )

        assert result is not None


@pytest.mark.asyncio
class TestStateIntegration:
    """状态管理集成测试"""

    async def test_full_workflow(self):
        """测试完整工作流"""
        store = InMemoryCheckpointStore()
        await store.init()

        # 创建线程
        await store.create_thread("workflow-1", {"name": "test-workflow"})

        # 保存检查点
        from agentorchestra.orchestration.state.checkpoint import Checkpoint
        cp = Checkpoint(
            thread_id="workflow-1",
            checkpoint_id="cp-1",
            state={"step": 1, "data": "test"}
        )
        await store.save_checkpoint(cp)

        # 获取检查点
        loaded = await store.load_checkpoint("workflow-1", "cp-1")
        assert loaded is not None
        assert loaded.state["step"] == 1

    async def test_lock_workflow(self):
        """测试锁工作流"""
        store = InMemoryCheckpointStore()
        await store.init()

        # 获取锁
        lock1 = await store.acquire_lock("resource-1", "tx-1", 30.0)
        assert lock1 is not None

        # 验证锁已占用
        lock2 = await store.acquire_lock("resource-1", "tx-2", 30.0)
        assert lock2 is None

        # 释放锁
        released = await store.release_lock("resource-1", "tx-1")
        assert released is True

        # 再次获取锁
        lock3 = await store.acquire_lock("resource-1", "tx-2", 30.0)
        assert lock3 is not None
