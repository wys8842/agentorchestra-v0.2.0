"""Governance 模块单元测试"""

import pytest
from agentorchestra.governance.tenancy.tenant import TenantContext


class TestTenantContext:
    """租户上下文测试"""

    def test_tenant_context_creation(self):
        """测试租户上下文创建"""
        ctx = TenantContext(tenant_id="tenant-1")
        assert ctx.tenant_id == "tenant-1"

    def test_tenant_id_property(self):
        """测试租户ID属性"""
        ctx = TenantContext(tenant_id="test-tenant")
        assert ctx.tenant_id == "test-tenant"
