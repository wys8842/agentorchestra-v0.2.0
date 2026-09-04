"""agentorchestra.runtime - 运行时域。

收纳 Agent 范式（``agents``）、上下文工程（``context``）与核心运行时
（``core``）。子包按能力划分：

- ``agents``  Agent 范式：Simple / ReAct / Reflection / PlanSolve / Loop + 工厂
- ``context`` 上下文工程：历史管理 / Token 预算 / 压缩 / GSSC
- ``core``    核心运行时：LLM / Config / Message / Agent 基类 / 可靠性 / 运维
"""
