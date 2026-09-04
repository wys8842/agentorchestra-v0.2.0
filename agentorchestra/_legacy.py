"""兼容层：把经典扁平导入路径映射到领域化新路径。

经典（重构前）顶层组件收纳进了领域包装包，公共导入路径保持不变：

====================  ======================================
经典路径               新物理路径（规范）
====================  ======================================
agentorchestra.agents  agentorchestra.runtime.agents
agentorchestra.context agentorchestra.runtime.context
agentorchestra.core    agentorchestra.runtime.core
agentorchestra.tools   agentorchestra.capability.tools
agentorchestra.skills  agentorchestra.capability.skills
agentorchestra.memory  agentorchestra.capability.memory
agentorchestra.state   agentorchestra.orchestration.state
agentorchestra.tx      agentorchestra.governance.tx
agentorchestra.tenancy agentorchestra.governance.tenancy
====================  ======================================

``governance`` / ``orchestration`` 顶层名字是"域包装包"本身：
- 顶层公共符号（ACLManager、Graph 等）由包装包 ``__init__`` 从
  ``govern`` / ``orch`` 再导出；
- 经典深层模块（如 ``agentorchestra.orchestration.graph``）按模块存在性
  映射到 ``...orch.graph`` / ``...govern.acl``。

导入走兼容名与走规范名得到的是**同一个模块对象**（不重复加载、类身份一致）。
本模块被 ``agentorchestra.__init__`` 在导入时自动安装。
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from typing import Optional

# 顶层组件别名：经典名 -> 规范名（规范目录与文件真实存在）
_LEGACY_TOP = {
    "agents": "agentorchestra.runtime.agents",
    "context": "agentorchestra.runtime.context",
    "core": "agentorchestra.runtime.core",
    "tools": "agentorchestra.capability.tools",
    "skills": "agentorchestra.capability.skills",
    "memory": "agentorchestra.capability.memory",
    "state": "agentorchestra.orchestration.state",
    "tx": "agentorchestra.governance.tx",
    "tenancy": "agentorchestra.governance.tenancy",
}

# 经典扁平组件（旧 governance/orchestration 的直接子模块）被移进域内子包
_GOVERN_FLAT = {"acl", "cas", "identity", "permission"}
_ORCH_FLAT = {"delivery", "events", "graph", "inbox", "migration", "nodes", "scheduler"}


def _guess(fullname: str) -> Optional[str]:
    """返回 fullname 的规范（新物理）模块名；无映射返回 None。"""
    if not fullname.startswith("agentorchestra."):
        return None
    tail = fullname[len("agentorchestra."):]
    segs = tail.split(".")
    top = segs[0]
    rest = segs[1:]
    if top in _LEGACY_TOP:
        target = _LEGACY_TOP[top]
        if rest:
            target += "." + ".".join(rest)
        return target
    if top == "governance" and rest and rest[0] in _GOVERN_FLAT:
        return "agentorchestra.governance.govern." + ".".join(rest)
    if top == "orchestration" and rest and rest[0] in _ORCH_FLAT:
        return "agentorchestra.orchestration.orch." + ".".join(rest)
    return None


class _AliasLoader(importlib.abc.Loader):
    """把别名名解析为已加载的规范模块（不重复执行模块代码）。"""

    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec):  # noqa: D102
        return None

    def exec_module(self, module) -> None:  # noqa: D102
        canonical = importlib.import_module(self._target)
        sys.modules[module.__name__] = canonical
        # 让经典父包/根包能通过属性链访问到同一模块对象
        parent_name, _, attr = module.__name__.rpartition(".")
        if parent_name and attr:
            parent = sys.modules.get(parent_name)
            if parent is not None and not hasattr(parent, attr):
                try:
                    setattr(parent, attr, canonical)
                except Exception:
                    pass


def _module_available(fullname: str) -> bool:
    try:
        return importlib.util.find_spec(fullname) is not None
    except (ImportError, AttributeError, ValueError):
        return False


class _LegacyAliasFinder(importlib.abc.MetaPathFinder):
    """优先于 PathFinder 拦截经典扁平路径。"""

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102
        guess = _guess(fullname)
        if guess is None or guess == fullname:
            return None
        if not _module_available(guess):
            return None
        loader = _AliasLoader(guess)
        return importlib.util.spec_from_loader(fullname, loader)


_finder: Optional[_LegacyAliasFinder] = None


def install_legacy_aliases() -> None:
    """注册经典路径兼容 finder（幂等）。"""
    global _finder
    if _finder is not None:
        return
    _finder = _LegacyAliasFinder()
    sys.meta_path.insert(0, _finder)


__all__ = ["install_legacy_aliases"]
