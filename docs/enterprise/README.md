# Enterprise 模块

## 概述

Enterprise 模块提供企业级特性：多租户/配额/安全/合规。

## 特性

### 多租户

- 命名空间隔离
- 资源配额管理
- 使用量计量
- 计费导出

### 安全

- 对象身份
- RBAC/ACL
- WORM 审计
- 操作追溯

### 可靠性

- 事务补偿
- 死信队列
- 幂等性保证
- 检查点恢复

### 可观测

- 结构化日志
- Prometheus 指标
- OTLP 追踪
- 健康检查

## 路线图

### M0 持久化

- WAL + Checkpoint
- Snapshot
- Interrupt

### M1 事务

- 补偿事务
- 幂等性
- DLQ

### M2 图通信

- DAG 编排
- 条件路由
- 有界回环

### M3 身份权限

- 对象身份
- RBAC/ACL
- WORM 审计

### M4 并发

- 分布式锁
- 乐观锁
- Fencing Token

### M5 可观测

- Trace 导出
- Metrics 收集
- 日志聚合

### M6 多租户

- 命名空间
- 配额
- 计费
