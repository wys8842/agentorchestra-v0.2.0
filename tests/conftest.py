"""Pytest 配置和共享 fixtures"""

import asyncio
import os
import sys
from typing import Generator

import pytest

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """创建事件循环用于异步测试"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_llm_response():
    """模拟 LLM 响应"""
    class MockResponse:
        def __init__(self, content: str = "Test response", tool_calls=None):
            self.choices = [MockChoice(content, tool_calls)]
            self.usage = MockUsage()

    class MockChoice:
        def __init__(self, content: str, tool_calls=None):
            self.message = MockMessage(content, tool_calls)

    class MockMessage:
        def __init__(self, content: str, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []

    class MockUsage:
        total_tokens = 100
        prompt_tokens = 50
        completion_tokens = 50

    return MockResponse


@pytest.fixture
def temp_dir(tmp_path):
    """创建临时目录"""
    return tmp_path


@pytest.fixture
def sample_messages():
    """示例消息列表"""
    return [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "How are you?"},
    ]


@pytest.fixture
def sample_config():
    """示例配置"""
    from agentorchestra.runtime.core.config import Config
    return Config.development()
