# Governance 模块

## 概述

Governance 模块提供企业级治理能力：权限/审计/身份/ACL。

## 子模块

### Identity

身份管理：

```python
from agentorchestration.governance.govern import Identity

identity = Identity()
identity.create("user-1", {"name": "张三"})
```

### ACL

访问控制：

```python
from agentorchestration.governance.govern import ACL

acl = ACL()
acl.grant("user-1", "resource", "read")
allowed = acl.check("user-1", "resource", "read")
```

### Permission

权限：

```python
from agentorchestration.governance.govern import Permission

perm = Permission("action", "resource")
perm.add_constraint("tenant=tenant-1")
```

### CAS

CAS 操作：

```python
from agentorchestration.governance.govern import CAS

cas = CAS(store)
ok = await cas.compare_and_swap("key", expected=1, new=2)
```

## WORM 审计

### Audit

审计日志：

```python
from agentorchestration.governance.govern import AuditLogger

logger = AuditLogger(store)
logger.log(action="read", principal="user-1", resource="doc-1")
entries = logger.query(principal="user-1")
```

## Tenancy 多租户

### TenantContext

租户上下文：

```python
from agentorchestration.governance.tenancy import TenantContext

ctx = TenantContext(tenant_id="tenant-1")
with ctx:
    # 在租户上下文中执行
    pass
```

### Quota

配额管理：

```python
from agentorchestration.governance.tenancy import QuotaManager

manager = QuotaManager()
manager.set_limit("tenant-1", "tokens", 100000)
usage = manager.get_usage("tenant-1", "tokens")
```

### Billing

计费：

```python
from agentorchestration.governance.tenancy import Billing

billing = Billing()
billing.record_usage("tenant-1", "tokens", 1000)
invoice = billing.get_invoice("tenant-1")
```
