"""prediction_verifier.py - T+1 预判验证器"""

from dataclasses import dataclass
from typing import Dict, Any, Optional

@dataclass
class VerifyResult:
    correct: bool
    direction_match: bool
    score: float        # 0-100
    deviation: float    # 实际与预判的偏差
    lesson_key: str     # 教训标识
    detail: str
    needs_update: bool  # 是否需要更新认知
    profit_ratio: float = 0.0  # 建议1新增：实际收益/基准期望（盈利比）

class PredictionVerifier:
    """T+1 预判验证器"""

    def __init__(self):
        # 偏差容错阈值（%）
        self.DIRECTION_TOLERANCE = 1.0

    def verify(self, prediction: Dict[str, Any], actual: Dict[str, Any]) -> VerifyResult:
        """
        盈利导向验证（建议1重构）。

        核心变化：
        - correct：不再只看方向，而是看"这笔交易是否值得做"
          → 条件：direction_match AND actual_close > 0（正收益）AND score >= 70
        - 新增 profit_ratio：实际收益 / 预测期望（衡量期望值是否兑现）
          → positive时：actual_close / 3.0（基准期望+3%）
          → negative时：abs(actual_close) / 3.0
          → neutral时：0
        - lesson_key 增加 'profitable' / 'loss' 区分
        """
        pred_dir = prediction.get("direction", "neutral")
        pred_confidence = prediction.get("confidence", 0.5)  # 建议1新增：置信度用于计算profit_ratio
        actual_close = actual.get("close_pct", 0)
        limit_down = actual.get("limit_down", False)

        # 方向匹配
        if pred_dir == "positive":
            direction_match = actual_close > self.DIRECTION_TOLERANCE
        elif pred_dir == "negative":
            direction_match = actual_close < -self.DIRECTION_TOLERANCE
        else:
            direction_match = abs(actual_close) < 3.0

        # 计算偏差（用于评风控分）
        deviation = actual_close
        if pred_dir == "positive":
            deviation = actual_close - 3.0
        elif pred_dir == "negative":
            deviation = actual_close + 3.0

        # 评分（建议1：盈利才算高分）
        if direction_match and actual_close > 0 and abs(deviation) < 1.0:
            score = 95
        elif direction_match and actual_close > 0 and abs(deviation) < 2.0:
            score = 85
        elif direction_match and actual_close > 0:
            score = 75
        elif abs(actual_close) < 2.0 and pred_dir == "neutral":
            score = 70
        elif direction_match and actual_close <= 0:
            # 方向对但亏钱：方向陷阱
            score = 55
        else:
            score = max(0, 50 - abs(deviation) * 5)

        # 建议1核心：correct = 方向对 AND 正收益 AND 评分>=70
        correct = direction_match and actual_close > 0 and score >= 70

        # 建议1新增：profit_ratio（实际收益/基准期望）
        if pred_dir == "positive" and actual_close > 0:
            profit_ratio = round(actual_close / 3.0, 2)  # 基准期望+3%
        elif pred_dir == "negative" and actual_close < 0:
            profit_ratio = round(abs(actual_close) / 3.0, 2)  # 基准期望-3%
        else:
            profit_ratio = 0.0

        # 偏差描述
        if deviation > 3:
            lesson_key = "overshoot"
            detail = f"实际涨幅{actual_close:.1f}%远超预判"
        elif deviation < -3:
            lesson_key = "undershoot"
            detail = f"实际涨幅{actual_close:.1f}%远低于预判"
        elif direction_match and actual_close > 0:
            lesson_key = "profitable"   # 建议1：盈利分类
            detail = f"预判准确，盈利{actual_close:.1f}%，profit_ratio={profit_ratio}"
        elif direction_match and actual_close <= 0:
            lesson_key = "loss"         # 建议1：方向陷阱
            detail = f"方向正确但实际亏损{actual_close:.1f}%"
        else:
            lesson_key = "wrong_direction"
            detail = f"方向错误：预判{pred_dir}，实际{actual_close:.1f}%"

        if limit_down:
            lesson_key = "limit_down"
            detail += "（跌停）"

        needs_update = not correct or abs(deviation) > 3

        # 建议1新增：profit_ratio 写入 dataclass
        return VerifyResult(
            correct=correct,
            direction_match=direction_match,
            score=score,
            deviation=round(deviation, 2),
            lesson_key=lesson_key,
            detail=detail,
            needs_update=needs_update,
            profit_ratio=profit_ratio,   # 建议1新增字段
        )

    def verify_batch(self, predictions: list, actuals: list) -> list:
        results = []
        for pred, actual in zip(predictions, actuals):
            results.append(self.verify(pred, actual))
        return results

    def get_statistics(self, results: list) -> Dict[str, Any]:
        if not results:
            return {"total": 0}
        correct = sum(1 for r in results if r.correct)
        avg_score = sum(r.score for r in results) / len(results)
        direction_acc = sum(1 for r in results if r.direction_match) / len(results)
        return {
            "total": len(results),
            "correct": correct,
            "accuracy": round(correct / len(results), 3),
            "avg_score": round(avg_score, 1),
            "direction_accuracy": round(direction_acc, 3),
        }
