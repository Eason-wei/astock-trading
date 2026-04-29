"""
weak_areas.py - 薄弱环节追踪系统
=====================================
职责：
  - 追踪系统薄弱环节（weak_areas.json）
  - 记录已知弱点和应对策略
  - 从验证结果中识别新弱点
  - 关联薄弱环节与形态/阶段矩阵

薄弱环节结构（weak_areas.json格式）：
  {
    "areas": [
      {
        "id": "WA001",
        "category": "形态判断",
        "description": "E1形态与普通波动的区分不清晰",
        "impact": "高",
        "frequency": "高",
        "strategies": ["策略A", "策略B"],
        "last_triggered": "2026-04-20",
        "status": "active",
        "related_chains": ["chain_idx_1"],
        "history": [
          {"date": "2026-04-20", "event": "发现", "lesson": "需要加入振幅阈值"}
        ]
      }
    ],
    "last_updated": "2026-04-20",
    "counts": {"total": 12, "active": 5, "mitigated": 7}
  }
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime


class WeakAreasStore:
    """薄弱环节存储管理器"""

    COUNTER = """
WeakAreas Category Reference:
  - 形态判断    : 分时形态识别/分类标准
  - 情绪识别    : 情绪周期定位/冰点/高潮判断
  - 仓位管理    : 仓位计算/调整时机
  - 选股        : 候选股筛选/排除条件
  - 时机        : 买入/卖出时机
  - 因果链      : 认知偏差/逻辑错误
  - 数据        : 数据缺失/质量问题
"""

    def __init__(self, store_path: str = None):
        if store_path is None:
            store_path = Path.home() / ".hermes/trading_study/weak_areas.json"
        self.path = Path(store_path)
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            with open(self.path, 'r', encoding='utf-8') as f:
                content = f.read()
                self._data = json.loads(content) if content else {}
                if not isinstance(self._data, dict):
                    self._data = {}
        else:
            self._data = {"areas": [], "last_updated": "", "counts": {}}

    def _save(self):
        self._data['last_updated'] = datetime.now().strftime('%Y-%m-%d')
        self._update_counts()
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def _update_counts(self):
        areas = self._data.get('areas', [])
        self._data['counts'] = {
            'total': len(areas),
            'active': sum(1 for a in areas if a.get('status') == 'active'),
            'mitigated': sum(1 for a in areas if a.get('status') == 'mitigated'),
            'monitoring': sum(1 for a in areas if a.get('status') == 'monitoring'),
        }

    def _generate_id(self) -> str:
        """生成新薄弱环节ID"""
        existing = [a['id'] for a in self._data.get('areas', [])]
        n = 1
        while f"WA{n:03d}" in existing:
            n += 1
        return f"WA{n:03d}"

    def reload(self):
        self._load()

    # ============================================================
    # [P0-3新增] 语义签名：识别两条描述是否"语义等价"
    # ============================================================
    _CORE_TYPES = [
        # 形态类
        'A类', 'B类', 'C1', 'D2', 'E1', 'E2', 'F1', 'H类',
        '一字板', '普通波动', '宽幅震荡', '温和放量', '冲高回落', '尾盘急拉',
        # 阶段类
        '冰点期', '启动期', '发酵期', '主升期', '高潮期', '降温期', '退潮期', '修复期',
        # 操作/结果类
        '低吸', '追涨', '打板', '止损', '误判', '判断错误', '方向错', '信号',
        '胜率', '准确率', '失败', '错误', '遗漏', '跟风', '共振',
    ]

    def _compute_semantic_signature(self, text: str) -> frozenset:
        """
        从文本中提取核心语义关键词集合，用于判断两条薄弱环节描述是否"语义等价"。
        [P0-3新增]

        算法：在前80字符内查找_CORE_TYPES关键词集合。
        Returns: frozenset of core keywords
        """
        core = set()
        sample = text[:80].lower()
        for kw in self._CORE_TYPES:
            if kw.lower() in sample:
                core.add(kw)
        return frozenset(core)

    # ============================================================
    # 查询接口
    # ============================================================

    def all(self) -> List[Dict[str, Any]]:
        """获取所有薄弱环节"""
        return self._data.get('areas', [])

    def get_active(self) -> List[Dict[str, Any]]:
        """获取所有活跃薄弱环节"""
        return [a for a in self._data.get('areas', []) if a.get('status') == 'active']

    def get_by_category(self, category: str) -> List[Dict[str, Any]]:
        """按类别获取薄弱环节"""
        return [a for a in self._data.get('areas', []) if a.get('category') == category]

    def get_by_id(self, wa_id: str) -> Optional[Dict[str, Any]]:
        """按ID获取薄弱环节"""
        for a in self._data.get('areas', []):
            if a['id'] == wa_id:
                return a
        return None

    def get_high_impact(self) -> List[Dict[str, Any]]:
        """获取高impact的薄弱环节"""
        return [a for a in self._data.get('areas', []) if a.get('impact') == '高']

    def search(self, keyword: str) -> List[Dict[str, Any]]:
        """搜索薄弱环节"""
        keyword = keyword.lower()
        results = []
        for a in self._data.get('areas', []):
            text = ' '.join([
                a.get('description', ''),
                a.get('category', ''),
                ' '.join(a.get('strategies', [])),
            ]).lower()
            if keyword in text:
                results.append(a)
        return results

    # ============================================================
    # 添加/更新接口
    # ============================================================

    def add(
        self,
        description: str,
        category: str,
        impact: str = "中",
        frequency: str = "中",
        strategies: List[str] = None,
        related_chains: List[str] = None,
    ) -> Dict[str, Any]:
        """
        添加新薄弱环节。[P0-3新增] 语义去重：
        签名与已有活跃条目重叠>=2则跳过，返回已有条目。
        """
        # P0-3: 语义去重检查
        new_sig = self._compute_semantic_signature(description)
        if new_sig:
            for existing_area in self._data.get('areas', []):
                if existing_area.get('status') != 'active':
                    continue
                existing_sig = self._compute_semantic_signature(
                    existing_area.get('description', ''))
                overlap = new_sig & existing_sig
                if len(overlap) >= 2:
                    return existing_area  # 语义重复，返回已有条目

        area = {
            'id': self._generate_id(),
            'category': category,
            'description': description,
            'impact': impact,
            'frequency': frequency,
            'strategies': strategies or [],
            'last_triggered': datetime.now().strftime('%Y-%m-%d'),
            'status': 'active',
            'related_chains': related_chains or [],
            'history': [{
                'date': datetime.now().strftime('%Y-%m-%d'),
                'event': '发现薄弱环节',
                'lesson': description,
            }],
        }
        self._data.setdefault('areas', []).append(area)
        self._save()
        return area

    def _extract_core_type(self, text: str) -> str:
        """提取核心类型关键词（用于去重匹配）"""
        for kw in ['C1', 'D2', 'E1', 'E2', 'F1', 'A类', 'B类', 'H类',
                    '高潮期', '退潮期', '冰点期', '升温期', '修复期', '降温期',
                    '一字板', '冲高回落', '尾盘急拉', '低开低走', '温和放量',
                    '跟风', '共振', '板块', '龙头']:
            if kw in text:
                return kw
        return text[:15]

    def add_from_verification_failure(
        self,
        prediction: str,
        actual: str,
        stage: str,
        root_cause: str,
        suggested_fix: str = None,
    ) -> Dict[str, Any]:
        """
        从验证失败中识别并添加薄弱环节。

        [P0-3改进] 多阶段去重匹配：
          Stage 1: 精确匹配 root_cause 完整文本
          Stage 2: 按核心类型匹配（形态名/阶段名/策略词）
          Stage 3: 按 root_cause 全文搜索（description 字段）
        """
        matched_area = None

        # Stage 1: 精确匹配 root_cause 完整文本
        for area in self._data.get('areas', []):
            desc = area.get('description', '')
            if root_cause in desc or desc in root_cause:
                matched_area = area
                break

        # Stage 2: 按核心类型匹配（但 core_type 太短时跳过，避免泛匹配）
        if not matched_area:
            core_type = self._extract_core_type(root_cause)
            # Design-4 fix: core_type == root_cause[:15] 说明没匹配到任何关键词，跳过 Stage 2
            if core_type != root_cause[:15]:  # 有真实关键词才用 Stage 2
                for area in self._data.get('areas', []):
                    desc = area.get('description', '')
                    if core_type in desc:
                        matched_area = area
                        break

        # Stage 3: 按 root_cause 全文搜索
        if not matched_area:
            search_results = self.search(root_cause)
            if search_results:
                matched_area = search_results[0]

        if matched_area:
            today = datetime.now().strftime('%Y-%m-%d')
            new_entry = {
                'date': today,
                'event': f"验证失败: {prediction} → {actual}",
                'lesson': root_cause,
            }
            last = matched_area['history'][-1] if matched_area['history'] else {}
            if last.get('event') != new_entry['event'] or last.get('date') != today:
                matched_area['history'].append(new_entry)
            if len(matched_area['history']) > 20:
                matched_area['history'] = matched_area['history'][-20:]
            matched_area['last_triggered'] = today
            self._save()
            return matched_area

        category = self._infer_category(root_cause, prediction, stage)
        return self.add(
            description=f"[{stage}阶段]{root_cause}。预判={prediction} 实际={actual}",
            category=category,
            impact='高' if stage in ['高潮期', '退潮期'] else '中',
            frequency='高',
            strategies=[suggested_fix] if suggested_fix else [],
        )

    def _infer_category(self, root_cause: str, prediction: str, stage: str) -> str:
        """推断薄弱环节类别"""
        root_lower = root_cause.lower()
        pred_lower = prediction.lower()

        if any(kw in root_lower for kw in ['形态', 'f30', '振幅', '拉升', '脉冲']):
            return '形态判断'
        elif any(kw in root_lower for kw in ['情绪', '冰点', '高潮', 'deg']):
            return '情绪识别'
        elif any(kw in root_lower for kw in ['仓位', '位置', '高低']):
            return '仓位管理'
        elif any(kw in root_lower for kw in ['跟风', '龙头', '孤身', '板块']):
            return '选股'
        elif any(kw in root_lower for kw in ['时机', '买入', '卖出', '次日']):
            return '时机'
        elif any(kw in pred_lower for kw in ['晋级', '断层', '连板']):
            return '选股'
        return '因果链'

    def update_status(self, wa_id: str, status: str, lesson: str = None) -> bool:
        """
        更新薄弱环节状态。
        status: 'active' / 'mitigated' / 'monitoring'
        """
        area = self.get_by_id(wa_id)
        if not area:
            return False

        area['status'] = status
        area['last_triggered'] = datetime.now().strftime('%Y-%m-%d')
        if lesson:
            area['history'].append({
                'date': datetime.now().strftime('%Y-%m-%d'),
                'event': f"状态更新: {status}",
                'lesson': lesson,
            })
        self._save()
        return True

    def add_strategy(self, wa_id: str, strategy: str) -> bool:
        """为薄弱环节添加应对策略"""
        area = self.get_by_id(wa_id)
        if not area:
            return False
        if strategy not in area.get('strategies', []):
            area.setdefault('strategies', []).append(strategy)
            area['last_triggered'] = datetime.now().strftime('%Y-%m-%d')
            self._save()
        return True

    # ============================================================
    # 关联
    # ============================================================

    def link_chain(self, wa_id: str, chain_theme: str) -> bool:
        """关联薄弱环节与因果链"""
        area = self.get_by_id(wa_id)
        if not area:
            return False
        if chain_theme not in area.get('related_chains', []):
            area.setdefault('related_chains', []).append(chain_theme)
            self._save()
        return True

    # ============================================================
    # 统计
    # ============================================================

    def get_statistics(self) -> Dict[str, Any]:
        """获取薄弱环节统计（直接从 areas 计算，不依赖 counts 字段）"""
        areas = self._data.get('areas', [])

        # 直接从 areas 计算，防止 counts 被污染导致数据错误
        by_category = {}
        for a in areas:
            cat = a.get('category', '未分类')
            by_category[cat] = by_category.get(cat, 0) + 1

        return {
            'total': len(areas),
            'active': sum(1 for a in areas if a.get('status') == 'active'),
            'mitigated': sum(1 for a in areas if a.get('status') == 'mitigated'),
            'monitoring': sum(1 for a in areas if a.get('status') == 'monitoring'),
            'by_category': by_category,
            'last_updated': self._data.get('last_updated', ''),
        }
