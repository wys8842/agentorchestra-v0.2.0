"""Governance 模块单元测试"""

import pytest
from agentorchestra.governance.tenancy.tenant import TenantContext, namespace_resource


class TestTenantContext:
    """租户上下文测试"""

    def test_tenant_context_creation(self):
        """测试租户上下文创建"""
        ctx = TenantContext(tenant_id="tenant-1")
        assert ctx.tenant_id == "tenant-1"

    def test_namespace_resource(self):
        """测试资源命名空间"""
        key = namespace_resource("test-key", "tenant-1")
        assert "tenant-1" in key
        assert "test-key" in key


class TestNamespaceResource:
    """资源命名空间测试"""

    def test_namespace_with_prefix(self):
        """测试带前缀的命名空间"""
        result = namespace_resource("my-resource", "tenant-1")
        assert result.startswith("tenant-1:")
        assert "my-resource" in result

    def test_namespace_without_tenant(self):
        """测试无租户的命名空间"""
        result = namespace_resource("my-resource")
        assert result == "my-resource"
