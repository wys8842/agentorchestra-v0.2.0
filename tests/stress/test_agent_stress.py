"""Agent 压力测试"""

import asyncio
import time
import pytest
from concurrent.futures import ThreadPoolExecutor
from agentorchestra.runtime.agents.simple_agent import SimpleAgent
from agentorchestra.runtime.core.llm import SymphonyLLM
from agentorchestra.capability.tools.registry import ToolRegistry


class MockLLM:
    """高性能模拟 LLM"""

    def __init__(self, response: str = "Mock response"):
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


class TestAgentConcurrency:
    """Agent 并发测试"""

    def test_concurrent_agent_creation(self):
        """测试并发创建 Agent"""
        def create_agent(i):
            llm = MockLLM(f"Response {i}")
            return SimpleAgent(name=f"Agent-{i}", llm=llm)

        with ThreadPoolExecutor(max_workers=10) as executor:
            agents = list(executor.map(create_agent, range(50)))

        assert len(agents) == 50

    @pytest.mark.asyncio
    async def test_concurrent_agent_run(self):
        """测试并发运行 Agent"""
        async def run_agent(i):
            llm = MockLLM(f"Result {i}")
            agent = SimpleAgent(name=f"Agent-{i}", llm=llm)
            return agent.run(f"Task {i}")

        start = time.time()
        tasks = [run_agent(i) for i in range(20)]
        results = await asyncio.gather(*tasks)
        elapsed = time.time() - start

        print(f"\n20 concurrent agent runs in {elapsed:.2f}s")
        assert len(results) == 20

    @pytest.mark.asyncio
    async def test_rapid_agent_creation_and_run(self):
        """测试快速创建和运行 Agent"""
        async def create_and_run(i):
            llm = MockLLM(f"Quick {i}")
            agent = SimpleAgent(name=f"QuickAgent-{i}", llm=llm)
            return agent.run(f"Quick task {i}")

        start = time.time()
        tasks = [create_and_run(i) for i in range(50)]
        results = await asyncio.gather(*tasks)
        elapsed = time.time() - start

        print(f"\n50 rapid agent runs in {elapsed:.2f}s")
        assert all(r is not None for r in results)


class TestToolRegistryStress:
    """工具注册表压力测试"""

    @pytest.mark.asyncio
    async def test_concurrent_tool_registration(self):
        """测试并发工具注册"""
        async def register_tools(registry, offset):
            from agentorchestra.capability.tools.base import Tool

            class DynamicTool(Tool):
                name = f"dynamic_tool_{offset}"
                description = "A dynamic tool"

                def execute(self, **kwargs):
                    return f"result {offset}"

            registry.register_tool(DynamicTool())
            return True

        registry = ToolRegistry()

        tasks = [register_tools(registry, i) for i in range(100)]
        results = await asyncio.gather(*tasks)

        assert all(results)
        tools = registry.list_tools()
        assert len(tools) >= 100

    @pytest.mark.asyncio
    async def test_concurrent_tool_execution(self):
        """测试并发工具执行"""
        from agentorchestra.capability.tools.base import Tool

        class FastTool(Tool):
            name = "fast_tool"
            description = "A fast tool"

            def execute(self, **kwargs):
                return "fast result"

        registry = ToolRegistry()
        registry.register_tool(FastTool())

        async def execute_tool(i):
            response = await registry.async_execute_tool("fast_tool", "{}")
            return response.text

        start = time.time()
        tasks = [execute_tool(i) for i in range(50)]
        results = await asyncio.gather(*tasks)
        elapsed = time.time() - start

        print(f"\n50 tool executions in {elapsed:.2f}s")
        assert all(r == "fast result" for r in results)


class TestMemoryBackendLoad:
    """内存后端负载测试"""

    @pytest.mark.asyncio
    async def test_sustained_load(self):
        """测试持续负载"""
        from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore

        store = InMemoryCheckpointStore()
        await store.init()

        duration = 5  # 5秒
        start = time.time()
        operations = 0

        while time.time() - start < duration:
            await store.create_thread(f"thread-{operations}", {"i": operations})
            operations += 1

        elapsed = time.time() - start
        ops_per_sec = operations / elapsed
        print(f"\n{operations} operations in {elapsed:.2f}s ({ops_per_sec:.1f} ops/s)")

        assert operations > 100

    @pytest.mark.asyncio
    async def test_burst_load(self):
        """测试突发负载"""
        from agentorchestra.orchestration.state.backends.memory_backend import InMemoryCheckpointStore
        from agentorchestra.orchestration.state.checkpoint import Checkpoint

        store = InMemoryCheckpointStore()
        await store.init()

        # 突发1000个操作
        async def burst_operation(i):
            thread_id = f"thread-{i % 100}"
            await store.create_thread(thread_id, {"i": i})
            cp = Checkpoint(thread_id, f"cp-{i}", {"i": i})
            await store.save_checkpoint(cp)
            return await store.load_checkpoint(thread_id, f"cp-{i}")

        start = time.time()
        tasks = [burst_operation(i) for i in range(1000)]
        results = await asyncio.gather(*tasks)
        elapsed = time.time() - start

        print(f"\n1000 burst operations in {elapsed:.2f}s")
        assert all(r is not None for r in results)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
