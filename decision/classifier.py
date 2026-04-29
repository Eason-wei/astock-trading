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

    def extract_features(self, minute_data: List[Dict]) -> MorphologyFeatures:
        """
        从241点分钟数据提取形态特征。

        minute_data: [
          {'price': 10.5, 'volume': 1000, 'time': '09:30', 'base_price': 10.0},
          {'price': 10.6, 'volume': 1200, 'time': '09:31'},
          ...
        ]
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
        board_quality = self._judge_board_quality(
            open_pct, close_pct, high_pct, low_pct,
            amplitude, is_limit_up=(close_pct >= 9.5)
        )

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
                              amplitude: float, is_limit_up: bool) -> str:
        """判断板的质量"""
        if not is_limit_up:
            return '非涨停'
        # 涨停了，判断是一字板还是实体板
        is_yibi = (close_pct < 1 and amplitude < 3)
        if is_yibi:
            return '一字板'
        # 烂板判断：最高点和收盘点差距大
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
    ) -> MorphologyFeatures:
        """
        从OHLC数据提取形态特征（无完整分钟数据时使用）。
        q1_vol_pct / q4_vol_pct 从外部传入（如从MySQL统计得出）
        """
        open_pct = (open_px - base_price) / base_price * 100
        close_pct = (close_px - base_price) / base_price * 100
        high_pct = (high_px - base_price) / base_price * 100
        low_pct = (low_px - base_price) / base_price * 100

        amplitude = (high_px - low_px) / base_price * 100

        # 成交量分段估算
        is_limit_up = close_pct >= 9.5
        f30 = q1_vol_pct if q1_vol_pct is not None else 0.0

        # 一字板判断
        is_yibi = (close_pct < 1 and f30 > 80) or (is_limit_up and amplitude < 3)
        board_quality = '一字板' if is_yibi else (
            '烂板' if (is_limit_up and amplitude > 8) else
            ('实体板' if is_limit_up else '非涨停')
        )

        # 成交量分段估算
        if q1_vol_pct is not None:
            remaining = 100.0 - q1_vol_pct
            q4_e = q4_vol_pct if q4_vol_pct is not None else remaining * 0.25
            q2_e = remaining * 0.40
            q3_e = remaining * 0.35
            q1_e = q1_vol_pct
        else:
            q1_e = q2_e = q3_e = q4_e = 25.0

        # 拉升方式（无分钟数据，用 amplitude+close_pct 估算）
        if amplitude < 3 and close_pct > 9.5:
            push_style = '早盘脉冲'
        elif close_pct > 5 and amplitude > 5:
            push_style = '全天稳健'
        else:
            push_style = '全天稳健'

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
        )

    # ============================================================
    # 形态分类（核心）
    # ============================================================

    def classify(self, f: MorphologyFeatures) -> Morphology:
        """
        基于形态特征分类到 Morphology 枚举。

        分类优先级：
          1. A类：一字板
          2. B类：正常涨停（close>=9.5% + amplitude<8%）
          3. C1：冲高回落（high-close>5% + amp>10%）
          4. D1：低开低走（open<-2% + close<open）
          5. D2：尾盘急拉（f30>80% + close在-2%~+1%）
          6. F1：温和放量稳步推进（Q1 40-60% + amp 3-8% + 上涨）
          7. E2：宽幅震荡（amp>8% + 未涨停）
          8. E1：普通波动（amp<5%）
          9. H：横向整理（amp<2 + Q1>70%）
        """
        # A类：一字板（无量锁仓）
        if f.board_quality == '一字板':
            return Morphology.A

        # B类：正常涨停（换手充分，稳健）
        if f.close_pct >= 9.5 and f.amplitude < 8:
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
        if 40 <= f.q1_volume_pct <= 60 and 3 <= f.amplitude <= 8:
            if f.close_pct > f.open_pct > 0:
                return Morphology.F1

        # E2：宽幅震荡（振幅>8%但不是涨停）
        if f.amplitude > 8 and f.close_pct < 9.5:
            return Morphology.E2

        # H：横向整理（价格几乎不动，成交量极低）
        # 注意：H 的 amplitude<2 在 E1 的 amplitude<5 之前检查
        if f.amplitude < 2 and f.q1_volume_pct > 70:
            return Morphology.H

        # E1：普通波动
        if f.amplitude < 5:
            return Morphology.E1

        # 默认：普通波动
        return Morphology.E1
