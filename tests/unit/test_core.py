"""Core 模块单元测试"""

import pytest
from agentorchestra.runtime.core.config import Config
from agentorchestra.runtime.core.message import Message
from agentorchestra.runtime.core.exceptions import SymphonyException


class TestConfig:
    """配置测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = Config()
        assert config.llm.default_model == "gpt-3.5-turbo"
        assert config.llm.default_provider == "openai"
        assert config.system.log_level == "WARNING"

    def test_development_config(self):
        """测试开发配置"""
        config = Config.development()
        assert config.system.debug is True
        assert config.system.log_level == "DEBUG"
        assert config.trace.enabled is True

    def test_production_config(self):
        """测试生产配置"""
        config = Config.production()
        assert config.system.debug is False

    def test_llm_config(self):
        """测试 LLM 配置"""
        config = Config()
        config.llm.temperature = 0.5
        config.llm.max_tokens = 2000
        assert config.llm.temperature == 0.5
        assert config.llm.max_tokens == 2000


class TestMessage:
    """消息测试"""

    def test_message_creation(self):
        """测试消息创建"""
        msg = Message("Hello world", "user")
        assert msg.content == "Hello world"
        assert msg.role == "user"

    def test_message_to_dict(self):
        """测试消息转字典"""
        msg = Message("Test", "assistant")
        d = msg.to_dict()
        assert d["content"] == "Test"
        assert d["role"] == "assistant"

    def test_message_from_dict(self):
        """测试从字典创建消息"""
        d = {"content": "Test", "role": "user"}
        msg = Message.from_dict(d)
        assert msg.content == "Test"
        assert msg.role == "user"


class TestExceptions:
    """异常测试"""

    def test_symphony_exception(self):
        """测试 Symphony 异常"""
        exc = SymphonyException("Test error")
        assert str(exc) == "Test error"
        assert isinstance(exc, Exception)

    def test_symphony_exception_with_code(self):
        """测试带错误码的异常"""
        exc = SymphonyException("Test error", error_code="TEST_ERROR")
        assert exc.error_code == "TEST_ERROR"
