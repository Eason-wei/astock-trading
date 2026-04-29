"""
types.py - 形态系统核心类型定义
=================================
职责：Morphology枚举 + MorphologyFeatures dataclass
不含任何业务逻辑，纯类型定义
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Morphology(Enum):
    """
    形态分类枚举（9种）
    
    A类: 一字板（无量锁仓，最强）
    B类: 正常涨停（换手充分，稳健）
    C1: 冲高回落型（高风险）
    D1: 低开低走（弱）
    D2: 尾盘急拉型（f30>80% + q4在-2%~+1%）
    E1: 普通波动（方向不明）
    E2: 宽幅震荡（方向不明）
    F1: 温和放量稳步推进（最安全，3/3胜率）
    H: 横向整理（无效信号）
    """
    A = "A类一字板"
    B = "B类正常涨停"
    C1 = "C1冲高回落"
    D1 = "D1低开低走"
    D2 = "D2尾盘急拉"
    E1 = "E1普通波动"
    E2 = "E2宽幅震荡"
    F1 = "F1温和放量"
    H = "H横向整理"

    @classmethod
    def from_string(cls, s: str) -> 'Morphology':
        """将字符串形态名转换为Morphology枚举"""
        s = s.upper().strip()
        mapping = {
            'A': cls.A, 'A类': cls.A, '一字板': cls.A,
            'B': cls.B, 'B类': cls.B,
            'C1': cls.C1, 'C1冲高回落': cls.C1, '冲高回落': cls.C1,
            'D1': cls.D1, 'D1低开低走': cls.D1, '低开低走': cls.D1,
            'D2': cls.D2, 'D2尾盘急拉': cls.D2, '尾盘急拉': cls.D2,
            'E1': cls.E1, 'E1普通波动': cls.E1, '普通波动': cls.E1,
            'E2': cls.E2, 'E2宽幅震荡': cls.E2, '宽幅震荡': cls.E2,
            'F1': cls.F1, 'F1温和放量': cls.F1, '温和放量': cls.F1,
            'H': cls.H, 'H横向整理': cls.H, '横向整理': cls.H,
        }
        return mapping.get(s, cls.E1)

    @classmethod
    def all_values(cls) -> list:
        return list(cls)


@dataclass
class MorphologyFeatures:
    """
    从241点分钟数据提取的形态特征（dataclass）
    
    用途：作为 classify() 和 predict() 的输入
    """
    open_pct: float       # 开盘涨跌幅(%)
    close_pct: float      # 收盘涨跌幅(%)
    high_pct: float       # 最高涨跌幅(%)
    low_pct: float        # 最低涨跌幅(%)

    q1_volume_pct: float  # Q1(9:30-10:00)成交量占比(%)
    q2_volume_pct: float  # Q2成交量占比(%)
    q3_volume_pct: float  # Q3成交量占比(%)
    q4_volume_pct: float  # Q4成交量占比(%)

    f30: float            # 前30分钟成交量占全天比例(%)
    amplitude: float      # 振幅 (high-low)/base_price * 100

    # 额外指标（分类辅助）
    push_up_style: str = ''     # 拉升方式：早盘脉冲/午盘拉升/尾盘偷袭/全天稳健
    board_quality: str = ''     # 板的质量：一字板/实体板/烂板/非涨停
    sector_leader: bool = False # ⚠️ P2-5：死字段，从未被任何模块使用，保留签名兼容

    # 时间维度特征（predictor 调节置信度用，外部调用时由选股模块填入）
    consec_days: int = 0        # 连续涨停天数（0=非连板）
    cycle_position: str = ''    # 'early'/'mid'/'late'/'unknown'（周期中所处位置）

    # ── 一字板精确判断（逐分钟价格一致性）─────────────────────────
    # 由 extract_features() 在有完整分钟数据时计算
    consistency_at_limit: float = 0.0
        # 涨停一字板精确度：全天价格 == 涨停价的分钟比例（0.0~1.0）
        # 修复后 A 类要求 consistency_at_limit >= 0.99
    consistency_at_lower: float = 0.0
        # 跌停一字板精确度：全天价格 == 跌停价的分钟比例（0.0~1.0）
        # 跌停一字板要求 consistency_at_lower >= 0.99
