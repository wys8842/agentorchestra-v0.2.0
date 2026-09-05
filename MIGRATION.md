# Symphony v0.1 — Migration Guide

> 版本降级：1.0.0 → **0.1.0**
> 本文档描述从 v1.0.0（已撤回）到 v0.1 的破坏性变更、修复、新 API。

## 1. 为什么是 0.1 而非 1.0

v1.0.0 的 "production-ready enterprise" 声明在深度评估后被认定为**不成立**：
- 22 个 CRITICAL 正确性/安全/持久化 bug
- 19 个 HIGH 架构问题
- `tests/` 目录完全为空
- 安全默认值全放行

按照"先稳后宣"原则，**降级为 0.1**，完成 Phase 0–6 后再宣告 1.0。

## 2. Phase 0 关键修复（critical bug 止血）

### 2.1 删除 examples 中的绝对路径注入

```python
# ❌ v1.0.0
sys.path.insert(0, 'D:/proj/agentorchestra')

# ✅ v0.1 — 删除即可（安装后自然 import）
```

### 2.2 ReActAgent.arun_stream 不再调用两次 LLM

```python
# ❌ v1.0.0：先 astream_invoke（流式），再 invoke_with_tools（取 tool_calls）
# → 用户被收双倍 LLM 费用

# ✅ v0.1：单次 ainvoke_with_tools + 一次性 yield content 块
result = await llm.ainvoke_with_tools(messages, tools, tool_choice="auto")
yield StreamEvent(chunk=result.choices[0].message.content)
```

### 2.3 默认开放安全模型 → 默认拒绝

```python
# ❌ v1.0.0：无规则 = 全放行（违反最小权限原则）
sm = SecurityManager()
sm.check(...)  # 永远 True

# ✅ v0.1：默认拒绝；需显式 allow() 才放行
sm = SecurityManager()
sm.check(...)  # 永远 False
sm.set_open_mode(True)  # 仅开发环境显式开启（生产禁用！）
sm.allow(["admin"], resource="*", action="*")
```

### 2.4 TraceLogger 修复 XSS

```python
# ❌ v1.0.0：payload 直接嵌入 HTML，可能执行 <script>
# ✅ v0.1：所有 payload / 字段值经 html.escape 转义
```

### 2.5 幂等键哈希包含请求参数

```python
# ❌ v1.0.0：仅哈希 action 名 → charge(alice, 50) 与 charge(alice, 9999) 碰撞
# ✅ v0.1：自动哈希 action 名 + request_payload + resources
async with coordinator.transaction(request_payload={"amount": 50}) as tx:
    ...
```

### 2.6 Lock 版本号不再归零

```python
# ❌ v1.0.0：release 后再次 acquire → version 重置为 0，破坏乐观并发
# ✅ v0.1：维护 per-resource 单调递增 version 计数器
```

### 2.7 补偿函数可访问 TxContext

```python
# ❌ v1.0.0：action.compensate_fn(params, None)  # 拿不到 ctx
# ✅ v0.1：action.compensate_fn(params, ctx)        # 支持访问 WAL/锁/身份
```

## 3. Phase 3：Config 拆分（opt-in by default）

### 3.1 默认值变化

| 字段 | v1.0.0 默认 | v0.1 默认 | 说明 |
|------|-------------|-----------|------|
| `trace_enabled` | True | **False** | 避免隐式文件 I/O |
| `skills_enabled` | True | **False** | 避免隐式文件扫描 |
| `session_enabled` | True | **False** | 避免隐式磁盘持久化 |
| `memory_enabled` | False | False | 未变 |
| `subagent_enabled` | True | **False** | 避免自动注册 TaskTool |
| `todowrite_enabled` | True | **False** | 避免自动注册 |
| `devlog_enabled` | True | **False** | 避免磁盘持久化 |
| `state_checkpoint_enabled` | True | **False** | 避免隐式 SQLite 创建 |
| `mcp_enabled` | False | False | 未变 |
| `ontology_engine_enabled` | False | False | 未变 |
| `log_level` | INFO | **WARNING** | 生产友好 |
| `debug` | False | False | 未变 |

### 3.2 新 Config API

```python
# 推荐：使用预设
config = Config.development()  # 开启 trace/skills/session
config = Config.production()   # 全部 opt-in

# 推荐：分组访问（新 API）
config = Config()
config.skills.enabled = True
config.trace.enabled = True
config.llm.temperature = 0.5

# 兼容：旧扁平 API（v1 写法）
config.skills_enabled = True  # 等价同 config.skills.enabled
config.trace_enabled = True
config.default_model = "gpt-4"  # 等价同 config.llm.default_model
```

## 4. Phase 3：标准导入路径

```python
# ❌ 旧路径（依赖 _legacy.py 的 MetaPathFinder hack）
from agentorchestra.agents.react_agent import ReActAgent
from agentorchestra.core.config import Config
from agentorchestra.tools.registry import ToolRegistry
from agentorchestra.state import get_default_store

# ✅ v0.1 标准领域路径
from agentorchestra.runtime.agents.react_agent import ReActAgent
from agentorchestra.runtime.core.config import Config
from agentorchestra.capability.tools.registry import ToolRegistry
from agentorchestra.orchestration.state import get_default_store
```

**说明**：旧路径在 v0.1 仍可用（通过 `_legacy.py` 自动映射），但会输出 DeprecationWarning。**v0.2 将删除旧路径。**

## 5. Phase 5：可观测性自研（无 OTel SDK 依赖）

- 移除 `opentelemetry-api` / `opentelemetry-sdk` 依赖
- 自研 `Tracer` / `Span` / `SpanExporter`（`runtime/core/tracing.py`）继续可用
- `OTLPHttpJsonExporter` 仅依赖 `urllib.request`（标准库）→ 可对接 Jaeger / Tempo
- TraceLogger 加 RLock + bounded deque（线程安全 + 内存安全）

```toml
# ❌ v1.0.0 pyproject.toml
otel = ["opentelemetry-api>=1.20", "opentelemetry-sdk>=1.20"]

# ✅ v0.1 — 已删除 otel extras
pip install agentorchestra        # 无 OTel 依赖
pip install agentorchestra[all]   # 仅可选：anthropic / gemini / mcp / neo4j / yaml / postgres
```

## 6. 已知未修复项（下一版本规划）

| ID | 问题 | 计划 |
|----|------|------|
| C3 | Checkpoint 持久化竞态 | v0.2 |
| C5 | Subagent 状态保护（contextvars RAII） | v0.2 |
| C7 | GraphScheduler fan-in 同步 | v0.3 |
| C8 | HITL 中断恢复逻辑 | v0.3 |
| C9 | SnapshotWorker 初始化竞态 | v0.2 |
| C15 | 租户隔离强制（per-resource ACL） | v0.3 |
| C22 | Workflow 参数 expansion 注入 | v0.2 |
| H1 | agent.py 上帝对象拆分 | v0.2 |

## 7. 升级步骤

```bash
# 1. 升级包
pip install --upgrade agentorchestra==0.1.0

# 2. （可选）启用开发预设
#    config = Config.development()

# 3. 移除 examples / 业务代码中的 sys.path.insert

# 4. （可选）将 Config 旧扁平字段访问改为分组 API
#    config.skills_enabled = True
#    ↓
#    config.skills.enabled = True
```
---

# v0.1.x → v0.2.0 升级指南

## ⚠️ 破坏性变更（必须 DROP 旧表）

v0.1.2 / v0.2.0 对 SQL backend 做了 schema 变更，**无 Alembic 迁移**。从 v0.1.x 升级时：

- `_LockRow` 新增 `fencing_token` 列
- 新增 `iteration_snapshots` 表
- PG 新增 `agentorchestra_wal_seq` / `agentorchestra_fencing_seq` sequence

**必须 DROP 现有表后重建**（或使用全新 DB / schema）：

```sql
-- SQLite
DROP TABLE IF EXISTS checkpoints, wal, snapshots, interrupts, locks,
  idempotency_keys, dlq, inbox_messages, inbox_acks, audit, threads,
  iteration_snapshots;

-- PostgreSQL（谨慎！会丢所有数据）
DROP SCHEMA public CASCADE;
```

内存 backend 不受影响。

## API 变更（v0.1.1 → v0.1.2 → v0.2.0）

### 1. `OptimisticLock.acquire` 返回值变更

```python
# ❌ v0.1.1：返回 bool
if await lock.acquire("res", "tx1"):
    ...

# ✅ v0.1.2+：返回 Optional[LockRecord]
record = await lock.acquire("res", "tx1")
if record is not None:
    token = record.fencing_token  # 可读 fencing_token
```

### 2. `compare_and_swap` 新增可选 `expected_fencing_token`

```python
# v0.1.1：3 参数
await store.compare_and_swap(resource_key, expected_version, owner_tx)

# v0.1.2+：4 参数（第4个可选，默认 None）
await store.compare_and_swap(
    resource_key, expected_version, owner_tx,
    expected_fencing_token=token,
)
```

向后兼容：3 位置参数仍可用（`expected_fencing_token` 默认 None）。

### 3. `delivery.DeliveryManager` 已 deprecated

`__all__: list = []`，不再通过 `from ... import *` 暴露。请勿继续使用。

### 4. Workflow 参数展开默认严格模式（v0.2.0）

未声明依赖的 `$node.field` 引用默认抛 `WorkflowParamExpansionError`；如需旧行为（静默 None），
在 context 中显式 `strict_param_expansion=False`。
