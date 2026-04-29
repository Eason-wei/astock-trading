"""three_questions.py - Three Questions Decision Filter"""

from dataclasses import dataclass
from typing import Dict, Any, List

@dataclass
class QuestionResult:
    question: str
    passed: bool
    score: float
    detail: str
    risk_level: str

@dataclass
class ThreeQuestionsResult:
    passed: bool
    overall_score: float
    questions: List[QuestionResult]
    warnings: List[str]
    final_verdict: str
    should_enter: bool

class ThreeQuestions:
    def __init__(self):
        self.weights = {"space_board": 0.40, "main_line": 0.35, "pain_effect": 0.25}

    def check(self, space_board: Dict[str, Any] = None, main_line: Dict[str, Any] = None,
              pain_score: float = None, board_health: float = None,
              top_rate: float = None, dadian_count: int = None,
              # 建议5新增：时间维度参数
              ladder_trend: str = None,    # 'accelerating'|'stable'|'decelerating' 梯队演化趋势
              main_line_trend: str = None, # 'emerging'|'peak'|'rotating'|'dying' 主线生命周期
              pain_trend: str = None,      # 'worsening'|'stable'|'improving' 亏钱效应趋势
              ) -> ThreeQuestionsResult:
        q1 = self._q1_space_board(space_board, board_health, ladder_trend)
        q2 = self._q2_main_line(main_line, main_line_trend)
        q3 = self._q3_pain_effect(pain_score, top_rate, dadian_count, pain_trend)
        questions = [q1, q2, q3]
        overall = sum(q.score * self.weights[q.question] for q in questions)
        warnings = [q.detail for q in questions if q.risk_level in ("high", "danger")]

        # 建议5：时间维度否决规则（趋势恶化时降低仓位上限）
        trend_penalty = 0
        trend_warn = []
        if ladder_trend == 'decelerating':
            trend_penalty += 15
            trend_warn.append('梯队高度下降中，连板生态走弱')
        if main_line_trend in ('rotating', 'dying'):
            trend_penalty += 10
            trend_warn.append(f'主线{main_line_trend}，操作价值降低')
        if pain_trend == 'worsening':
            trend_penalty += 10
            trend_warn.append('亏钱效应扩散中')

        # 综合分数扣除时间趋势惩罚
        overall = max(0, overall - trend_penalty)

        if overall >= 70 and q1.passed and q2.passed:
            verdict, should_enter = "heavy", True
        elif overall >= 50:
            verdict, should_enter = "light", True
        else:
            verdict, should_enter = "forbidden", False

        warnings += trend_warn
        return ThreeQuestionsResult(passed=should_enter, overall_score=round(overall,1),
            questions=questions, warnings=warnings, final_verdict=verdict, should_enter=should_enter)

    def _q1_space_board(self, sb, board_health, ladder_trend=None):
        """
        Q1：空间板判断。

        注意：ladder_trend='decelerating' 的惩罚统一在 check() 的 trend_penalty 中处理，
        不在 Q1 内部重复扣分，保持单点惩罚原则。
        """
        if sb is None and board_health is None:
            return QuestionResult("space_board", False, 30, "cannot judge", "danger")
        score = board_health if board_health is not None else 50
        detail = f"board_health={score}"

        # 建议5：时间维度调整
        if ladder_trend == 'accelerating':
            score = max(score, 80)   # 加速趋势 → 加分
            detail += " [梯队加速中]"

        if sb:
            status, up_days = sb.get("status", "unknown"), sb.get("up_days", 0)
            detail += f" status={status} days={up_days}"
            if status == "连板" and up_days >= 3: score = max(score, 85)
            elif status == "断板": score = min(score, 45)
            elif status == "跌停": score = 15
        return QuestionResult("space_board", score >= 50, score, detail,
                              "low" if score >= 70 else ("medium" if score >= 50 else "high"))

    def _q2_main_line(self, ml, main_line_trend=None):
        """
        Q2：主线判断 + 时间维度（建议5）。

        时间维度：
        - main_line_trend='emerging'：新周期启动，主线确认中 → 加分
        - main_line_trend='peak'：主线高潮，板块加速赶顶 → 降分
        - main_line_trend='rotating'：主线切换期，轮动快 → 降分
        - main_line_trend='dying'：主线消亡，市场无明确方向 → 降分
        """
        if ml is None:
            return QuestionResult("main_line", False, 30, "no main line", "high")
        status, strength = ml.get("status", "none"), ml.get("strength", 0.5)
        base_score = 85 if (status == "明确" and strength >= 0.7) else (
            70 if (status == "明确") else (45 if status == "模糊" else 20))
        detail = f"theme={ml.get('theme')} status={status}"

        # 建议5：时间维度调整
        if main_line_trend == 'emerging':
            base_score = max(base_score, 85)
            detail += " [新周期启动]"
        elif main_line_trend == 'peak':
            base_score = min(base_score, 60)   # 主线高潮 → 降低分
            detail += " [主线高潮，警惕]"
        elif main_line_trend in ('rotating', 'dying'):
            base_score = min(base_score, 45)
            detail += f" [{main_line_trend}]"

        passed = status == "明确" and base_score >= 50
        return QuestionResult("main_line", passed, base_score, detail,
                              "low" if base_score >= 70 else "medium")

    def _q3_pain_effect(self, pain_score, top_rate, dadian_count, pain_trend=None):
        """
        Q3：亏钱效应 + 时间维度（建议5）。

        时间维度：
        - pain_trend='worsening'：亏钱效应扩散 → 直接高风险
        - pain_trend='improving'：亏钱效应收敛 → 加分
        """
        score = 50
        detail = []
        if pain_score is not None:
            # pain_score: 80=健康（可以操作）→ Q3应给高分
            #             20=恐慌（不能操作）→ Q3应给低分
            # 直接用，不翻转
            score = pain_score
            detail.append(f"pain={pain_score}")
        if top_rate is not None:
            if top_rate >= 80: score = max(70, score)
            elif top_rate < 60: score = min(40, score)
            detail.append(f"top_rate={top_rate}")
        if dadian_count is not None:
            if dadian_count > 3: score = min(35, score)
            detail.append(f"dadian={dadian_count}")

        # 建议5：时间维度调整
        if pain_trend == 'worsening':
            score = min(score, 40)
            detail.append("[亏钱效应扩散中]")
        elif pain_trend == 'improving':
            score = max(score, 70)
            detail.append("[亏钱效应收敛中]")

        passed = score >= 50
        return QuestionResult("pain_effect", passed, score, " ".join(detail) if detail else "no data",
                              "low" if score >= 70 else "medium")
