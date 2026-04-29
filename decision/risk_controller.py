"""risk_controller.py - Risk/Reward and Stop Loss System"""

from dataclasses import dataclass

HARD_STOP = 0.05
SOFT_STOP = 0.03
PROFIT_TARGET = 0.25
MIN_RR = 3.0
MIN_EXPECTANCY = 0.01
MIN_TURNOVER = 8000

@dataclass
class RiskResult:
    rr_ratio: float
    expectancy: float
    label: str
    can_enter: bool

@dataclass  
class StopLossResult:
    should_stop: bool
    stop_type: str
    reason: str
    action: str

class RiskController:
    def calculate_rr(self, entry: float, target: float, stop: float,
                     win_rate: float = None, avg_win: float = None, avg_loss: float = None) -> RiskResult:
        if entry <= 0 or stop >= entry or target <= entry:
            return RiskResult(0.0, -1.0, "param error", False)
        rr = (target - entry) / (entry - stop)
        if win_rate is not None and avg_win is not None and avg_loss is not None:
            expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
        else:
            expectancy = (rr - 1) / (rr + 1) * 0.5
        label = "excellent" if rr >= 5 else ("good" if rr >= 3 else ("fair" if rr >= 2 else "poor"))
        can_enter = rr >= MIN_RR and expectancy > MIN_EXPECTANCY
        return RiskResult(round(rr,2), round(expectancy,4), label, can_enter)

    def should_stop_loss(self, current: float, entry: float, limit_down: bool = False) -> StopLossResult:
        if entry <= 0:
            return StopLossResult(False, "none", "invalid entry", "hold")
        loss_pct = (entry - current) / entry
        if loss_pct >= HARD_STOP or limit_down:
            return StopLossResult(True, "hard", f"loss={loss_pct:.1%}>={HARD_STOP:.1%}", "exit")
        if loss_pct >= SOFT_STOP:
            return StopLossResult(True, "soft", f"loss={loss_pct:.1%}>={SOFT_STOP:.1%}", "reduce")
        return StopLossResult(False, "none", f"normal loss={loss_pct:.1%}", "hold")

    def should_take_profit(self, current: float, entry: float, high: float = None,
                           trailing: float = None) -> StopLossResult:
        if entry <= 0:
            return StopLossResult(False, "none", "invalid entry", "hold")
        profit_pct = (current - entry) / entry
        if profit_pct >= PROFIT_TARGET:
            return StopLossResult(True, "profit", f"profit={profit_pct:.1%}>={PROFIT_TARGET:.1%}", "exit")
        if trailing is not None and high is not None:
            drawdown = (high - current) / high
            if drawdown >= trailing:
                return StopLossResult(True, "trailing", f"drawdown={drawdown:.1%}>={trailing:.1%}", "exit")
        return StopLossResult(False, "none", f"holding profit={profit_pct:.1%}", "hold")

    def check_system_risk(self, turnover: float, fall_count: int = None,
                          emotion_score: float = None) -> dict:
        """
        系统风险检查。
        注意：rise_pct 参数已删除（P2-3 死参数，从未被调用方传入）。
        """
        risks, position_limit = [], 1.0
        if turnover < MIN_TURNOVER:
            risks.append(f"turnover={turnover}<{MIN_TURNOVER}")
            position_limit = min(position_limit, 0.10)
        if fall_count is not None and fall_count > 30:
            risks.append(f"fall_count={fall_count}>30")
            position_limit = min(position_limit, 0.10)
        # P2-②修复：emotion_score 范围 0-100，>100 永不触发；改为 >90（高潮区警戒）
        if emotion_score is not None and emotion_score > 90:
            risks.append(f"emotion={emotion_score}>90(高潮区)")
            position_limit = min(position_limit, 0.20)
        return {"has_risk": len(risks) > 0, "risks": risks,
                "position_limit": position_limit, "can_enter": position_limit >= 0.10}
