"""position_rules.py - Stage-based position sizing rules"""

from dataclasses import dataclass
from typing import Optional

STAGE_POSITION_CONFIG = {
    # 注意：所有key必须与 step3.get('qingxu') 返回值完全一致
    # qingxu 可能返回: '冰点期' / '退潮期' / '升温期' / '高潮期' / '降温期' / '修复期'
    "冰点期": {"base": 0.20, "min": 0.10, "max": 0.30, "note": "强龙轻仓"},
    "退潮期": {"base": 0.15, "min": 0.10, "max": 0.30, "note": "高位不碰"},
    "升温期": {"base": 0.40, "min": 0.30, "max": 0.50, "note": "主线确认积极"},
    "高潮期": {"base": 0.40, "min": 0.30, "max": 0.50, "note": "持仓不追加"},
    "降温期": {"base": 0.20, "min": 0.10, "max": 0.30, "note": "快进快出"},
    "修复期": {"base": 0.20, "min": 0.10, "max": 0.30, "note": "轻仓观察"},
}

@dataclass
class PositionConfig:
    stage: str
    base: float
    min: float
    max: float
    note: str
    lianban_multiplier: float = 1.0
    sector_multiplier: float = 1.0
    final_position: float = 0.0

    def should_enter_position(self) -> bool:
        """仓位>最小阈值才算有效入场仓位"""
        return self.final_position > 0.05

    def can_enter(self, t1_direction: str = None, final_confidence: float = None) -> bool:
        """综合判断是否可以入场：仓位有效 + 预测方向正面 + 置信度足够"""
        if not self.should_enter_position():
            return False
        if t1_direction is not None and t1_direction != 'positive':
            return False
        if final_confidence is not None and final_confidence < 0.6:
            return False
        return True


class PositionRules:
    def get_stage_config(self, stage: str) -> PositionConfig:
        # P2-A修复：阶段名称无校验回退时加warning，便于排查静默bug
        if stage not in STAGE_POSITION_CONFIG:
            import logging
            logging.warning(f"[PositionRules] 未知阶段 '{stage}'，回退到'修复期'。请检查 step1/step3 返回的 qingxu 字段是否在 STAGE_POSITION_CONFIG 中。")
            stage = "修复期"
        cfg = STAGE_POSITION_CONFIG.get(stage, STAGE_POSITION_CONFIG["修复期"])
        return PositionConfig(stage=stage, **cfg)

    def calculate(self, stage: str, lianban_days: int = 0, sector_strength: float = 0.5,
                  is_main_line: bool = False, emotion_score: float = None) -> PositionConfig:
        cfg = STAGE_POSITION_CONFIG.get(stage, STAGE_POSITION_CONFIG["修复期"])
        pc = PositionConfig(stage=stage, **cfg)

        if lianban_days >= 5: pc.lianban_multiplier = 1.5
        elif lianban_days >= 3: pc.lianban_multiplier = 1.3
        elif lianban_days == 2: pc.lianban_multiplier = 1.15

        if sector_strength >= 0.8: pc.sector_multiplier = 1.2
        elif sector_strength >= 0.6: pc.sector_multiplier = 1.1
        if is_main_line: pc.sector_multiplier *= 1.1

        if emotion_score is not None:
            if emotion_score < 20: pc.sector_multiplier *= 0.9
            elif emotion_score > 90: pc.sector_multiplier *= 0.95

        final = pc.base * pc.lianban_multiplier * pc.sector_multiplier
        final = min(pc.max, max(pc.min, final))
        pc.final_position = round(final, 2)
        return pc

    def should_enter(self, stage: str, emotion_score: float = None) -> bool:
        # 退潮期/降温期：无论情绪分数多少，都不应开仓（除非极端冰点<20）
        if emotion_score is not None and emotion_score < 20:
            return True  # 极端冰点允许轻仓试探
        return stage not in ["降温期", "退潮期"]

    def get_position_label(self, stage: str) -> str:
        """获取阶段仓位标签（P1-①修复：补充缺失方法）"""
        labels = {
            "冰点期": "轻仓试探",
            "退潮期": "空仓观望",
            "修复期": "轻仓观察",
            "升温期": "积极布局",
            "高潮期": "持仓不追",
            "降温期": "快进快出",
        }
        return labels.get(stage, "未知")
