"""
predictor.py - T+1 预测引擎
============================
职责：
  1. predict()：单只股票 T+1 预测（形态 + 市场阶段 → 结论）
  2. predict_batch()：批量预测

预测规则（5条核心规则代码化）：
  C1 × 高潮期 → T+1 必跌（高潮地雷）
  F1 → T+1 最安全（3/3胜率）
  D2 × 高潮/退潮/降温期 → T+1 必跌
  A类 → 最强形态，继续看多
  板块共振 → +15% 置信度加成
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional

from .types import Morphology, MorphologyFeatures
from .accuracy_tracker import AccuracyTracker


# ============================================================
# 配置加载（运行时读取 JSON，不硬编码）
# ============================================================

def _load_config() -> Dict[str, Any]:
    """加载形态配置文件"""
    config_path = Path(__file__).parent / 'config' / 'morphology_config.json'
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    # Fallback：内联配置（保证predictor独立可用）
    return _INLINE_CONFIG


def _get_morph_config(morph: Morphology) -> Dict[str, Any]:
    """获取单个形态的基础配置"""
    cfg = _load_config()
    return cfg.get('morphologies', {}).get(morph.value, {})


def _get_stage_override(morph: Morphology, stage: str) -> Optional[Dict[str, Any]]:
    """获取某阶段对某形态的特殊覆盖配置"""
    cfg = _load_config()
    stage_overrides = cfg.get('stage_overrides', {})
    if stage in stage_overrides and morph.value in stage_overrides[stage]:
        return stage_overrides[stage][morph.value]
    return None


# 内联配置（JSON文件不存在时的兜底）
_INLINE_CONFIG = {
    'morphologies': {
        'A类一字板': {'name': 'A类一字板', 't1_bias': 'positive', 't1_confidence': 0.85, 'risk_level': '极低', 'key_indicator': '成交在开盘前30分已完成，盘中无量'},
        'B类正常涨停': {'name': 'B类正常涨停', 't1_bias': 'positive', 't1_confidence': 0.65, 'risk_level': '中低'},
        'C1冲高回落': {'name': 'C1冲高回落', 't1_bias': 'negative', 't1_confidence': 0.75, 'risk_level': '高'},
        'D1低开低走': {'name': 'D1低开低走', 't1_bias': 'negative', 't1_confidence': 0.70, 'risk_level': '高'},
        'D2尾盘急拉': {'name': 'D2尾盘急拉', 't1_bias': 'negative', 't1_confidence': 0.80, 'risk_level': '高'},
        'E1普通波动': {'name': 'E1普通波动', 't1_bias': 'neutral', 't1_confidence': 0.45, 'risk_level': '中'},
        'E2宽幅震荡': {'name': 'E2宽幅震荡', 't1_bias': 'neutral', 't1_confidence': 0.50, 'risk_level': '中'},
        'F1温和放量': {'name': 'F1温和放量稳步推进', 't1_bias': 'positive', 't1_confidence': 0.88, 'risk_level': '低'},
        'H横向整理': {'name': 'H横向整理', 't1_bias': 'neutral', 't1_confidence': 0.40, 'risk_level': '高'},
    }
}


# ============================================================
# T1 Predictor
# ============================================================

class T1Predictor:
    # P2-1: 提取为类常量，消除重复定义
    _SECTOR_BOOST_MAP = {
        Morphology.E1: 0.20,
        Morphology.E2: 0.18,
        Morphology.B:  0.10,
        Morphology.C1: 0.12,
        Morphology.D1: 0.05,
        Morphology.H:  0.05,
    }

    """
    T+1 预测引擎（单一职责：预测）

    用法：
        predictor = T1Predictor()
        result = predictor.predict(features, morphology, '高潮期', sector_strength=0.8)

    准确率说明：
        confidence 不再是 JSON 里的静态值，而是从 accuracy_tracker
        实时查询该 (形态×阶段) 的真实历史准确率（贝叶斯平滑）。
        方向（positive/negative/neutral）由 JSON 规则决定，不受影响。
    """

    # 类级别单例 tracker（所有实例共享同一个 tracker）
    _tracker: AccuracyTracker = None

    def __init__(self):
        if T1Predictor._tracker is None:
            T1Predictor._tracker = AccuracyTracker()

    # ============================================================
    # 置信度：从 tracker 实时查询，而非 JSON 静态值
    # ============================================================

    def _get_confidence(self, morphology: Morphology, market_stage: str, json_conf: float) -> float:
        """
        从 accuracy_tracker 查询真实准确率，替代 JSON 静态 conf。
        
        逻辑：
        1. 先查 tracker 有无该 (morph×stage) 的历史数据
        2. 有 → 返回贝叶斯平滑后的真实准确率
        3. 无 → fallback 到 JSON 静态值
        """
        morph_tag = morphology.value  # e.g. 'E1普通波动'
        real = self._tracker.get_real_precision(morph_tag, market_stage, min_samples=3)
        if real is not None:
            return real
        return json_conf  # fallback：没有足够样本时用 JSON 默认值

    def predict(
        self,
        features: MorphologyFeatures,
        morphology: Morphology,
        market_stage: str,
        sector_strength: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        综合形态 + 市场阶段，输出 T+1 预测结论。

        Args:
            features: 形态特征（from MorphologyClassifier）
            morphology: 分类结果（from MorphologyClassifier.classify）
            market_stage: '冰点期'/'退潮期'/'升温期'/'高潮期'/'降温期'/'修复期'
            sector_strength: 板块强度 0-1（可选）

        Returns:
            {
                'morphology': 'F1温和放量稳步推进',
                't1_direction': 'positive',
                't1_expected_change': '+2%~+5%',
                'confidence': 0.88,
                'rule_applied': 'F1规则：3/3胜率，温和放量稳步推进',
                'warnings': [],
                'sector_boost': 0.0,
                'final_confidence': 0.88,
            }
        """
        # 优先检查阶段特殊覆盖
        override = _get_stage_override(morphology, market_stage)
        if override:
            return self._build_from_override(features, morphology, market_stage, override, sector_strength)

        # 检查各形态特殊规则
        rule_result = self._apply_special_rules(features, morphology, market_stage, sector_strength)
        if rule_result:
            return rule_result

        # 通用预测（base config + 板块加成）
        return self._build_generic_prediction(features, morphology, market_stage, sector_strength)

    # ============================================================
    # 特殊规则（按形态 × 阶段组合）
    # ============================================================

    def _apply_special_rules(
        self,
        features: MorphologyFeatures,
        morphology: Morphology,
        market_stage: str,
        sector_strength: Optional[float],
    ) -> Optional[Dict[str, Any]]:
        """应用5条核心特殊规则，有特殊return时返回dict，否则返回None走通用路径

        注意：C1/D2的高潮期规则已迁移到JSON stage_overrides，
        此处只保留无条件规则（F1/A）和无条件negative规则。
        E1×高潮期和C1×高潮期的override路径guard在_build_from_override()中。
        """

        # 规则2：F1 最安全（无条件）
        if morphology == Morphology.F1:
            cfg = _get_morph_config(morphology)
            json_conf = cfg.get('t1_confidence', 0.88)
            real_conf = self._get_confidence(morphology, market_stage, json_conf)
            warnings = []

            # === 时间维度调节（consec_days + cycle_position）===
            conf_adjustment = 0.0
            if features.consec_days >= 4:
                conf_adjustment = -0.15
                warnings.append(f'⚠️ F1连续{features.consec_days}板：高位缩量横盘风险，连板越多越危险')
            elif features.consec_days <= 1:
                conf_adjustment = 0.05
                warnings.append('✅ F1首板/非连板：底部启动最安全，确认度最高')

            real_conf = max(0.50, min(0.95, real_conf + conf_adjustment))

            return {
                'morphology': morphology.value,
                't1_direction': 'positive',
                't1_expected_change': '+2%~+5%',
                'confidence': real_conf,
                'rule_applied': 'F1规则：3/3胜率，温和放量稳步推进' + (f'（consec_days={features.consec_days}调节{conf_adjustment:+g}）' if conf_adjustment != 0 else ''),
                'warnings': warnings,
                'sector_boost': 0.0,
                'final_confidence': real_conf,
            }

        # 规则3：D2 × 高潮/退潮/降温期 → 必跌（push_up_style 精细化）
        if morphology == Morphology.D2:
            if -2 <= features.close_pct <= 1:
                if market_stage in ('高潮期', '退潮期', '降温期'):
                    # push_up_style 精细化：早盘脉冲 vs 全天阴跌 区分
                    if features.push_up_style == '早盘脉冲':
                        warnings = ['⚠️ D2早盘脉冲+退潮期：冲高后全天阴跌，尾盘无支撑，T+1低开']
                        conf_adj = -0.05  # 比普通D2更悲观
                    elif features.push_up_style == '全天稳健':
                        warnings = ['⚠️ D2但分时偏稳健：可能是被动跟随非主动偷袭，但仍需谨慎']
                        conf_adj = 0.0
                    else:
                        warnings = ['尾盘偷袭在退潮期次日大概率低开杀']
                        conf_adj = 0.0
                    return {
                        'morphology': morphology.value,
                        't1_direction': 'negative',
                        't1_expected_change': '<-3%',
                        'confidence': max(0.65, 0.80 + conf_adj),
                        'rule_applied': f'D2规则({market_stage})：f30>80%→T+1必跌（push_up_style={features.push_up_style}）',
                        'warnings': warnings,
                        'sector_boost': 0.0,
                        'final_confidence': max(0.65, 0.80 + conf_adj),
                    }
                else:  # 升温期/修复期/冰点期
                    # 走通用路径，让 JSON stage_overrides 处理
                    return None

        # 规则4：A类一字板——仅在冰点/升温/修复期有效，降温/退潮/高潮期降权或排除
        if morphology == Morphology.A:
            cfg = _get_morph_config(morphology)
            warnings = []
            if features.q4_volume_pct > 20:
                warnings.append('⚠️尾盘放量，T+1可能开板')
            # 连板天数调节
            if features.consec_days >= 5:
                warnings.append(f'⚠️ A类连续{features.consec_days}板：一字板开板风险极大，建议回避')
                return {
                    'morphology': morphology.value,
                    't1_direction': 'neutral',
                    't1_expected_change': '-3%~+2%',
                    'confidence': 0.35,
                    'rule_applied': f'A类规则：连续{features.consec_days}板开板风险极大，直接降为neutral',
                    'warnings': warnings,
                    'sector_boost': 0.0,
                    'final_confidence': 0.35,
                }
            # 降温/退潮/高潮期 一字板降权
            if market_stage in ('降温期', '退潮期', '高潮期'):
                warnings.append(f'⚠️{market_stage}一字板高开低走风险大，建议回避')
                return {
                    'morphology': morphology.value,
                    't1_direction': 'neutral',   # 建议3：降为中性，不给positive
                    't1_expected_change': '-2%~+3%',  # 建议3：预期收窄且不确定
                    'confidence': 0.45,           # 建议3：置信度降至E1水平
                    'rule_applied': f'A类规则：{market_stage}一字板降权（高开低走风险）',
                    'warnings': warnings,
                    'sector_boost': 0.0,
                    'final_confidence': 0.45,
                }
            # 冰点/升温/修复期：维持原判断
            json_conf = cfg.get('t1_confidence', 0.85)
            real_conf = self._get_confidence(morphology, market_stage, json_conf)
            return {
                'morphology': morphology.value,
                't1_direction': 'positive',
                't1_expected_change': '+3%~+8%',
                'confidence': real_conf,
                'rule_applied': 'A类规则：一字板（无量锁仓），最强形态',
                'warnings': warnings,
                'sector_boost': 0.0,
                'final_confidence': real_conf,
            }

        return None

    def _build_from_override(
        self,
        features: MorphologyFeatures,
        morphology: Morphology,
        market_stage: str,
        override: Dict[str, Any],
        sector_strength: Optional[float],
    ) -> Dict[str, Any]:
        """从 JSON 配置的阶段覆盖构建预测（方向由JSON定，置信度由tracker定）

        注意：此函数在 JSON override 之前执行，用于放置"override 之前必须拦截"的硬规则。
        """

        # === Guard：JSON override 硬规则（优先级最高，必须先检查）===

        # C1×高潮期冲高过深 → 不论 JSON 怎么写，直接降 negative
        # JSON 注释里写了"105样本/胜率100%可能存在幸存者偏差"，这个 guard 就是
        # 对应幸存者偏差的修正：冲高超12%回落说明主力出货，不应给 positive
        if morphology == Morphology.C1 and market_stage == '高潮期':
            pullback = features.high_pct - features.close_pct
            if pullback >= 12:
                return {
                    'morphology': morphology.value,
                    't1_direction': 'negative',
                    't1_expected_change': '<-2%',
                    'confidence': 0.65,
                    'rule_applied': 'C1×高潮期冲高过深guard：high_pct-close_pct≥12%，回落过深直接给negative（幸存者偏差修正）',
                    'warnings': ['⚠️ C1×高潮期冲高超12%回落，深层回调风险大，已降权至negative'],
                    'sector_boost': 0.0,
                    'final_confidence': 0.65,
                }

        # E1×高潮期 amplitude≥3 → 不论 JSON 怎么写，降为 neutral
        # amplitude≥3 说明波动大（相对振幅），在高潮期是随波逐流型，次日情绪回落直接跟跌
        if morphology == Morphology.E1 and market_stage == '高潮期':
            if features.amplitude >= 3:
                return {
                    'morphology': morphology.value,
                    't1_direction': 'neutral',
                    't1_expected_change': '-2%~+2%',
                    'confidence': 0.45,
                    'rule_applied': 'E1×高潮期amplitude修正：amplitude≥3降为中性（随波逐流型）',
                    'warnings': ['⚠️ E1×高潮期amplitude≥3：随波逐流型，高潮期次日情绪回落直接跟跌'],
                    'sector_boost': 0.0,
                    'final_confidence': 0.45,
                }

        # === 正常 override 逻辑 ===
        morph_tag = morphology.value
        json_conf = override['t1_confidence']
        real_conf = self._get_confidence(morphology, market_stage, json_conf)

        # === Cold Start 警告：tracker 样本不足时提示 ===
        warnings = []
        tracker_count = self._tracker.get_sample_count(morph_tag, market_stage)
        if tracker_count is not None and tracker_count < 5:
            warnings.append(f'⚠️ {morphology.value}×{market_stage} 仅{tracker_count}个历史样本，置信度基于先验值')

        # === 时间维度调节（consec_days）===
        conf_adjustment = 0.0
        if morphology == Morphology.E1 and market_stage == '高潮期':
            if features.consec_days >= 3:
                conf_adjustment = -0.10
                warnings.append(f'⚠️ E1×高潮期连续{features.consec_days}天：连续非涨停跟涨后高位风险累积')
        if morphology == Morphology.E2 and market_stage == '高潮期':
            if features.consec_days >= 3:
                conf_adjustment = -0.08
                warnings.append(f'⚠️ E2×高潮期连续{features.consec_days}天：高倍股高位宽幅震荡风险')

        if conf_adjustment != 0:
            real_conf = max(0.30, min(0.95, real_conf + conf_adjustment))

        # === 差异化板块加成（按形态）===
        sector_boost = 0.0
        if sector_strength is not None and sector_strength > 0.7:
            # 弱形态更需要板块支撑，强形态自身就够了
            base_direction = override['t1_direction']
            boost = self._SECTOR_BOOST_MAP.get(morphology, 0.10)
            if base_direction == 'positive':
                sector_boost = boost
                real_conf = min(0.95, real_conf + boost)
            elif base_direction == 'neutral' and sector_strength > 0.85:
                # neutral 方向：极强板块可微弱加成并倾向 slight_positive
                sector_boost = boost * 0.4
                real_conf = min(0.95, real_conf + sector_boost)

        return {
            'morphology': morphology.value,
            't1_direction': override['t1_direction'],
            't1_expected_change': override['t1_expected_change'],
            'confidence': real_conf,
            'rule_applied': override['rule_applied'],
            'warnings': warnings,
            'sector_boost': round(sector_boost, 2),
            'final_confidence': real_conf,
        }

    # ============================================================
    # 通用预测路径
    # ============================================================

    def _build_generic_prediction(
        self,
        features: MorphologyFeatures,
        morphology: Morphology,
        market_stage: str,
        sector_strength: Optional[float],
    ) -> Dict[str, Any]:
        """通用预测（BCDEH形态 + 无特殊规则时）"""
        cfg = _get_morph_config(morphology)
        base_direction = cfg.get('t1_bias', 'neutral')
        json_conf = cfg.get('t1_confidence', 0.5)
        real_conf = self._get_confidence(morphology, market_stage, json_conf)

        warnings = []
        sector_boost = 0.0

        # === Cold Start 警告 ===
        morph_tag = morphology.value
        tracker_count = self._tracker.get_sample_count(morph_tag, market_stage)
        if tracker_count is not None and tracker_count < 5:
            warnings.append(f'⚠️ {morphology.value}×{market_stage} 仅{tracker_count}个历史样本，置信度基于先验值')

        # === 差异化板块加成 ===
        if sector_strength is not None and sector_strength > 0.7:
            boost = self._SECTOR_BOOST_MAP.get(morphology, 0.10)
            if base_direction == 'positive':
                sector_boost = boost
                real_conf = min(0.95, real_conf + boost)
            elif base_direction == 'neutral' and sector_strength > 0.85:
                sector_boost = boost * 0.4
                real_conf = min(0.95, real_conf + sector_boost)

        direction_map = {
            'positive': ('+1%~+3%', '上涨'),
            'negative': ('-1%~+1%', '震荡或下跌'),
            'neutral': ('-2%~+2%', '方向不明'),
        }
        expected, label = direction_map.get(base_direction, ('-2%~+2%', '未知'))

        return {
            'morphology': morphology.value,
            't1_direction': base_direction,
            't1_expected_change': expected,
            'confidence': round(real_conf, 2),
            'rule_applied': f'通用规则：{cfg.get("name", morphology.value)}',
            'warnings': warnings,
            'sector_boost': round(sector_boost, 2),
            'final_confidence': round(real_conf, 2),
        }

    # ============================================================
    # 批量预测
    # ============================================================

    def predict_batch(
        self,
        stocks: List[Dict[str, Any]],
        market_stage: str,
        is_st: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        批量预测（用于选股阶段）。

        stocks: [{
            'code': '000001', 'name': '平安',
            'open': 10.0, 'high': 11.0, 'low': 9.5, 'close': 10.8,
            'base_close': 9.8,
            'q1_vol_pct': 35.0,   # 可选
            'sector_strength': 0.8,  # 可选
        }]
        is_st: 是否为ST股（决定涨停比例，ST=5% 非ST=10%）
        """
        from .classifier import MorphologyClassifier
        clf = MorphologyClassifier()
        results = []

        for s in stocks:
            f = clf.extract_from_ohlc(
                open_px=s['open'],
                high_px=s['high'],
                low_px=s['low'],
                close_px=s['close'],
                base_price=s.get('base_close', s['open']),
                q1_vol_pct=s.get('q1_vol_pct'),
                q4_vol_pct=s.get('q4_vol_pct'),
                is_st=is_st,
            )
            morph = clf.classify(f)
            pred = self.predict(f, morph, market_stage, sector_strength=s.get('sector_strength'))
            results.append({**s, **pred})

        return results
