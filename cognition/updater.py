"""
updater.py - 认知更新协调器
=====================================
职责：
  - 连接step7/8验证结果 → 更新信念库/因果链/薄弱环节
  - 统一入口：receive_verification_result()
  - 智能判断：何时更新信念、何时添加因果链、何时记录薄弱环节
  - 避免重复更新：基于日期+内容去重

使用方式：
  from cognition import CognitionUpdater
  updater = CognitionUpdater()

  # step7验证后调用
  updater.receive_verification_result(
      prediction_key='D2形态T+1必跌',
      prediction='T+1收盘-3%以下',
      actual='T+1收盘-5%',
      correct=False,
      market_stage='高潮期',
      lesson='D2在高潮期加速了失败效应',
      morphology_type='D2',
  )
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from pathlib import Path

from .beliefs import BeliefStore
from .causal_chains import CausalChainStore
from .weak_areas import WeakAreasStore


class CognitionUpdater:
    """
    认知更新协调器

    将step7/8的验证结果转化为认知体系的更新。
    单一入口，分流到三个子存储。
    """

    # 低价值更新阈值（内容太短或太模糊则跳过）
    MIN_LESSON_LEN = 5
    MIN_TRIGGER_LEN = 3

    def __init__(
        self,
        beliefs_path: str = None,
        chains_path: str = None,
        weak_areas_path: str = None,
    ):
        self.beliefs = BeliefStore(beliefs_path)
        self.chains = CausalChainStore(chains_path)
        self.weak_areas = WeakAreasStore(weak_areas_path)

    @property
    def _today_key(self) -> str:
        """Design-5 fix: 动态计算，进程长时间运行时跨午夜也不会用错日期"""
        return datetime.now().strftime('%Y%m%d')

    # ===== 核心入口 =====

    def receive_verification_result(
        self,
        prediction_key: str,
        prediction: str,
        actual: str,
        correct: bool,
        market_stage: str = None,
        lesson: str = None,
        morphology_type: str = None,
        trigger: str = None,
        mechanism: str = None,
        outcome: str = None,
        root_cause: str = None,
        suggested_fix: str = None,
        confidence: float = None,   # D-①: 估算数据降权传参
    ) -> Dict[str, Any]:
        """
        接收step7/8验证结果，智能分发到各存储

        Args:
            prediction_key: 预判标识（如 'D2形态T+1必跌'）
            prediction: 预判内容
            actual: 实际结果
            correct: 是否正确
            market_stage: 市场阶段
            lesson: 从本次验证学到的教训
            morphology_type: 关联的形态类型（A/B/C/D/E/F + 数字）
            trigger: 因果链触发条件（可选，从lesson提取）
            mechanism: 因果链机制（可选）
            outcome: 因果链结果（可选）
            root_cause: 失败根本原因（传入weak_areas）
            suggested_fix: 建议的修复方法（传入weak_areas）

        Returns:
            更新结果摘要
        """
        results = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'prediction_key': prediction_key,
            'correct': correct,
            'actions': [],
        }

        # 1. 更新信念库（估算数据降权：confidence=0.3）
        belief_result = self._update_belief(
            prediction_key, prediction, actual, correct, market_stage, lesson, morphology_type, confidence
        )
        if belief_result:
            results['actions'].append(('belief', belief_result))

        # 2. 如果提供了TMO结构，添加因果链
        if trigger and len(trigger) >= self.MIN_TRIGGER_LEN:
            chain_result = self._add_causal_chain(
                trigger, mechanism, outcome, correct, market_stage, morphology_type
            )
            if chain_result:
                results['actions'].append(('chain', chain_result))

        # 3. 如果验证失败且有根本原因，记录薄弱环节
        if not correct and root_cause:
            weak_result = self._record_weak_area(
                prediction, actual, market_stage, root_cause, suggested_fix, morphology_type
            )
            if weak_result:
                results['actions'].append(('weak_area', weak_result))

        return results

    # ===== 子更新器 =====

    def _update_belief(
        self,
        prediction_key: str,
        prediction: str,
        actual: str,
        correct: bool,
        market_stage: str,
        lesson: str,
        morphology_type: str,
        confidence: float = None,   # D-①: 估算数据降权
    ) -> Optional[Dict[str, Any]]:
        """更新信念库"""
        # 构建信念内容
        content_parts = [f"[{market_stage or '未知阶段'}]"]
        content_parts.append(f"预判={prediction}")
        content_parts.append(f"实际={actual}")
        content_parts.append("✅验证通过" if correct else "❌验证失败")
        if lesson and len(lesson) >= self.MIN_LESSON_LEN:
            content_parts.append(f"教训={lesson}")
        if morphology_type:
            content_parts.append(f"关联形态={morphology_type}")

        content = " | ".join(content_parts)

        # 如果验证正确，是否需要更新"正确"信念的置信度？
        # 如果验证失败，必须记录
        if not correct or lesson:
            result = self.beliefs.upsert_from_verification(
                prediction_key=prediction_key,
                prediction=prediction,
                actual=actual,
                correct=correct,
                market_stage=market_stage,
                lesson=lesson,
                confidence=confidence,  # D-①修复：forward confidence to BeliefStore
            )
            return result
        return None

    def _add_causal_chain(
        self,
        trigger: str,
        mechanism: str,
        outcome: str,
        correct: bool,
        market_stage: str,
        morphology_type: str,
    ) -> Optional[Dict[str, Any]]:
        """添加因果链"""
        theme_parts = ['验证']
        if market_stage:
            theme_parts.append(market_stage)
        if morphology_type:
            theme_parts.append(morphology_type)
        theme_parts.append('✅' if correct else '❌')
        theme = '_'.join(theme_parts)

        verdict_tag = "已验证" if correct else "待修正"
        return self.chains.add(
            trigger=trigger,
            mechanism=mechanism or '',
            outcome=outcome or '',
            theme=f"{theme}_{verdict_tag}",
            source_title=f"step7/8验证_{self._today_key}",
        )

    def _record_weak_area(
        self,
        prediction: str,
        actual: str,
        market_stage: str,
        root_cause: str,
        suggested_fix: str,
        morphology_type: str,
    ) -> Optional[Dict[str, Any]]:
        """记录薄弱环节"""
        return self.weak_areas.add_from_verification_failure(
            prediction=prediction,
            actual=actual,
            stage=market_stage or '未知',
            root_cause=root_cause,
            suggested_fix=suggested_fix or '',
        )

    # ===== 批量处理 =====

    # Design-3 fix: 已知有效 key 白名单，防止外部 dict 含未知 key 导致 TypeError
    _VALID_VERIFICATION_KEYS = frozenset([
        'prediction_key', 'prediction', 'actual', 'correct',
        'market_stage', 'lesson', 'morphology_type',
        'trigger', 'mechanism', 'outcome',
        'root_cause', 'suggested_fix', 'confidence',
    ])

    def receive_batch_results(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        批量接收验证结果
        results: List[receive_verification_result(...) 格式的dict]
        """
        summary = {'total': len(results), 'correct': 0, 'failed': 0, 'updates': [], 'skipped': 0}
        for r in results:
            # Design-3 fix: 只传有效 key，丢弃未知字段
            safe_r = {k: v for k, v in r.items() if k in self._VALID_VERIFICATION_KEYS}
            if len(safe_r) != len(r):
                summary['skipped'] += 1  # 记录被丢弃的字段
            if safe_r.get('correct'):
                summary['correct'] += 1
            else:
                summary['failed'] += 1
            upd = self.receive_verification_result(**safe_r)
            summary['updates'].append(upd)
        return summary

    # ===== 查询接口（透传） =====

    def query_beliefs(self, keyword: str = None, stage: str = None) -> List[Dict[str, Any]]:
        """查询信念"""
        if stage:
            return self.beliefs.query_by_stage(stage)
        if keyword:
            return [{'key': k, **v} for k, v in
                    {k: self.beliefs.get(k) for k in self.beliefs.search(keyword)}.items()]
        return [{'key': k, **v} for k, v in self.beliefs.get_all().items()]

    def query_chains(self, theme: str = None, stage: str = None) -> List[Dict[str, Any]]:
        """查询因果链"""
        if theme:
            return self.chains.get_by_theme(theme)
        if stage:
            return self.chains.get_by_stage(stage)
        return self.chains.all_chains()

    def query_weak_areas(self, active_only: bool = False) -> List[Dict[str, Any]]:
        """查询薄弱环节"""
        if active_only:
            return self.weak_areas.get_active()
        return self.weak_areas.all()

    # ===== 统计 =====

    def get_system_status(self) -> Dict[str, Any]:
        """获取认知体系整体状态"""
        return {
            'beliefs': self.beliefs.get_statistics(),
            'causal_chains': self.chains.get_statistics(),
            'weak_areas': self.weak_areas.get_statistics(),
            'last_update': datetime.now().strftime('%Y-%m-%d %H:%M'),
        }
