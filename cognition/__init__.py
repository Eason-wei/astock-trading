"""
cognition/ - A股交易体系认知系统
=====================================
负责：信念版本化 / 因果链管理 / 薄弱环节追踪 / 认知更新

核心文件：
  - beliefs.py      : 版本化信念（来自cognitions.json）
  - causal_chains.py: 因果链管理（来自logic_chains.json）
  - weak_areas.py  : 薄弱环节追踪（来自weak_areas.json）
  - updater.py     : 认知更新器（step7/8验证后调用）

使用方式：
  from cognition import BeliefStore, CausalChainStore, WeakAreasStore
  store = BeliefStore()
  current = store.get('龙头溢价逻辑')
  conflicts = store.get_conflicts('龙头溢价逻辑')
"""

from .beliefs import BeliefStore
from .causal_chains import CausalChainStore
from .weak_areas import WeakAreasStore
from .updater import CognitionUpdater

__all__ = [
    'BeliefStore',
    'CausalChainStore',
    'WeakAreasStore',
    'CognitionUpdater',
]
