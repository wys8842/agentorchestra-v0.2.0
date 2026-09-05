# Tenancy 模块

## 概述

Tenancy 模块提供多租户能力：隔离/配额/计费。

## 组件

### TenantContext

租户上下文：

```python
from agentorchestration.governance.tenancy import TenantContext

# 创建上下文
ctx = TenantContext(tenant_id="tenant-1")

# 使用上下文
with ctx:
    # 自动添加租户前缀
    key = namespace_resource("my-resource")

# 嵌套租户
with TenantContext("tenant-1"):
    with TenantContext("tenant-2"):
        pass
```

### namespace_resource

资源命名空间：

```python
from agentorchestration.governance.tenancy import namespace_resource

# 带租户
key = namespace_resource("resource", "tenant-1")  # tenant-1:resource

# 不带租户
key = namespace_resource("resource")  # resource
```

### QuotaManager

配额管理：

```python
from agentorchestration.governance.tenancy import QuotaManager

manager = QuotaManager()

# 设置配额
manager.set_limit("tenant-1", "tokens", 100000)
manager.set_limit("tenant-1", "api_calls", 1000)

# 检查配额
allowed = manager.check_limit("tenant-1", "tokens", 1000)

# 使用量
manager.record_usage("tenant-1", "tokens", 100)
usage = manager.get_usage("tenant-1", "tokens")
```

### Billing

计费：

```python
from agentorchestration.governance.tenancy import Billing

billing = Billing()

# 记录使用
billing.record_usage("tenant-1", "tokens", 100)

# 获取账单
invoice = billing.get_invoice("tenant-1", period="2024-01")
```

## 隔离机制

| 隔离级别 | 说明 |
|----------|------|
| 命名空间 | 资源前缀隔离 |
| 配额 | 资源使用限制 |
| 计费 | 使用量统计 |
| 审计 | 操作记录追溯 |
