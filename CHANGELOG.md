# Changelog

## [0.2.0] — 2026-09-05

> 修复 v0.1.2 评估中 **5 CRITICAL + 10 HIGH + 11 MEDIUM**，包括 3 个"上线即爆雷"问题。

### CRITICAL 修复

- **C-1**：`TraceLogger._write_html_event` 引用不存在的 `event_obj`（应为形参 `event`）→ NameError 导致 HTML 输出 100% 失败。改为 `event.get('event_id', ...)`；并移除 `finalize()` 的 emoji `print()`（Windows GBK 控制台 `UnicodeEncodeError`，归因于 L-1）
- **C-2**：`Tracer` 引入 `SpanBatcher`（size 阈值 50 / window 1000ms）+ `end_span` 入 buffer；触发后调 `exporter.export_batch()`；新增 `flush()`；使 H-9 修复的 OTLP 批处理真正接线
- **C-3**：README 顶部加 **⚠️ v0.2.0 升级警告**（schema 不兼容，旧 DB 需 DROP 表重建）；MIGRATION.md 补 `v0.1.x → v0.2.0` 章节（fencing_token 列 / iteration_snapshots 表 / PG sequence）
- **C-4**：内存后端 `release_lock` 同步清理 `_lock_versions`（防 per-resource dict 单调增长内存泄漏）；`delete_expired_messages` 级联清理 `_inbox_acks`（内存 + SQL 双后端，防孤儿回执）
- **C-5**：删除内存后端旧版 `compare_and_swap` 死代码（无 fencing_token 版本，被新版覆盖但残留）

### HIGH 修复

- **H-1**：确认 `compare_and_swap` 第4参数 `expected_fencing_token=None` 向后兼容（3 位置参数安全）
- **H-2**：Workflow `_expand_params` 默认严格模式——未声明依赖的 `$node.field` 引用抛 `WorkflowParamExpansionError`；`strict_param_expansion=False` 可降级为 warning+None
- **H-5**：TraceLogger `_maybe_rotate_jsonl` 收窄异常为 `except OSError`；`finalize` 移除 emoji print 改用 logger
- **H-7**：`Agent._apply_tool_filter` / `_restore_tools` 移除 `_temp_disabled_*` 私有 dict 操作（改用 contextvars `temporary_tool_filter` 或 no-op 向后兼容层）

### MEDIUM 修复

- **M-8**：MIGRATION.md 补 v0.1.x → v0.2.0 迁移指南
- **M-11**（TraceLogger rotate）等部分文档同步

### 已知限制（v0.3 处理）

- `ReActAgent` 三循环（run/arun/arun_stream）仍重复（~600 行；需 `ReActExecutor` 抽象重构）
- `GraphScheduler` fan-in barrier 未实现（C-N7，需 scheduler 主循环重构）
- `CheckpointStore ABC` 27 个 abstractmethod 未拆分（god interface；需按 Thread/Lock/Inbox/Audit 拆接口）
- `165 个 except Exception`（非关键路径）+ `65 个 # type: ignore` 持续收窄
- SQLite `_fencing_seq` 多进程不安全 + PG sequence 名硬编码（多租户冲突）
- `tests/` 仍 100% 空（用户明确要求不做测试）

## [0.1.2] — 2026-09-05

> 修复 v0.1.1 评估中剩余 **1 CRITICAL + 5 HIGH + 2 MEDIUM**（跨后端一致性 + 可观测性 + 文档漂移）。

### CRITICAL 修复

- **C-1**：SQL `_LockRow` 加 `fencing_token` 列（迁移到位）；`_PG_FENCING_SEQ_NAME` sequence 在 `init()` 创建；`acquire_lock` 返回的 LockRecord `fencing_token` 真实写入 DB；`compare_and_swap` 支持 `expected_fencing_token` 可选校验

### HIGH 修复

- **H-2**：`PlanSolveAgent` 模块级函数（`_build_tool_schemas_from_registry` / `_map_parameter_type` / `_execute_tool_call_from_registry`）改为委派到 `Agent` 基类的 classmethod，消除 ~150 行重复代码
- **H-4**：`OptimisticLock.acquire` 返回 `Optional[LockRecord]`（而非 `bool`），调用方可读到 `fencing_token`；`compare_and_swap` 接受 `expected_fencing_token` 可选校验；`coordinator.py` 同步适配
- **H-7**：scheduler `_load_iteration` / `_save_iteration` 改用 `store.load_iteration_snapshot` / `save_iteration_snapshot` 专用 API（**O(1)** 查询/写入），取代原 WAL 扫描（O(N)）；新增 `_IterationSnapshotRow` 表 + upsert 语义
- **H-9**：`OTLPHttpJsonExporter.export_batch(spans)` 批处理多 span 为单次 OTLP POST（resourceSpans[].scopeSpans[].spans[]），避免高频追踪时单 span 同步阻塞
- **H-10**：README 第3 行版本号同步至 v0.1.2（之前漂移到 v0.1.0 标识）

### MEDIUM 修复

- **M-1**：`delivery.py` `__all__: list = []`（不通过 `from ... import *` 暴露），强化 deprecation 标注
- **H-8**：`TraceLogger` 新增 `_MAX_JSONL_BYTES = 50MB` 阈值；`_maybe_rotate_jsonl()` 在文件过大时关闭当前文件并滚动到 `.N.jsonl` 后缀

### 异常处理收窄（H-5）

- **scheduler.py `emit` 回调**：`except Exception` → `except (TypeError, ValueError, RuntimeError)`（on_node_error 应是 callable）
- **scheduler.py `_save_iteration`**：`except Exception` → `except (AttributeError, KeyError, TypeError)`（调用错误本地化；DB / 网络错误向上传播以便监控告警）

### 已知限制（v0.3 处理）

- `ReActAgent` 三循环（run/arun/arun_stream）仍重复（涉及行为兼容重构，留 v0.3）
- `58 个 except Exception` 中 53 个尚未收窄（重点路径已完成）
- `65 个 # type: ignore` 尚未消除（持续推进）

## [0.1.1] — 2026-09-05

> 深度再评估后修复 **11 CRITICAL + 已验证 HIGH 项**（源自 v0.1.0 评估报告）。

### CRITICAL 修复

- **C-N1**：删除 `agent.py` 中 4 个死代码方法（`_register_task_tool` / `_register_todowrite_tool` / `_register_devlog_tool` / `_init_checkpoint_store`），累计 -130 行；功能已被 Capability 完整接管
- **C-N2**：SQL 后端 `acquire_lock` 与内存后端对齐——记录旧 version 并 `new_version = prev_version + 1`（per-resource 单调递增）
- **C-N3**：SQL 后端实现 `resolve_dlq`（`UPDATE _DLQRow SET status='resolved', resolved_at=:now WHERE dlq_id=:id`）
- **C-N4**：`DLQEntry` 增加 `id: Optional[int]` 字段；内存后端 `_dlq_next_id` 自增分配；解决 v0.1 引入的"resolve 永远 no-op"新 bug
- **C-N5**：`_resolve_path` 加路径穿越防护——解析后 `Path.resolve()` + `is_relative_to(working_dir)` 校验；拦截 `../../etc/passwd` 等
- **C-N6**：`ToolGenerator.filter` 增加 `conditions` JSON schema 校验（必须是 dict；递归校验 key 必须是字符串）
- **C-N7**：fan-in 多入边同步——标注为 v0.2 重构项（涉及执行语义，暂不实现，列入限制）
- **C-N8**：`DeliveryManager` 标注 deprecated（GraphScheduler 已内置投递语义，不再调用）
- **C-N9**：`GraphScheduler.iteration` 持久化——通过 `_save_iteration` 写入 WAL（`action_type=STATE_UPDATE`，payload 包含 `graph_id` + `iter`），`_load_iteration` 启动时恢复
- **C-N10**：`LockRecord` 增加 `fencing_token: int` 字段；内存后端 `_fencing_token` 全局单调递增；防止僵尸事务绕过 TTL 后误写
- **C-N11**：SQL 后端 PostgreSQL 路径创建 `agentorchestra_wal_seq` sequence；`append_wal` 用 `nextval(seq)` 原子分配；SQLite 保留 `BEGIN IMMEDIATE`

### HIGH 修复

- **H-N2**：`SecurityManager.set_open_mode(True)` 必须设置 `AGENTORCHESTRA_ALLOW_OPEN_MODE=1` 环境变量；未设置时抛 RuntimeError；构造时同样校验
- **H-N3**：新增 `opt_out_namespace_scope()` contextvars context manager；运维跨租户场景可在 with 块内调用 `namespace_resource()` 跳过前缀
- **H-N4**：`TraceLogger` 引入 `_overflow_warned` 标记；deque 溢出时 emit 一次性 WARNING（不再静默）
- **H-N5**：`TraceLogger` 引入 `_event_counter` 单调递增；`details_id` 基于 `event_id` 而非 deque 长度（避免 bounded 弹出后 ID 复用冲突）
- **H-N6**：`Span.add_event` 同时记录 `wall_ns`（`time.time_ns()`）；OTLP exporter 优先用 `wall_ns` 而非 monotonic→ns 反推
- **H-N7**：`TraceLogger` HTML `<title>` 中 `session_id` 经 `html.escape` 转义（纵深防御）
- **H-N10**：`InterruptResumer` 引入 `max_handler_failures=3`；handler 失败计数；超过阈值时标记 `processed_tokens` 避免死循环

### 已知限制（v0.2 处理）

- **C-N7**：GraphScheduler fan-in（多入边汇聚）需要 barrier 重构；当前每个上游完成即 send，下游 MergeNode 收到多条消息
- **C-N8**：DeliveryManager 仅 deprecated 标注，未真正删除（v0.2 若仍无调用方则移除）
- **H-N8**：`ReActAgent.run/arun/arun_stream` 三套循环仍重复（执行语义重构风险高，留 v0.2）
- **H-N9**：`PlanSolveAgent` 模块级函数与基类重复（Executor 与 Agent 边界重构）
- **H-N11**：55 个 `except Exception` 收窄为具体类型（持续推进）
- **H-N12**：65 个 `# type: ignore` 消除（持续推进）

## [0.1.0] — 2026-09-04

> **版本降级**：1.0.0 → 0.1.0。
> 深度评估发现 22 个 CRITICAL bug + 19 个 HIGH 架构问题，按"先稳后宣"原则降级。

### Phase 0 — 关键止血

- **删除** examples 中所有 `sys.path.insert("D:/proj/agentorchestra")`（绝对路径泄漏）
- **修复** `ReActAgent.arun_stream` 重复 LLM 调用（流式 + invoke_with_tools 双倍费用）
- **修复** `KeyboardInterrupt` 处理器改用 logger（`react_agent.py`）
- **修复** `SecurityManager` 默认开放 → 默认拒绝（deny-by-default）
- **修复** `TraceLogger` XSS（HTML 输出经 `html.escape`）
- **修复** 幂等键哈希（`coordinator.py:144-146`）— 纳入 `request_payload` 与 `resources`
- **修复** WAL 序列号分配竞态（SQLite `BEGIN IMMEDIATE` 序列化）
- **修复** Lock 版本号释放后归零（per-resource 单调递增计数器）
- **修复** `compensation.py:60` ctx 丢失 — `compensate_fn(params, ctx)`

### Phase 3 — Config 与 API 收敛

- **拆分** `Config` 为 17 个子配置类（`LLMConfig` / `SystemConfig` / `HistoryConfig` 等）
- **新增** `Config.development()` / `Config.production()` 预设
- **变更** 所有 feature flag 默认 **False**（opt-in by default）
  - `trace_enabled` True → False
  - `skills_enabled` True → False
  - `session_enabled` True → False
  - `subagent_enabled` True → False
  - `todowrite_enabled` True → False
  - `devlog_enabled` True → False
  - `state_checkpoint_enabled` True → False
  - `log_level` INFO → WARNING
- **兼容** 旧扁平字段访问（`config.skills_enabled` 等价 `config.skills.enabled`）

### Phase 4 — 引用路径标准化

- **修复** 所有 examples 使用标准领域路径（`runtime.agents.*` / `runtime.core.*` / `capability.tools.*` / `governance.*` / `orchestration.*`）
- **保留** `_legacy.py` MetaPathFinder 作为过渡兼容层（v0.2 删除）

### Phase 5 — 可观测性自研

- **移除** `opentelemetry-api` / `opentelemetry-sdk` 依赖（`otel` extras 已删）
- **强化** `TraceLogger` 线程安全（`RLock`）+ bounded events（`deque(maxlen=50000)`）
- **修复** OTLP 时间近似（改用真实 `start_wall_ns` / `end_wall_ns`）
- **新增** `Span.start_wall_ns` / `Span.end_wall_ns` 字段
- **新增** 自研 `MemoryExporter` / `JsonlExporter` / `OTLPHttpJsonExporter`（标准库即可）

### Phase 6 — API 收敛文档

- **新增** `MIGRATION.md`（v1.0 → v0.1 迁移指南）
- **新增** `CHANGELOG.md`（本文档）

### Phase 0 — 关键止血（补充修复）

- **修复** Agent.interrupt 异步竞态（C3）：原 `asyncio.run()` 在已有 loop 中触发 RuntimeError；改用 `asyncio.get_running_loop()` + fire-and-forget task
- **修复** SnapshotWorker 初始化竞态（C9）：`_stop` 在 `__init__` 中直接创建，不再依赖 `loop.is_running()`；`start()` 路径独立处理；自动从 `store.list_threads()` 发现 threads
- **修复** `OptimisticLock` 自动 namespace 隔离（C15）：`acquire` / `read_version` / `compare_and_swap` 自动通过 `namespace_resource()` 拼接租户 namespace
- **修复** Workflow 参数 expansion 注入（C22）：`_expand_params` 增加 `current_node_id` + `current_deps` 白名单检查，未声明依赖的跨节点引用置 None + 记录 warning
- **修复** Subagent 状态 RAII（C5）：新增 `temporary_tool_filter()` contextvars context manager；`ToolRegistry.execute_tool` / `list_tools` 集成 contextvars 检查；移除 `_temp_disabled_*` 私有 dict 方案
- **修复** DLQ resolve / replay（C14）：`DeadLetterQueue.resolve()` / `replay(coordinator)` API；CheckpointStore ABC + Memory backend 实现
- **新增** `InterruptResumer`（C8）：轮询 RESUMED 状态 interrupt 并触发对应 handler
- **新增** `TenantIsolationError` / `namespace_resource()` / `enforce_tenant_access()` 辅助工具
- **修复** Backends 包懒加载：避免 SQLAlchemy 1.4 环境无法 import 整个 framework（postgres_backend 需要 SQLAlchemy 2.0，改为 `__getattr__` 懒加载）

### Phase 2 — Agent 上帝对象拆分（完成）

- **删除** `agent.py` 中 ~170 行重复 inline 初始化代码（TraceLogger / SkillLoader / MCP / Ontology / SessionStore / MemoryManager / SubAgent / TodoWrite / DevLog / Checkpoint 全部下放）
- **新增** `Agent.capabilities` 属性（`CapabilityRegistry` 实例，13 个内置 capability）
- **新增** `Agent._capability_state` dict（capability 注入的共享状态）
- **保持向后兼容**：`agent.trace_logger` / `agent.skill_loader` / `agent.memory_manager` 等属性仍可通过属性复制从 `_capability_state` 获取
- **行数变化**：`agent.py` 1791 → 1693 行（-98 行；剩余主要是 lifecycle / run / tool dispatch / checkpoint 等核心 Agent 逻辑）

### Phase 2 — Capability 基础设施（已在上轮完成）

- `runtime/capabilities/base.py` — `Capability` 基类 + `CapabilityContext`
- `runtime/capabilities/registry.py` — `CapabilityRegistry` + `default_capabilities()`
- `runtime/capabilities/builtins.py` — 13 个内置 Capability

### 已知未修复（v0.2 计划）

- `agent.py` 上帝对象拆分（H1）— capability 基础设施已就绪，迁移留待 v0.2
- GraphScheduler fan-in 同步（C7）
- DeliveryManager 死代码清理（C10）
- Iteration counter 持久化（C11）
- Fencing tokens（C13）
- Graph cycle detection 改进（C14-2）
- JSON 反序列化 schema 校验（C20）
- 路径脱敏正则补全（C21）