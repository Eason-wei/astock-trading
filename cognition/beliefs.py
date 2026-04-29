"""
beliefs.py - 版本化信念存储系统
=====================================
职责：
  - 读取/写入/查询信念（cognitions.json）
  - 追踪信念版本历史
  - 检测新旧信念的冲突
  - 从step7/8验证结果中识别需要更新的信念

信念结构（cognitions.json格式）：
  {
    "信念名": {
      "current": "当前版本的完整描述",
      "version": 78,
      "updated": "2026-04-20",
      "conflict": "与旧版本的冲突描述（可选）",
      "old_belief": "旧版本的错误认知（可选）",
      "source": "来源（可选）",
      "trigger": "触发条件（可选）",
      "mechanism": "机制（可选）",
      "outcome": "结果（可选）",
      "when_works": "生效条件（可选）",
      "when_fails": "失效条件（可选）",
    }
  }
"""

import json
import copy
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime


class BeliefStore:
    """信念存储管理器"""

    def __init__(self, store_path: str = None):
        if store_path is None:
            store_path = Path.home() / ".hermes/trading_study/cognitions.json"
        self.path = Path(store_path)
        self._cache: Dict[str, Any] = None
        self._load()

    def _load(self):
        """加载信念库（自动迁移历史数据）"""
        if self.path.exists():
            with open(self.path, 'r', encoding='utf-8') as f:
                content = f.read()
                self._cache = json.loads(content) if content else {}
                if not isinstance(self._cache, dict):
                    self._cache = {}
        else:
            self._cache = {}

        # 自动迁移：补全历史数据缺失的字段
        migrated = self._migrate_if_needed()
        if migrated:
            self._save()  # 迁移后立即持久化

    def _migrate_if_needed(self) -> int:
        """
        检查并迁移缺失字段的历史条目。
        每次 _load 时调用，确保旧数据自动补全。
        Returns: 迁移的条目数量
        """
        migrated = 0
        for key, entry in list(self._cache.items()):
            changed = False

            # 1. 补 confidence（None → 0.6，version>50 → 0.75）
            if entry.get('confidence') is None:
                entry['confidence'] = 0.75 if entry.get('version', 0) > 50 else 0.6
                entry['_confidence_notes'] = '迁移补录: 历史数据无置信度记录'
                changed = True

            # 2. 补 _semantic_conflicts
            if '_semantic_conflicts' not in entry:
                entry['_semantic_conflicts'] = []
                changed = True

            # 3. 补 _confidence_notes
            if '_confidence_notes' not in entry:
                entry['_confidence_notes'] = '迁移补录: 历史数据无置信度记录'
                changed = True

            if changed:
                self._cache[key] = entry
                migrated += 1

        return migrated

    def _save(self):
        """原子写入：先写临时文件再 rename，避免中途崩溃污染原文件"""
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                          dir=self.path.parent, delete=False, encoding='utf-8')
        try:
            json.dump(self._cache, tmp, ensure_ascii=False, indent=2)
            tmp.close()
            os.replace(tmp.name, self.path)  # atomic on POSIX
        except Exception:
            if os.path.exists(tmp.name):
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
            raise

    def reload(self):
        """强制重新加载"""
        self._load()

    # ============================================================
    # [P0-1新增] 语义签名：识别两条描述是否"语义等价"
    # ============================================================
    _CORE_TYPES = [
        # 形态类
        'A类', 'B类', 'C1', 'D2', 'E1', 'E2', 'F1', 'H类',
        '一字板', '普通波动', '宽幅震荡', '温和放量', '冲高回落', '尾盘急拉',
        # 阶段类
        '冰点期', '启动期', '发酵期', '主升期', '高潮期', '降温期', '退潮期', '修复期',
        # 操作类
        '低吸', '追涨', '打板', '止损', '空仓', '轻仓', '重仓', '满仓',
        '看多', '看空', '胜率', '准确率',
    ]

    def _compute_semantic_signature(self, text: str) -> frozenset:
        """
        从文本中提取核心语义关键词集合，用于判断两条描述是否"语义等价"。
        [P0-1新增]

        算法：在前100字符内查找_CORE_TYPES关键词集合（控制复杂度）。
        Returns: frozenset of core keywords
        """
        core = set()
        sample = text[:100].lower()
        for kw in self._CORE_TYPES:
            if kw.lower() in sample:
                core.add(kw)
        return frozenset(core)

    # ===== 查询接口 =====

    def get(self, belief_key: str) -> Optional[Dict[str, Any]]:
        """
        获取信念的最新版本
        Returns: None if not found
        """
        return self._cache.get(belief_key)

    def get_current(self, belief_key: str) -> Optional[str]:
        """获取信念的当前描述文本"""
        entry = self.get(belief_key)
        return entry.get('current') if entry else None

    def get_all_keys(self) -> List[str]:
        """获取所有信念名称"""
        return list(self._cache.keys())

    def get_all(self) -> Dict[str, Any]:
        """获取信念库完整快照（副本），避免直接暴露内部缓存"""
        import copy
        return copy.deepcopy(self._cache)

    def get_version(self, belief_key: str) -> int:
        """获取信念版本号"""
        entry = self.get(belief_key)
        return entry.get('version', 0) if entry else 0

    def get_conflicts(self, belief_key: str) -> Optional[str]:
        """获取信念的冲突描述"""
        entry = self.get(belief_key)
        return entry.get('conflict') if entry else None

    def get_trigger_mechanism_outcome(self, belief_key: str) -> Dict[str, str]:
        """获取信念的TMO结构"""
        entry = self.get(belief_key)
        if not entry:
            return {}
        return {
            'trigger': entry.get('trigger', ''),
            'mechanism': entry.get('mechanism', ''),
            'outcome': entry.get('outcome', ''),
            'when_works': entry.get('when_works', ''),
            'when_fails': entry.get('when_fails', ''),
        }

    def search(self, keyword: str) -> List[str]:
        """
        搜索信念名称或描述中包含关键词的信念
        """
        results = []
        keyword = keyword.lower()
        for key, entry in self._cache.items():
            if keyword in key.lower():
                results.append(key)
                continue
            current = entry.get('current', '')
            if keyword in current.lower():
                results.append(key)
        return results

    def search_by_theme(self, theme: str) -> List[Dict[str, Any]]:
        """
        按theme字段搜索因果链（需要读logic_chains）
        """
        from .causal_chains import CausalChainStore
        chains = CausalChainStore()
        return chains.get_by_theme(theme)

    def query_by_stage(self, stage: str) -> List[Dict[str, Any]]:
        """
        查询特定市场阶段相关的信念
        stage: '冰点期'/'退潮期'/'升温期'/'高潮期'/'降温期'/'修复期'
        """
        relevant = []
        stage_keywords = {
            '冰点期': ['冰点', '冰点日', '冰点期'],
            '退潮期': ['退潮', '高位', '跟风'],
            '高潮期': ['高潮', '高位', '一字板'],
            '升温期': ['升温', '启动'],
            '修复期': ['修复', '反弹'],
        }
        keywords = stage_keywords.get(stage, [stage])
        for key, entry in self._cache.items():
            current = entry.get('current', '')
            for kw in keywords:
                if kw in current:
                    relevant.append({'key': key, **entry})
                    break
        return relevant

    # ===== 更新接口 =====

    def upsert_from_verification(
        self,
        prediction_key: str,
        prediction: str,
        actual: str,
        correct: bool,
        market_stage: str = None,
        lesson: str = None,
        confidence: float = None,   # D-①: 估算数据降权
    ) -> Dict[str, Any]:
        """
        从step7/8验证结果中更新/新增信念

        这个是核心接口——验证后调用这个来更新认知体系
        """
        key = f"验证_{prediction_key}_{datetime.now().strftime('%Y%m%d')}"

        if correct:
            content = f"✅验证通过：{prediction} → 实际={actual}。{lesson or ''}"
        else:
            content = f"❌验证失败：预判={prediction} 实际={actual}。认知需修正。{lesson or ''}"

        if market_stage:
            content = f"[{market_stage}阶段] {content}"

        # D-①: 估算数据降权标记
        source = "step7/8验证"
        if confidence is not None and confidence < 1.0:
            source = f"{source}[估算数据:confidence={confidence}]"

        return self.update(
            belief_key=key,
            new_content=content,
            source=source,
            when_works=prediction if correct else None,
            when_fails=prediction if not correct else None,
            confidence=confidence,
            _correct=correct,   # P0-2: 传递验证结果用于置信度调整
        )

    def get_recent_updates(self, days: int = 7) -> List[Dict[str, Any]]:
        """获取最近N天更新的信念"""
        cutoff = datetime.now().timestamp() - days * 86400
        recent = []
        for key, entry in self._cache.items():
            try:
                updated = datetime.strptime(entry.get('updated', '1970-01-01'), '%Y-%m-%d')
                if updated.timestamp() >= cutoff:
                    recent.append({'key': key, **entry})
            except (ValueError, TypeError):
                pass  # 跳过格式错误的日期，不静默吞所有异常
        return sorted(recent, key=lambda x: x.get('updated', ''), reverse=True)

    def get_statistics(self) -> Dict[str, Any]:
        """获取信念库统计"""
        total = len(self._cache)
        with_conflict = sum(1 for e in self._cache.values() if e.get('conflict'))
        with_tmo = sum(1 for e in self._cache.values() if e.get('trigger'))
        versions = [e.get('version', 0) for e in self._cache.values()]
        return {
            'total': total,
            'with_conflict': with_conflict,
            'with_tmo_structure': with_tmo,
            'max_version': max(versions) if versions else 0,
            'avg_version': sum(versions) / total if total else 0,
        }

    # ============================================================
    # P0-crfix: 语义冲突检测（跨信念）
    # ============================================================
    # 核心观点关键词（用于检测语义矛盾）
    _SEMANTIC_ANCHORS = [
        '满仓', '轻仓', '空仓', '重仓', '半仓',
        '追涨', '低吸', '打板', '止损', '不止损',
        '看多', '看空', '做多', '做空', '逆势',
        '顺势', '共振', '独立', '跟风',
        '安全', '危险', '禁止', '必须',
    ]

    def _extract_claims(self, text: str) -> Dict[str, tuple]:
        """
        从信念文本中提取核心观点。
        Returns: {anchor_keyword: (polarity, context_fragment)}
        polarity: +1(正向) / -1(负向) / 0(中性)
        """
        anchors = {}
        text_lower = text.lower()
        for kw in self._SEMANTIC_ANCHORS:
            if kw in text_lower:
                # 判断上下文极性：找相邻的修饰词
                idx = text_lower.index(kw)
                ctx = text_lower[max(0, idx-4):idx+6]
                polarity = 0
                for pos_kw in ['安全', '必须', '可以', '看多', '正确', '有效', '做多', '顺势']:
                    if pos_kw in ctx:
                        polarity = 1
                        break
                for neg_kw in ['危险', '禁止', '不能', '看空', '错误', '无效', '做空', '逆势']:
                    if neg_kw in ctx:
                        polarity = -1
                        break
                anchors[kw] = (polarity, ctx)
        return anchors

    def _semantic_conflicts_with(self, new_claims: Dict[str, tuple], exclude_key: str = None) -> List[Dict[str, Any]]:
        """
        检查新信念的核心观点是否与已有信念矛盾。
        矛盾条件：同一 anchor_keyword 极性相反。
        Returns: [{existing_key, anchor, new_polarity, existing_polarity, existing_belief_text}, ...]
        """
        conflicts = []
        for key, entry in self._cache.items():
            if key == exclude_key:
                continue
            existing_claims = self._extract_claims(entry.get('current', ''))
            for anchor, (new_pol, new_ctx) in new_claims.items():
                if anchor in existing_claims:
                    existing_pol, existing_ctx = existing_claims[anchor]
                    if new_pol != 0 and existing_pol != 0 and new_pol != existing_pol:
                        conflicts.append({
                            'existing_key': key,
                            'anchor': anchor,
                            'new_polarity': new_pol,
                            'existing_polarity': existing_pol,
                            'new_context': new_ctx,
                            'existing_context': existing_ctx,
                            'existing_text': entry.get('current', '')[:80],
                        })
        return conflicts

    # ============================================================
    # [P0-crfix] handle_search_result: cron脚本的唯一写入入口
    # 串联：语义冲突检测 + 置信度追踪 + JSON持久化
    # ============================================================
    _SOURCE_WEIGHTS = {
        'Tavily搜索': 0.8,       # 网络文章，有噪音
        '从已有链提炼': 0.9,      # 基于已有验证过的链二次提炼，可靠性高
        'step7/8验证': 1.0,       # 实战验证，最可靠
        '实战复盘': 1.0,
    }

    def handle_search_result(
        self,
        theme: str,
        new_content: str,
        source: str = 'Tavily搜索',
        old_belief: str = None,
    ) -> Dict[str, Any]:
        """
        cron脚本写入cognitions.json的统一入口。

        自动完成：
        1. 语义冲突检测（跨信念极性矛盾）
        2. 置信度估算（新内容 vs 已有置信度，加权融合）
        3. 覆盖式写入JSON

        Returns: {
            'updated': bool,
            'version': int,
            'conflict': str or None,
            'semantic_conflicts': [...],
            'confidence': float,
            'confidence_delta': float,
        }
        """
        existing = self._cache.get(theme)
        new_version = 1
        conflict = None

        if existing:
            new_version = existing.get('version', 0) + 1

        # 1. 语义冲突检测（跨信念）
        new_claims = self._extract_claims(new_content)
        semantic_conflicts = self._semantic_conflicts_with(new_claims, exclude_key=theme)

        # 2. 内容级冲突（与旧信念的冲突）
        if old_belief and old_belief != new_content:
            # 检测新旧内容是否有条件词/转折词暗示的冲突
            negations = ['但', '然而', '只是', '不过', '未必', '不一定']
            conditions = ['前提是', '关键看', '核心是', '条件是']
            text = f"{old_belief} {new_content}"
            has_neg = any(w in text for w in negations)
            has_cond = any(w in text for w in conditions)
            if has_neg or has_cond:
                conflict = f"内容冲突: {old_belief[:40]} → {new_content[:60]}"
            else:
                conflict = f"版本升级 {existing.get('version', 1)}→{new_version}，内容已变更"

        # 合并语义冲突到conflict字段
        if semantic_conflicts:
            sem_conflict_str = '; '.join([
                f"与「{c['existing_key']}」在「{c['anchor']}」{'正' if c['new_polarity']>0 else '负'}vs{'正' if c['existing_polarity']>0 else '负'}"
                for c in semantic_conflicts[:3]
            ])
            conflict = (conflict + f" | 语义冲突: {sem_conflict_str}") if conflict else f"语义冲突: {sem_conflict_str}"

        # 3. 置信度追踪
        # 新内容的初始置信度由来源决定
        source_weight = self._SOURCE_WEIGHTS.get(source, 0.8)
        # 已有置信度
        existing_conf = existing.get('confidence') if existing else None
        existing_source = existing.get('source') if existing else None

        if existing_conf is not None:
            if existing_source == source:
                # 同来源持续确认 → 置信度上浮（向1.0靠拢）
                if existing.get('current') == new_content:
                    # 内容完全一致，确认强化
                    new_conf = min(0.99, existing_conf + 0.03)
                    confidence_note = f"同源确认+0.03: {existing_conf:.3f}→{new_conf:.3f}"
                elif conflict:
                    # 同源但有冲突，说明该来源内部有分歧，降权
                    new_conf = max(0.40, existing_conf * 0.80)
                    confidence_note = f"同源冲突×0.80: {existing_conf:.3f}→{new_conf:.3f}"
                else:
                    # 同源内容更新，无冲突，轻微上浮
                    new_conf = min(0.99, existing_conf + 0.02)
                    confidence_note = f"同源更新+0.02: {existing_conf:.3f}→{new_conf:.3f}"
            elif conflict:
                # 跨来源 + 有冲突 → 较大惩罚
                new_conf = max(0.40, existing_conf * 0.75)
                confidence_note = f"跨源冲突×0.75: {existing_conf:.3f}→{new_conf:.3f}"
            else:
                # Design-5 fix: 跨来源无冲突时，取 min（保守），而非 max（会拉高）
                new_conf = min(existing_conf * 0.95, source_weight)
                confidence_note = f"跨源切换×0.95→min: {existing_conf:.3f}×0.95={existing_conf*0.95:.3f} vs {source_weight} → {new_conf:.3f}"
        else:
            # 全新主题，直接用来源权重
            new_conf = source_weight
            confidence_note = f"新主题初始: {source_weight}"

        new_conf = round(new_conf, 3)

        # 4. 写入
        self._cache[theme] = {
            'current': new_content[:200],
            'version': new_version,
            'updated': datetime.now().strftime('%Y-%m-%d'),
            'conflict': conflict,
            'old_belief': old_belief or (existing.get('current') if existing else None),
            'source': source,
            'confidence': new_conf,
            '_confidence_note': confidence_note,  # P0-crfix: 置信度变化原因（可追溯）
            # 保留已有TMO结构
            'trigger': existing.get('trigger') if existing else None,
            'mechanism': existing.get('mechanism') if existing else None,
            'outcome': existing.get('outcome') if existing else None,
            'when_works': existing.get('when_works') if existing else None,
            'when_fails': existing.get('when_fails') if existing else None,
            # P0-crfix: 完整语义冲突结构也持久化（不只是拼成字符串）
            '_semantic_conflicts': semantic_conflicts,  # [{anchor, new_polarity, existing_polarity, existing_text, ...}, ...]
        }
        self._save()

        delta = round(new_conf - (existing_conf or 0.0), 3) if existing_conf is not None else 0.0

        return {
            'updated': True,
            'version': new_version,
            'conflict': conflict,
            'semantic_conflicts': semantic_conflicts,
            'confidence': new_conf,
            'confidence_delta': delta,
            'confidence_note': confidence_note,
            'source': source,
        }

    def update(
        self,
        belief_key: str,
        new_content: str,
        source: str = None,
        old_content: str = None,
        trigger: str = None,
        mechanism: str = None,
        outcome: str = None,
        when_works: str = None,
        when_fails: str = None,
        confidence: float = None,   # D-①: 估算数据降权标记
        _correct: bool = None,     # P0-2: 验证是否正确（用于置信度更新）
    ) -> Dict[str, Any]:
        """
        更新信念（新版本）

        Args:
            belief_key: 信念名称
            new_content: 新版本内容
            source: 来源（如 'Tavily搜索'/'实战复盘'/'4/03冰点实战'）
            old_content: 如果是修正旧认知，填写被修正的旧内容
            trigger/mechanism/outcome: TMO结构
            when_works/when_fails: 适用/失效条件
            _correct: P0-2: 验证结果（用于信念置信度更新）

        Returns:
            更新结果摘要

        Note:
            updated 字段记录"写入日期"（YYYY-MM-DD），不是数据发生日期。
            要按数据日期查询，解析 current 内容中的实际值。
        """
        existing = self._cache.get(belief_key)
        new_version = 1
        conflict = None

        if existing:
            new_version = existing['version'] + 1
            old_belief_text = existing.get('current', '')
            if old_content and old_content != old_belief_text:
                # 旧内容不匹配但有旧内容记录
                conflict = f"旧版本({existing['version']}): '{old_belief_text[:50]}...' → 新版本: '{new_content[:50]}...'"
            elif new_content != old_belief_text:
                # 内容变化，检测冲突
                conflict = f"版本升级 {existing['version']}→{new_version}，内容已变更"

        today = datetime.now().strftime('%Y-%m-%d')

        # P0-1: 语义冲突检测（跨信念）
        new_claims = self._extract_claims(new_content)
        semantic_conflicts = self._semantic_conflicts_with(new_claims, exclude_key=belief_key)
        if semantic_conflicts:
            conflict_details = '; '.join([
                f"与「{c['existing_key']}」在「{c['anchor']}」上极性冲突({'正' if c['new_polarity']>0 else '负'} vs {'正' if c['existing_polarity']>0 else '负'})"
                for c in semantic_conflicts
            ])
            conflict = (conflict + f" | 语义冲突: {conflict_details}") if conflict else f"语义冲突: {conflict_details}"

        # Bug-1 fix: 置信度三分支（去除冗余 else，current_conf is None 时用 source_weight）
        current_conf = existing.get('confidence') if existing else None
        if _correct is not None and existing and existing.get('version', 1) > 1:
            prev = current_conf if current_conf is not None else 0.6
            if _correct:
                new_conf = min(0.99, prev + 0.05)   # 验证成功 +5%
            else:
                new_conf = max(0.10, prev - 0.10)   # 验证失败 -10%
            confidence = round(new_conf, 3)
        elif current_conf is not None:
            confidence = current_conf
        else:
            # Bug-1 fix: 新信念首次写入，confidence 不能为 None，用来源权重
            confidence = self._SOURCE_WEIGHTS.get(source, 0.8) if source else 0.6

        # P0-1: 跨信念语义签名检测（防止不同key描述同一认知）
        # 对新信念：如果_signature与其他已有信念冲突，记录但允许写入
        new_sig = self._compute_semantic_signature(new_content)
        cross_sig_conflicts = []
        for other_key, other_entry in self._cache.items():
            if other_key == belief_key:
                continue
            other_sig = self._compute_semantic_signature(other_entry.get('current', ''))
            # 签名重叠>=2个关键词 → 潜在重复
            overlap = new_sig & other_sig
            if len(overlap) >= 2:
                cross_sig_conflicts.append((other_key, other_sig, overlap))
        # Bug-3 fix: `s` 是完整 other_sig，`o` 才是交集，修复格式化变量名
        if cross_sig_conflicts and not existing:
            sig_conflict_note = " | ⚠️潜在跨信念重复: " + "; ".join([
                f"与「{k}」重叠{list(o)[0]}..." for k, s, o in cross_sig_conflicts
            ])
            conflict = (conflict or "") + sig_conflict_note

        self._cache[belief_key] = {
            'current': new_content,
            'version': new_version,
            'updated': today,
            'conflict': conflict,
            'old_belief': existing.get('current') if (existing and conflict and not old_content) else None,
            'source': source or (existing.get('source') if existing else None),  # P1-②: guard existing
            'trigger': trigger or (existing.get('trigger') if existing else None),
            'mechanism': mechanism or (existing.get('mechanism') if existing else None),
            'outcome': outcome or (existing.get('outcome') if existing else None),
            'when_works': when_works or (existing.get('when_works') if existing else None),
            'when_fails': when_fails or (existing.get('when_fails') if existing else None),
            'confidence': confidence,
            # Bug-2 fix: update() 写入时也要包含 _semantic_conflicts（与 handle_search_result() 一致）
            '_semantic_conflicts': semantic_conflicts,
        }

        self._save()
        result = {
            'belief_key': belief_key,
            'old_version': new_version - 1,
            'new_version': new_version,
            'conflict': conflict,
            'semantic_conflicts': semantic_conflicts,   # P0-1
        }
        return result
