"""Agents 模块单元测试"""

import pytest
from agentorchestra.runtime.agents.simple_agent import SimpleAgent
from agentorchestra.runtime.agents.react_agent import ReActAgent
from agentorchestra.runtime.agents.plan_solve_agent import PlanSolveAgent
from agentorchestra.runtime.agents.reflection_agent import ReflectionAgent
from agentorchestra.runtime.agents.loop_agent import LoopAgent
from agentorchestra.runtime.core.llm import SymphonyLLM
from agentorchestra.capability.tools.registry import ToolRegistry


class MockLLM:
    """模拟 LLM"""

    def __init__(self, response: str = "Mock response"):
        self.response = response

    def invoke(self, messages, **kwargs):
        class MockResponse:
            def __init__(self, text):
                self.choices = [type('obj', (object,), {'message': type('obj', (object,), {'content': text})()})()]
                self.usage = type('obj', (object,), {'total_tokens': 10})()
        return MockResponse(self.response)

    async def ainvoke(self, messages, **kwargs):
        return self.invoke(messages, **kwargs)


class TestSimpleAgent:
    """简单 Agent 测试"""

    def test_agent_creation(self):
        """测试 Agent 创建"""
        llm = MockLLM()
        agent = SimpleAgent(name="TestAgent", llm=llm)
        assert agent.name == "TestAgent"

    def test_agent_run(self):
        """测试 Agent 运行"""
        llm = MockLLM("Hello!")
        agent = SimpleAgent(name="TestAgent", llm=llm)
        result = agent.run("Say hello")
        assert result == "Hello!"


class TestReActAgent:
    """ReAct Agent 测试"""

    def test_react_agent_creation(self):
        """测试 ReAct Agent 创建"""
        llm = MockLLM()
        agent = ReActAgent(name="ReActTest", llm=llm)
        assert agent.name == "ReActTest"
        assert agent.max_steps == 5

    def test_react_agent_custom_steps(self):
        """测试自定义步数"""
        llm = MockLLM()
        agent = ReActAgent(name="Test", llm=llm, max_steps=10)
        assert agent.max_steps == 10


class TestPlanSolveAgent:
    """计划求解 Agent 测试"""

    def test_plan_solve_agent_creation(self):
        """测试计划求解 Agent 创建"""
        llm = MockLLM()
        agent = PlanSolveAgent(name="Planner", llm=llm)
        assert agent.name == "Planner"


class TestReflectionAgent:
    """反思 Agent 测试"""

    def test_reflection_agent_creation(self):
        """测试反思 Agent 创建"""
        llm = MockLLM()
        agent = ReflectionAgent(name="Reflector", llm=llm)
        assert agent.name == "Reflector"


class TestLoopAgent:
    """循环 Agent 测试"""

    def test_loop_agent_creation(self):
        """测试循环 Agent 创建"""
        llm = MockLLM()
        agent = LoopAgent(name="Looper", llm=llm)
        assert agent.name == "Looper"
        assert agent.max_iterations == 3

    def test_loop_agent_custom_iterations(self):
        """测试自定义迭代次数"""
        llm = MockLLM()
        agent = LoopAgent(name="Test", llm=llm, max_iterations=5)
        assert agent.max_iterations == 5
