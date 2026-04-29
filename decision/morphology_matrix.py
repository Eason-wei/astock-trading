"""
morphology_matrix.py - 形态×阶段 T+1 预测矩阵（Facade）
=========================================================
职责：向后兼容的 Facade，委托给 classifier.py + predictor.py

重构结构：
  types.py          → Morphology枚举 + MorphologyFeatures dataclass
  config/morphology_config.json → 形态配置（代码化规则）
  classifier.py     → MorphologyClassifier（特征提取 + classify）
  predictor.py      → T1Predictor（5条核心T+1预测规则）
  morphology_matrix.py → MorphologyMatrix Facade（保持API兼容）

用法（保持不变）：
  mm = MorphologyMatrix()
  features = mm.extract_features(minute_data, code='688531.SH')  # 241点数据，code用于涨跌停价
  morph = mm.classify(features)
  result = mm.predict(features, morph, '高潮期', sector_strength=0.8)
"""

from typing import Dict, Any, List, Optional

from .types import Morphology, MorphologyFeatures
from .classifier import MorphologyClassifier
from .predictor import T1Predictor


class MorphologyMatrix:
    """
    形态×阶段 T+1 预测矩阵（Facade）

    委托给：
      - MorphologyClassifier：特征提取 + classify
      - T1Predictor：5条核心T+1预测规则
    """

    def __init__(self):
        self._clf = MorphologyClassifier()
        self._pred = T1Predictor()
        # 兼容旧代码：self.config 仍然可用
        import json, os
        config_path = os.path.join(os.path.dirname(__file__), 'config', 'morphology_config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                self.config = cfg.get('morphologies', {})
        else:
            # fallback 到 predictor 内联配置
            self.config = {}

    # ============================================================
    # 特征提取（委托给 MorphologyClassifier）
    # ============================================================

    def extract_features(self, minute_data: List[Dict], code: str = "", is_st: bool = False) -> MorphologyFeatures:
        """从241点分钟数据提取形态特征。is_st决定ST股涨停比例（5%而非10%）。"""
        return self._clf.extract_features(minute_data, code=code, is_st=is_st)

    def extract_from_ohlc(
        self,
        open_px: float,
        high_px: float,
        low_px: float,
        close_px: float,
        base_price: float,
        q1_vol_pct: Optional[float] = None,
        q4_vol_pct: Optional[float] = None,
    ) -> MorphologyFeatures:
        """从OHLC数据提取形态特征（无完整分钟数据时）"""
        return self._clf.extract_from_ohlc(
            open_px=open_px,
            high_px=high_px,
            low_px=low_px,
            close_px=close_px,
            base_price=base_price,
            q1_vol_pct=q1_vol_pct,
            q4_vol_pct=q4_vol_pct,
        )

    # ============================================================
    # 形态分类（委托给 MorphologyClassifier）
    # ============================================================

    def classify(self, f: MorphologyFeatures) -> Morphology:
        """基于特征分类到 Morphology 枚举"""
        return self._clf.classify(f)

    # ============================================================
    # T+1 预测（委托给 T1Predictor）
    # ============================================================

    def predict(
        self,
        features: MorphologyFeatures,
        morphology: Morphology,
        market_stage: str,
        sector_strength: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        综合形态 + 市场阶段，输出 T+1 预测结论。

        Returns: {
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
        return self._pred.predict(features, morphology, market_stage, sector_strength)

    # ============================================================
    # 兼容方法
    # ============================================================

    def string_to_morphology(self, form_str: str) -> Morphology:
        """将字符串形态名转换为 Morphology 枚举"""
        return Morphology.from_string(form_str)

    def predict_batch(
        self,
        stocks: List[Dict[str, Any]],
        market_stage: str,
    ) -> List[Dict[str, Any]]:
        """批量预测"""
        return self._pred.predict_batch(stocks, market_stage)

    # ============================================================
    # 以下为遗留方法，保持签名兼容
    # ============================================================

    def predict_with_stage(
        self,
        features: MorphologyFeatures,
        morphology: Morphology,
        market_stage: str,
        sector_strength: Optional[float] = None,
    ) -> Dict[str, Any]:
        """predict_with_stage（别名，与predict相同）"""
        return self.predict(features, morphology, market_stage, sector_strength)
