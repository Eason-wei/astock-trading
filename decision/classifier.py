"""
classifier.py - 形态分类器
===========================
职责：
  1. extract_features()：从241点分钟数据提取 MorphologyFeatures
  2. extract_from_ohlc()：从OHLC数据提取 MorphologyFeatures
  3. classify()：基于 MorphologyFeatures 输出 Morphology 枚举

不涉及预测逻辑，只负责"这是什么形态"
"""

import math
from typing import Dict, Any, List, Optional

from .types import Morphology, MorphologyFeatures


# ============================================================
# 涨跌停价格计算（从 step5_stock_filter.py 迁移，不引入 step5 依赖）
# ============================================================

def _get_limit_ratio(code: str, is_st: bool) -> tuple:
    """根据股票代码判断涨跌停比例。返回 (limit_ratio, down_ratio, market)"""
    pure = code.strip().split('.')[0]
    if pure.startswith('688'):
        m = 'kcb'
        return (0.20, 0.20, m) if not is_st else (0.10, 0.10, m)
    elif pure.startswith('300') or pure.startswith('301'):
        m = 'cyb'
        return (0.20, 0.20, m) if not is_st else (0.10, 0.10, m)
    elif (pure.startswith('9') or pure.startswith('8')) and len(pure) == 6:
        m = 'bj'
        return (0.30, 0.30, m) if not is_st else (0.15, 0.15, m)
    else:
        m = 'main'
        return (0.10, 0.10, m) if not is_st else (0.05, 0.05, m)


def _round_limit_price(price: float, decimals: int, market: str, prev_close: float) -> float:
    """涨停价四舍五入：低价股（prev_close<2）用 tick 穷举找最优候选"""
    multiplier = 10 ** decimals
    rounded = math.floor(price * multiplier + 0.5) / multiplier
    if rounded < 0.01:
        return 0.01
    if prev_close < 2.0:
        # 低价股：上下浮动3个tick，找实际涨幅最接近目标的候选
        tick = 0.01 if prev_close >= 1.0 else (0.001 if prev_close >= 0.1 else 0.0001)
        base = round(price, decimals)
        target = 0.10 if market in ('main',) else (0.20 if market in ('cyb', 'kcb') else 0.30)
        if market == 'bj':
            target = 0.30 if prev_close >= 1.0 else 0.20
        elif market in ('cyb', 'kcb'):
            target = 0.20 if prev_close >= 1.0 else 0.10
        best, best_diff = base, float('inf')
        for i in range(-3, 4):
            c = base + i * tick
            if c <= 0:
                continue
            diff = abs((c - prev_close) / prev_close - target)
            if diff < best_diff:
                best, best_diff = c, diff
        return best
    return rounded


def _calculate_limit_prices(
    prev_close: float, code: str, is_st: bool = False
) -> tuple:
    """
    计算涨跌停价。
    返回 (limit_up, limit_down)，单位元，精确到分。
    """
    up_ratio, down_ratio, market = _get_limit_ratio(code, is_st)
    limit_up = _round_limit_price(prev_close * (1.0 + up_ratio), 2, market, prev_close)
    limit_down = _round_limit_price(prev_close * (1.0 - down_ratio), 2, market, prev_close)
    limit_down = max(limit_down, 0.01)
    return limit_up, limit_down


class MorphologyClassifier:
    """
    形态分类器（单一职责：分类）

    用法：
        clf = MorphologyClassifier()
        features = clf.extract_from_ohlc(open=10, high=11, ...)
        morph = clf.classify(features)
    """

    # ============================================================
    # 特征提取：从241点分钟数据
    # ============================================================

    def extract_features(self, minute_data: List[Dict], code: str = "", is_st: bool = False) -> MorphologyFeatures:
        """
        从241点分钟数据提取形态特征。

        minute_data: [
          {'price': 10.5, 'volume': 1000, 'time': '09:30', 'base_price': 10.0},
          {'price': 10.6, 'volume': 1200, 'time': '09:31'},
          ...
        ]

        Args:
            minute_data: 241点分钟数据列表
            code: 股票代码（如 '688531.SH'），用于计算涨跌停价。
                  若 minute_data 中无 code 字段，必须传入；否则科创板/创业板
                  会因默认主板比例导致涨停价计算错误。
            is_st: 是否为ST/*ST股。决定涨跌停比例（ST主板=5%，非ST=10%）。
        """
        prices = [d['price'] for d in minute_data if d.get('price') is not None]
        volumes = [d['volume'] for d in minute_data if d.get('volume') is not None]

        if not prices or not volumes:
            raise ValueError("minute_data需要包含price和volume字段")

        # 基准价（前一交易日收盘）
        base_price = minute_data[0].get('base_price')
        if not base_price:
            base_price = prices[0] / (1 + (minute_data[0].get('change_pct', 0) / 100))
        if not base_price:
            base_price = prices[0]

        # 价格
        open_px = prices[0]
        close_px = prices[-1]
        high_px = max(prices)
        low_px = min(prices)

        open_pct = (open_px - base_price) / base_price * 100
        close_pct = (close_px - base_price) / base_price * 100
        high_pct = (high_px - base_price) / base_price * 100
        low_pct = (low_px - base_price) / base_price * 100

        # 振幅
        amplitude = (high_px - low_px) / base_price * 100

        # 成交量分段
        total_vol = sum(volumes)
        q1_count = 30  # 9:30-10:00
        q2_count = 60  # 10:00-11:00
        q3_count = 90  # 11:00-13:00
        # q4_count = 60  # 13:00-14:57

        q1_vol = sum(volumes[:q1_count]) if len(volumes) >= q1_count else sum(volumes)
        q2_vol = sum(volumes[q1_count:q1_count+q2_count]) if len(volumes) >= q1_count+q2_count else 0
        q3_vol = sum(volumes[q1_count+q2_count:q1_count+q2_count+q3_count]) if len(volumes) >= q1_count+q2_count+q3_count else 0
        q4_vol = sum(volumes[q1_count+q2_count+q3_count:]) if len(volumes) > q1_count+q2_count+q3_count else 0

        q1_vol_pct = q1_vol / total_vol * 100 if total_vol > 0 else 0
        q2_vol_pct = q2_vol / total_vol * 100 if total_vol > 0 else 0
        q3_vol_pct = q3_vol / total_vol * 100 if total_vol > 0 else 0
        q4_vol_pct = q4_vol / total_vol * 100 if total_vol > 0 else 0

        # f30 = 前30分钟成交量 / 全天成交量
        f30 = q1_vol_pct

        # 拉升方式判断
        push_up_style = self._judge_push_style(prices, volumes, q1_count)

        # 板的质量判断
        # ST股涨停阈值为5%，非ST为10%
        limit_threshold = 4.5 if is_st else 9.5
        is_limit_up = close_pct >= limit_threshold
        board_quality = self._judge_board_quality(
            open_pct, close_pct, high_pct, low_pct,
            amplitude, is_limit_up=is_limit_up,
            limit_threshold=limit_threshold
        )

        # ── 一字板精确判断：逐分钟价格一致性 ──────────────────────
        # 涨跌停价计算需要正确的市场（科创板20%/创业板20% ≠ 主板10%）
        # code 优先从参数传入，其次从 minute_data 字段
        # is_st 决定涨跌停比例（ST主板=5%，非ST=10%）
        _code = code or minute_data[0].get('code', '')
        limit_up, limit_down = _calculate_limit_prices(base_price, _code, is_st=is_st)

        total_minutes = len(prices)
        at_limit_count = sum(1 for p in prices if abs(p - limit_up) < 0.005)
        at_lower_count = sum(1 for p in prices if abs(p - limit_down) < 0.005)
        consistency_at_limit = at_limit_count / total_minutes if total_minutes > 0 else 0.0
        consistency_at_lower = at_lower_count / total_minutes if total_minutes > 0 else 0.0

        return MorphologyFeatures(
            open_pct=round(open_pct, 2),
            close_pct=round(close_pct, 2),
            high_pct=round(high_pct, 2),
            low_pct=round(low_pct, 2),
            q1_volume_pct=round(q1_vol_pct, 2),
            q2_volume_pct=round(q2_vol_pct, 2),
            q3_volume_pct=round(q3_vol_pct, 2),
            q4_volume_pct=round(q4_vol_pct, 2),
            f30=round(f30, 2),
            amplitude=round(amplitude, 2),
            push_up_style=push_up_style,
            board_quality=board_quality,
            consistency_at_limit=round(consistency_at_limit, 4),
            consistency_at_lower=round(consistency_at_lower, 4),
        )

    def _judge_push_style(self, prices: List[float], volumes: List[float], q1_count: int) -> str:
        """判断拉升方式"""
        if len(prices) < q1_count:
            return '全天稳健'

        early_prices = prices[:q1_count]
        mid_prices = prices[q1_count:q1_count+60] if len(prices) > q1_count+60 else prices[q1_count:]
        late_prices = prices[-60:] if len(prices) >= 60 else prices

        early_gain = early_prices[-1] - early_prices[0]
        mid_gain = mid_prices[-1] - mid_prices[0] if mid_prices else 0
        late_gain = late_prices[-1] - late_prices[0] if late_prices else 0

        if late_gain > early_gain * 1.5 and late_gain > mid_gain:
            return '尾盘偷袭'
        elif early_gain > mid_gain * 1.5 and early_gain > late_gain:
            return '早盘脉冲'
        elif mid_gain > early_gain * 1.2 and mid_gain > late_gain:
            return '午盘拉升'
        return '全天稳健'

    def _judge_board_quality(self, open_pct: float, close_pct: float,
                              high_pct: float, low_pct: float,
                              amplitude: float, is_limit_up: bool,
                              limit_threshold: float = 9.5) -> str:
        """
        判断板的质量。

        ST股涨停阈值为5%，非ST为10%（通过limit_threshold参数传入）。
        涨跌停一字板：amplitude < 3% + 涨跌停方向
        烂板：涨停后炸开过，收盘与最高点差 > 3%
        实体板：正常封板，非烂板
        非涨停：未涨停
        """
        # 一字板细分：amplitude < 3% 时判断方向
        if amplitude < 3:
            if close_pct >= limit_threshold:
                return '涨停一字板'
            if close_pct <= -limit_threshold:
                return '跌停一字板'
            return '普通一字板'

        # amplitude >= 3%：正常判断
        if not is_limit_up:
            return '非涨停'
        if high_pct - close_pct > 3:
            return '烂板'
        return '实体板'

    # ============================================================
    # 特征提取：从OHLC数据（无分钟明细时）
    # ============================================================

    def extract_from_ohlc(
        self,
        open_px: float,
        high_px: float,
        low_px: float,
        close_px: float,
        base_price: float,
        q1_vol_pct: Optional[float] = None,
        q4_vol_pct: Optional[float] = None,
        is_st: bool = False,
        code: Optional[str] = None,
    ) -> MorphologyFeatures:
        """
        从OHLC数据提取形态特征（无完整分钟数据时的降级路径）。

        base_price 必须 > 0，否则无法计算涨跌幅，返回全零特征。
        调用方应确保 base_price 有效（可从 minute_data 的 base_price 字段获取）。
        由于没有逐分钟明细，无法判断"241个时间点是否都等于涨停价"。
        若需要精确判断，应调用 extract_features(241点分钟数据)。

        is_st 用于区分涨跌停判断阈值（ST股更窄）。
        code 用于区分市场类型（主板/科创板/创业板/北交所）。
        Bug51修复：新增code参数后，可正确区分科创板/创业板20%涨跌停与主板10%涨跌停。

        涨跌停阈值：
          主板(非ST): 10%  (close_pct >= 9.5% 视为涨停)
          主板(ST):    5%  (close_pct >= 4.5% 视为涨停)
          科创/创业(非ST): 20% (close_pct >= 19.5% 视为涨停)
          科创/创业(ST):   10% (close_pct >= 9.5% 视为涨停)
          北交所(非ST): 30%  (close_pct >= 29.5% 视为涨停)
          北交所(ST):   15%  (close_pct >= 14.5% 视为涨停)

        board_quality 判断（无逐分钟明细，用 amplitude 近似）：
          涨停一字板：amplitude < 3% 且 涨停
          跌停一字板：amplitude < 3% 且 跌停
          普通一字板：amplitude < 3% 且非涨跌停
          烂板：amplitude >= 3% 且涨停后炸开 high_pct - close_pct > 3
          实体板：amplitude >= 3% 且正常涨停
          非涨停：未涨停
        """
        # base_price 保护：必须 > 0 才能计算涨跌幅
        if base_price is None or base_price <= 0:
            return MorphologyFeatures(
                open_pct=0.0, close_pct=0.0, high_pct=0.0, low_pct=0.0,
                q1_volume_pct=0.0, q2_volume_pct=0.0, q3_volume_pct=0.0, q4_volume_pct=0.0,
                f30=0.0, amplitude=0.0, push_up_style='全天稳健',
                board_quality='非涨停', consistency_at_limit=0.0, consistency_at_lower=0.0,
            )

        open_pct = (open_px - base_price) / base_price * 100
        close_pct = (close_px - base_price) / base_price * 100
        high_pct = (high_px - base_price) / base_price * 100
        low_pct = (low_px - base_price) / base_price * 100

        amplitude = (high_px - low_px) / base_price * 100

        # 涨跌停阈值：根据市场类型(code)和is_st共同决定
        # Bug51修复：从单一is_st判断改为市场+ST联合判断
        pure = (code or '').strip().split('.')[0] if code else ''
        if pure.startswith('688') or pure.startswith('300') or pure.startswith('301'):
            # 科创板/创业板：非ST=20%，ST=10%
            limit_threshold = 9.5 if is_st else 19.5
        elif (pure.startswith('9') or pure.startswith('8')) and len(pure) == 6:
            # 北交所：非ST=30%，ST=15%
            limit_threshold = 14.5 if is_st else 29.5
        else:
            # 主板：非ST=10%，ST=5%
            limit_threshold = 4.5 if is_st else 9.5
        is_limit_up = close_pct >= limit_threshold

        f30 = q1_vol_pct if q1_vol_pct is not None else 0.0

        # ── board_quality 判断（无逐分钟明细，用 amplitude 近似）────────
        if is_limit_up and amplitude < 3:
            board_quality = '涨停一字板'   # amplitude<3% = 最高-最低极小，锁仓
        elif close_pct <= -limit_threshold and amplitude < 3:
            board_quality = '跌停一字板'
        elif amplitude < 3:
            board_quality = '普通一字板'
        elif is_limit_up and high_pct - close_pct > 3:
            board_quality = '烂板'
        elif is_limit_up:
            board_quality = '实体板'
        else:
            board_quality = '非涨停'

        # 成交量分段估算
        if q1_vol_pct is not None:
            remaining = 100.0 - q1_vol_pct
            q4_e = q4_vol_pct if q4_vol_pct is not None else remaining * 0.25
            q2_e = remaining * 0.40
            q3_e = remaining * 0.35
            q1_e = q1_vol_pct
        else:
            q1_e = q2_e = q3_e = q4_e = 25.0

        # 拉升方式（无分钟数据，用 OHLC 估算）
        # 判断逻辑：全天高位震荡+振幅小 → 一字板(早盘已封)
        #         全天大部分时间在低位+尾盘急拉 → 尾盘偷袭
        #         其他 → 全天稳健
        mid_pct = (high_pct + low_pct) / 2
        is_near_limit = amplitude < 3 and close_pct >= limit_threshold
        is_tail_push = close_pct <= 1 and low_pct < mid_pct - 3
        if is_near_limit:
            push_style = '早盘脉冲'    # 一字板早盘封死
        elif is_tail_push:
            push_style = '尾盘偷袭'
        else:
            push_style = '全天稳健'

        # consistency_at_limit/lower 保持默认值 0.0
        # classify() 中 consistency==0.0 + board_quality=='涨停一字板' → A类 fallback

        return MorphologyFeatures(
            open_pct=round(open_pct, 2),
            close_pct=round(close_pct, 2),
            high_pct=round(high_pct, 2),
            low_pct=round(low_pct, 2),
            q1_volume_pct=round(q1_e, 2),
            q2_volume_pct=round(q2_e, 2),
            q3_volume_pct=round(q3_e, 2),
            q4_volume_pct=round(q4_e, 2),
            f30=round(f30, 2),
            amplitude=round(amplitude, 2),
            push_up_style=push_style,
            board_quality=board_quality,
            # consistency_at_limit/lower = 0.0（默认值，表明无逐分钟明细）
        )

    # ============================================================
    # 形态分类（核心）
    # ============================================================

    def classify(self, f: MorphologyFeatures) -> Morphology:
        """
        基于形态特征分类到 Morphology 枚举。

        分类优先级：
          1. A类：涨停一字板（逐分钟一致性>=95%，优先）或 board_quality=='涨停一字板'（无分钟数据fallback）
          2. D1：跌停一字板（逐分钟一致性>=95%）或 board_quality=='跌停一字板'
          3. B类：正常涨停（close>=9.5% + amplitude<8%）
          4. C1：冲高回落（high-close>5% + amp>10%）
          5. D1：低开低走（open<-2% + close<open）
          6. D2：尾盘急拉（f30>80% + close在-2%~+1%）
          7. F1：温和放量稳步推进（Q1 40-60% + amp 3-8% + 上涨）
          8. E2：宽幅震荡（amp>8% + 未涨停）
          9. H：横向整理（amp<2 + Q1>70%）
          10. E1：普通波动（amp<5%，兜底）
        """
        # ── ① A类涨停一字板 ────────────────────────────────────
        # 有分钟数据：逐分钟一致性（>=99%才是一字板）
        if f.consistency_at_limit >= 0.99:
            return Morphology.A
        # 无分钟数据（extract_from_ohlc路径）：
        # consistency_at_limit 保持默认值 0.0（表明未计算逐分钟数据）
        # board_quality='涨停一字板' 是 OHLC 近似的判断，用作 fallback
        if f.consistency_at_limit == 0.0 and f.board_quality == '涨停一字板':
            return Morphology.A

        # ── ② D1跌停一字板 ────────────────────────────────────
        if f.consistency_at_lower >= 0.99:
            return Morphology.D1
        if f.consistency_at_lower == 0.0 and f.board_quality == '跌停一字板':
            return Morphology.D1

        # B类：正常涨停（换手充分，稳健）
        # 阈值20%：真实涨停股振幅可达8-20%（开板后再封是常态），amplitude<20%可覆盖
        # Bug39修复：12%过窄，14%涨停振幅会落入E1兜底（错误分类）
        if f.close_pct >= 9.5 and f.amplitude < 20:
            return Morphology.B

        # C1：冲高回落（收盘涨幅远小于最高点涨幅，振幅大）
        if f.high_pct - f.close_pct > 5 and f.amplitude > 10:
            return Morphology.C1

        # D1：低开低走
        if f.open_pct < -2 and f.close_pct < f.open_pct:
            return Morphology.D1

        # D2：尾盘急拉（f30>80% + q4在-2%~+1%）
        if f.f30 > 80 and -2 <= f.close_pct <= 1:
            return Morphology.D2

        # F1：温和放量稳步推进（Q1占比40-60%，振幅3-8%，量价配合）
        # 修复：open_pct可以=0（平开也是稳步推进），改为 >= 0
        if 40 <= f.q1_volume_pct <= 60 and 3 <= f.amplitude <= 8:
            if f.close_pct >= f.open_pct >= 0:
                return Morphology.F1

        # E2：宽幅震荡（振幅>8%但不是涨停）
        if f.amplitude > 8 and f.close_pct < 9.5:
            return Morphology.E2

        # H：横向整理（价格几乎不动，成交量极低）
        # Bug33修复：amplitude<2%在extract_from_ohlc路径几乎无法触发（high-low通常为整数）
        # 改为<3%更实用，同时保留H类"极度低波动"的语义
        if f.amplitude < 3 and f.q1_volume_pct > 70:
            return Morphology.H

        # E1：普通波动
        if f.amplitude < 5:
            return Morphology.E1

        # 默认：普通波动
        return Morphology.E1
