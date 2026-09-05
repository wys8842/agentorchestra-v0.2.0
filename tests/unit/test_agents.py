"""Agents 模块单元测试"""

import pytest
from agentorchestra.runtime.agents.simple_agent import SimpleAgent
from agentorchestra.runtime.agents.react_agent import ReActAgent
from agentorchestra.runtime.agents.plan_solve_agent import PlanSolveAgent
from agentorchestra.runtime.agents.reflection_agent import ReflectionAgent
from agentorchestra.runtime.agents.loop_agent import LoopAgent, LoopState, LoopStatus, Budget, Plan, Evidence, Reflection, TerminationDecision
from agentorchestra.runtime.core.llm import SymphonyLLM
from agentorchestra.capability.tools.registry import ToolRegistry


class MockLLM:
    """模拟 LLM"""

    def __init__(self, response: str = "Mock response"):
        self.response = response
        self.model = "mock-model"
        self.provider = "mock"

    def invoke(self, messages, **kwargs):
        class MockResponse:
            def __init__(self, text):
                self.choices = [type('obj', (object,), {'message': type('obj', (object,), {'content': text, 'tool_calls': None})()})()]
                self.usage = type('obj', (object,), {'total_tokens': 10})()
        return MockResponse(self.response)

    def invoke_with_tools(self, messages, tools, **kwargs):
        class MockResponse:
            def __init__(self, text):
                self.choices = [type('obj', (object,), {'message': type('obj', (object,), {'content': text, 'tool_calls': []})()})()]
                self.usage = type('obj', (object,), {'total_tokens': 10})()
        return MockResponse(self.response)

    async def ainvoke(self, messages, **kwargs):
        return self.invoke(messages, **kwargs)

    async def ainvoke_with_tools(self, messages, tools, **kwargs):
        return self.invoke_with_tools(messages, tools, **kwargs)


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
        assert result is not None


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
        assert agent.max_steps == 5
        # 新特性默认关闭
        assert agent.enable_reflection is False
        assert agent.enable_replan is False

    def test_loop_agent_custom_steps(self):
        """测试自定义步数"""
        llm = MockLLM()
        agent = LoopAgent(name="Test", llm=llm, max_steps=10)
        assert agent.max_steps == 10

    def test_loop_agent_with_reflection(self):
        """测试启用反思"""
        llm = MockLLM()
        agent = LoopAgent(name="Test", llm=llm, enable_reflection=True)
        assert agent.enable_reflection is True

    def test_loop_agent_with_replan(self):
        """测试启用再规划"""
        llm = MockLLM()
        agent = LoopAgent(name="Test", llm=llm, enable_replan=True, max_replans=3)
        assert agent.enable_replan is True
        assert agent.max_replans == 3

    def test_loop_agent_run_simple_mode(self):
        """测试简单模式运行"""
        llm = MockLLM("Test response")
        agent = LoopAgent(name="Test", llm=llm)
        # 测试创建成功即可，不测试实际运行（Mock LLM 行为有限）
        assert agent.name == "Test"


class TestLoopState:
    """循环状态测试"""

    def test_loop_state_creation(self):
        """测试循环状态创建"""
        state = LoopState(goal="Test goal")
        assert state.goal == "Test goal"
        assert state.status == LoopStatus.RUNNING
        assert len(state.evidence) == 0

    def test_loop_state_with_budget(self):
        """测试带预算的状态"""
        budget = Budget(max_steps=10, max_replans=3)
        state = LoopState(goal="Test", budget=budget)
        assert state.budget.max_steps == 10
        assert state.budget.max_replans == 3


class TestPlan:
    """计划测试"""

    def test_plan_creation(self):
        """测试计划创建"""
        plan = Plan(
            steps=["step1", "step2"],
            current_step=0,
            success_criteria=["criterion1"]
        )
        assert len(plan.steps) == 2
        assert plan.current_step == 0

    def test_plan_to_dict(self):
        """测试计划序列化"""
        plan = Plan(steps=["step1"])
        d = plan.to_dict()
        assert "steps" in d
        assert d["steps"] == ["step1"]


class TestEvidence:
    """证据测试"""

    def test_evidence_creation(self):
        """测试证据创建"""
        evidence = Evidence(
            tool_name="test_tool",
            tool_call_id="call-123",
            status="success"
        )
        assert evidence.tool_name == "test_tool"
        assert evidence.status == "success"


class TestReflection:
    """反思测试"""

    def test_reflection_creation(self):
        """测试反思创建"""
        reflection = Reflection(
            progress=0.5,
            issues=["issue1"],
            should_replan=False
        )
        assert reflection.progress == 0.5
        assert len(reflection.issues) == 1


class TestTerminationDecision:
    """终止决策测试"""

    def test_termination_decision_creation(self):
        """测试终止决策创建"""
        decision = TerminationDecision(
            signal="completed",
            action="stop",
            reason="Goal satisfied"
        )
        assert decision.signal == "completed"
        assert decision.action == "stop"
