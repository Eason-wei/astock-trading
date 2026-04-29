"""
causal_chains.py - 因果链管理系统
=====================================
职责：
  - 管理logic_chains.json中的因果链
  - 按theme/theme_category组织
  - 支持TMO(Trigger-Mechanism-Outcome)查询
  - 从验证结果中提取新因果链

因果链结构（logic_chains.json格式）：
  {
    "chains": [
      {
        "trigger": "触发条件",
        "mechanism": "机制/原理",
        "outcome": "结果/现象",
        "when_works": "生效条件",
        "when_fails": "失效条件",
        "source_title": "来源标题",
        "source_url": "来源URL",
        "theme": "主题分类",
        "learned_at": "学习时间"
      }
    ]
  }
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from collections import defaultdict
from datetime import datetime


class CausalChainStore:
    """因果链存储管理器"""

    def __init__(self, store_path: str = None):
        if store_path is None:
            store_path = Path.home() / ".hermes/trading_study/logic_chains.json"
        self.path = Path(store_path)
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self):
        """加载因果链库"""
        if self.path.exists():
            with open(self.path, 'r', encoding='utf-8') as f:
                content = f.read()
                self._data = json.loads(content) if content else {"chains": []}
                if not isinstance(self._data, dict):
                    self._data = {"chains": []}
        else:
            self._data = {"chains": []}

    def _save(self):
        """持久化因果链库"""
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def reload(self):
        self._load()

    # ===== 查询接口 =====

    def all_chains(self) -> List[Dict[str, Any]]:
        """获取所有因果链"""
        return self._data.get('chains', [])

    def get_by_theme(self, theme: str) -> List[Dict[str, Any]]:
        """按theme字段筛选因果链"""
        theme = theme.lower()
        return [
            c for c in self._data.get('chains', [])
            if theme in c.get('theme', '').lower()
        ]

    def get_by_stage(self, stage: str) -> List[Dict[str, Any]]:
        """
        按市场阶段筛选相关因果链
        stage: '冰点期'/'退潮期'/'升温期'/'高潮期'/'降温期'/'修复期'
        """
        stage_keywords = {
            '冰点期': ['冰点', '反弹', '修复', '强势龙头'],
            '退潮期': ['退潮', '踩踏', '高位', 'A杀', '跟风'],
            '高潮期': ['高潮', '一字板', '加速', '跟风'],
            '升温期': ['升温', '启动', '主线'],
            '修复期': ['修复', '反弹'],
            '降温期': ['降温', '分歧', '炸板'],
        }
        keywords = stage_keywords.get(stage, [stage])
        chains = []
        for c in self._data.get('chains', []):
            text = ' '.join([
                c.get('trigger', ''),
                c.get('mechanism', ''),
                c.get('outcome', ''),
            ]).lower()
            if any(kw.lower() in text for kw in keywords):
                chains.append(c)
        return chains

    def search(self, keyword: str) -> List[Dict[str, Any]]:
        """
        全文搜索因果链
        """
        keyword = keyword.lower()
        results = []
        for c in self._data.get('chains', []):
            text = ' '.join([
                c.get('trigger', ''),
                c.get('mechanism', ''),
                c.get('outcome', ''),
                c.get('theme', ''),
            ]).lower()
            if keyword in text:
                results.append(c)
        return results

    def get_themes(self) -> List[str]:
        """获取所有theme分类"""
        themes = set()
        for c in self._data.get('chains', []):
            theme = c.get('theme', '')
            if theme:
                themes.add(theme)
        return sorted(themes)

    def get_theme_stats(self) -> Dict[str, int]:
        """获取各theme的因果链数量"""
        stats = defaultdict(int)
        for c in self._data.get('chains', []):
            theme = c.get('theme', '未分类')
            stats[theme] += 1
        return dict(stats)

    def get_tmo(self, chain_idx: int) -> Optional[Dict[str, str]]:
        """
        获取指定因果链的TMO结构
        Returns: {'trigger': ..., 'mechanism': ..., 'outcome': ...}
        """
        chains = self._data.get('chains', [])
        if 0 <= chain_idx < len(chains):
            c = chains[chain_idx]
            return {
                'trigger': c.get('trigger', ''),
                'mechanism': c.get('mechanism', ''),
                'outcome': c.get('outcome', ''),
                'when_works': c.get('when_works', ''),
                'when_fails': c.get('when_fails', ''),
            }
        return None

    def find_chain(self, trigger_hint: str = None, theme: str = None) -> List[Dict[str, Any]]:
        """
        组合搜索因果链
        """
        chains = self._data.get('chains', [])
        if trigger_hint:
            hint = trigger_hint.lower()
            chains = [c for c in chains if hint in c.get('trigger', '').lower()]
        if theme:
            t = theme.lower()
            chains = [c for c in chains if t in c.get('theme', '').lower()]
        return chains

    # ===== 添加接口 =====

    def add(
        self,
        trigger: str,
        mechanism: str,
        outcome: str,
        theme: str,
        source_title: str = None,
        source_url: str = None,
        when_works: str = None,
        when_fails: str = None,
    ) -> Dict[str, Any]:
        """
        添加新因果链
        """
        chain = {
            'trigger': trigger,
            'mechanism': mechanism,
            'outcome': outcome,
            'when_works': when_works or '',
            'when_fails': when_fails or '',
            'source_title': source_title or '',
            'source_url': source_url or '',
            'theme': theme,
            'learned_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        }
        self._data.setdefault('chains', []).append(chain)
        self._save()
        return chain

    def add_from_verification(
        self,
        trigger: str,
        mechanism: str,
        outcome: str,
        verified: bool,
        market_stage: str = None,
        source_title: str = "step7/8验证提取",
    ) -> Dict[str, Any]:
        """
        从验证结果中提取并添加因果链

        这在step7/8发现新的因果关系时调用
        """
        theme = f"验证_{market_stage}" if market_stage else "验证"
        verdict_tag = "✅已验证" if verified else "❌待修正"
        chain = self.add(
            trigger=trigger,
            mechanism=mechanism,
            outcome=outcome,
            theme=f"{theme}_{verdict_tag}",
            source_title=source_title,
        )
        return chain

    # ===== 批量操作 =====

    def add_batch(self, chains: List[Dict[str, Any]]) -> int:
        """
        批量添加因果链
        Returns: 添加数量
        """
        existing_count = len(self._data.get('chains', []))
        for c in chains:
            self._data.setdefault('chains', []).append(c)
        self._save()
        return len(self._data.get('chains', [])) - existing_count

    def remove_by_theme(self, theme: str) -> int:
        """删除指定theme的所有因果链"""
        original = len(self._data.get('chains', []))
        self._data['chains'] = [
            c for c in self._data.get('chains', [])
            if theme.lower() not in c.get('theme', '').lower()
        ]
        removed = original - len(self._data['chains'])
        if removed:
            self._save()
        return removed

    # ===== 统计 =====

    def get_statistics(self) -> Dict[str, Any]:
        """获取因果链库统计"""
        chains = self._data.get('chains', [])
        return {
            'total': len(chains),
            'themes': len(self.get_themes()),
            'theme_distribution': self.get_theme_stats(),
            'verified': len([c for c in chains if '✅' in c.get('theme', '')]),
            'needs_fix': len([c for c in chains if '❌' in c.get('theme', '')]),
        }
