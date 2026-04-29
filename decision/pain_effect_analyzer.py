"""
pain_effect_analyzer.py — 亏钱效应专项分析模块
============================================================
设计原则：点(个股/龙头) + 线(连板梯队) + 面(市场整体)
替代原有的粗糙 pain_trend 推断（原来只有2个信号）

【一票否决】触发时总分直接压至阈值：
  ① 龙头高位炸板（炸板比例≥25% + 高位板≥5）→ ≤25
  ② 昨日涨停指数跌幅>3% → ≤30
  ③ 涨跌比<0.3 → ≤20
  ④ 跌停家数≥15 + 封板率<50% → ≤20
  ⑤ 封板率<40% + 涨跌比<0.5 → ≤30

【四维度评分】
  维度一：龙头高位股  权重 25%
  维度二：连板生态    权重 25%
  维度三：涨停质量    权重 35%  ← 最直接反映当日亏钱
  维度四：市场整体    权重 15%

输出字段：
  score       综合评分（0-100）
  level       四档文字描述
  trend       较昨日变化
  signals     关键信号列表
  warnings    风险警示列表
  action      操作建议
  breakdown   各维度得分明细

使用方式：
  from decision.pain_effect_analyzer import run, print_report
  result = run(fupan_data_dict)
  print(print_report(result))
"""

import json
from typing import Dict, Any, List, Optional


# ─────────────────────────────────────────────────────────
# 字段定义（来自 MongoDB fupan_data）
# ─────────────────────────────────────────────────────────
# 注意：以下为核心维度字段，非全部 MongoDB 字段
# zhaban（炸板家数）：直接取 MongoDB fupan_data.open_num
# continue_top_num（连板家数）：直接取 MongoDB fupan_data.continue_top_num
# zdb：涨跌比（字符串格式如 '1 : 1.6'，表示上涨:下跌的比例），本模块不使用
# MongoDB未使用字段：date/sh_rate/sh_num/top_num/same_num/
#   stop_num/degree_market/degree_top/long_code/long_name/cyb_num/sz_rate/cyb_rate/
#   sz_num/zz500_rate/zz500_num/sz50_rate/sz50_num/hs300_rate/hs300_num/
#   bx_money/degree_market5/degree_top5/yugu_amount/zhuxian(主线板块)
FIELD_DEFS = {
    "bottom_num":          "跌停家数（当日跌停股票数）",
    "damian":              "大面家数（开盘→收盘跌幅>10%）",
    "top_rate":            "封板率（涨停中封住的比例，%）",
    "yesterday_top_rate":  "昨日涨停溢价（昨日涨停股今日平均涨幅，%）",
    "highopen_rate":       "高开率（昨日涨停股今日高开比例，%）",
    "long_ban":            "市场最高连板高度（数字，如7）",
    "continue_top_num":    "连板中继股数量（≥2连板的股票数，含龙头）",
    "up_num":              "上涨家数",
    "down_num":            "下跌家数",
    "amount":              "当日成交额（亿）",
    "diff_amount":         "成交额较上日变化（亿）",
    "ban1": "一板家数",
    "ban2": "二板家数",
    "ban3": "三板家数",
    "ban4": "四板家数",
    "ban5": "五板家数",
    "ban6": "六板家数",
    "ban7": "七板及以上家数",
}


# ─────────────────────────────────────────────────────────
# 一票否决检查
# ─────────────────────────────────────────────────────────


def _check_veto(fupan: Dict) -> tuple[Optional[float], List[str]]:
    """
    检查一票否决条件。
    返回 (override_score, veto_reasons)
    - override_score=None 表示未触发否决
    - override_score=float 表示触发，值为强制总分上限
    """
    veto_reasons = []

    zhaban = int(fupan.get("open_num", 0) or 0)
    top_rate = float(fupan.get("top_rate", 100))
    long_ban = int(fupan.get("long_ban", 0) or 0)
    yesterday_top_rate = float(fupan.get("yesterday_top_rate", 0))
    up_num = int(fupan.get("up_num", 0))
    down_num = int(fupan.get("down_num", 1))
    zhangdie_ratio = up_num / max(down_num, 1)
    bottom_num = int(fupan.get("bottom_num", 0))

    # top_num（收盘涨停家数）已废弃：否决条件不依赖涨停家数绝对值
    # 旧否决条件曾用 top_num < XX，后改为 top_rate（封板率）更准确
    # top_num = int(fupan.get("top_num", 0) or 0)  # 保留声明，避免 caller 侧字段断裂

    # ① 龙头高位炸板（极端情况 → 直接否决）
    # 收紧阈值：炸板>=15 + 封板率<=70% + 高位>=6板 → 极端危险，直接禁止
    if zhaban >= 15 and top_rate <= 70 and long_ban >= 6:
        veto_reasons.append(f"龙头高位炸板（极端）：炸板{zhaban}只(封板率{top_rate:.0f}%)+最高{long_ban}板")
        return 25.0, veto_reasons

    # ─────────────────────────────────────────────────────────
    # 以下为非否决类警示，在 run() 中单独处理降权
    # ─────────────────────────────────────────────────────────

    # ①b 高温市场高位炸板警示（degree_market>=55 → 不否决，但记录警示）
    # 高温市场里zhaban=10~14 + top_rate<=75 + long_ban>=5 代表高位接力压力大
    # 阈值宽松是因为高温市场本身是好事，但高位炸板仍需警示
    degree_market = float(fupan.get("degree_market", 0))
    if (zhaban >= 10 and top_rate <= 75 and long_ban >= 5
            and degree_market >= 55):
        veto_reasons.append(
            f"高温市场高位炸板警示：炸板{zhaban}只(封板率{top_rate:.0f}%)+最高{long_ban}板"
            f"(温度{degree_market:.0f}≥55) → 降权处理"
        )
        # 不返回，继续走四维度，最后在 run() 中降权

    # ② 昨日涨停指数跌幅>3%
    if yesterday_top_rate < -3:
        veto_reasons.append(f"昨日涨停指数跌幅>{abs(yesterday_top_rate):.1f}%，最强资金集体亏损")
        return 30.0, veto_reasons

    # ③ 涨跌比<0.3（市场全面恐慌）
    if zhangdie_ratio < 0.3:
        veto_reasons.append(f"涨跌比={zhangdie_ratio:.2f}<0.3，市场全面恐慌")
        return 20.0, veto_reasons

    # ④ 跌停≥15 + 封板率<50%（最强资金被闷杀）
    # 原阈值<60%与维度三top_rate<40%的缓冲区间冲突
    # 调整为<50%：与维度三偏低区间(40~60)区分开，更精准
    if bottom_num >= 15 and top_rate < 50:
        veto_reasons.append(f"跌停={bottom_num}+封板率={top_rate:.0f}%：最强资金被闷杀")
        return 20.0, veto_reasons

    # ⑤ 封板率<40% + 涨跌比<0.5
    if top_rate < 40 and zhangdie_ratio < 0.5:
        veto_reasons.append(f"封板率={top_rate:.0f}%+涨跌比={zhangdie_ratio:.2f}：炸板潮+跌多涨少")
        return 30.0, veto_reasons

    return None, veto_reasons


# ─────────────────────────────────────────────────────────
# 维度一：龙头高位股（权重 25%）
# ─────────────────────────────────────────────────────────
def _score_long_ban(fupan: Dict) -> Dict[str, Any]:
    """
    评估龙头高度、跟风质量、大面扩散。
    核心逻辑：高位≠危险，高位+跟风不足=危险
    """
    long_ban = int(fupan.get("long_ban", 0) or 0)
    continue_top_num = int(fupan.get("continue_top_num", 0))
    # highopen_rate 已在维度三处理，维度一不再重复计算
    damian = int(fupan.get("damian", 0))

    base = 70
    signals = []
    warnings = []

    # 维度一只看跟风质量（highopen_rate 已在维度三处理，避免双重计数）
    # 跟风质量：高位龙头需要跟风资金充足才算安全
    if long_ban >= 5:
        if continue_top_num < 5:
            base -= 20
            signals.append("龙头孤家寡人")
            warnings.append(f"最高{long_ban}板仅{continue_top_num}只连板股跟风，接力资金不足")
        elif continue_top_num >= 10:
            base += 10
            signals.append(f"龙头行情：高位{long_ban}板+跟风充足({continue_top_num}只)")
    elif long_ban >= 3:
        if continue_top_num < 3:
            base -= 10
            signals.append("跟风不足")
            warnings.append(f"{long_ban}板跟风仅{continue_top_num}只，小心见顶")

    # 大面扩散
    if damian >= 10:
        base -= 25
        warnings.append(f"大面扩散：{damian}只大面股，亏钱效应大规模扩散")
    elif damian >= 5:
        base -= 15
        warnings.append(f"大面局部扩散：{damian}只，注意风险")
    elif damian >= 1:
        base -= 5
        signals.append(f"少量大面：{damian}只")

    # 炸板潮（高位股风险）：用 open_num
    zhaban = int(fupan.get("open_num", 0) or 0)
    if zhaban >= 8 and long_ban >= 4:
        warnings.append(f"炸板潮：{zhaban}只炸板，高位股风险大")

    score = max(0, min(100, base))
    return {"score": score, "signals": signals, "warnings": warnings}


# ─────────────────────────────────────────────────────────
# 维度二：连板生态（权重 25%）
# ─────────────────────────────────────────────────────────
def _score_ladder(fupan: Dict) -> Dict[str, Any]:
    """
    评估连板梯队健康度：晋级率、断层、梯队空虚。
    断层扣分：连续断层视为"大断层"加重扣分。
    """
    ban_counts = {
        7: int(fupan.get("ban7", 0)),
        6: int(fupan.get("ban6", 0)),
        5: int(fupan.get("ban5", 0)),
        4: int(fupan.get("ban4", 0)),
        3: int(fupan.get("ban3", 0)),
        2: int(fupan.get("ban2", 0)),
        1: int(fupan.get("ban1", 0)),
    }

    base = 70
    signals = []
    warnings = []

    # 晋级率（ban1→ban2）
    ban1 = ban_counts[1]
    ban2 = ban_counts[2]
    if ban1 > 0:
        rate = ban2 / ban1 * 100
        if rate >= 30:
            base += 10
            signals.append(f"晋级率={rate:.1f}%，正常")
        elif rate >= 20:
            base -= 10
            signals.append(f"晋级率={rate:.1f}%，偏低")
            warnings.append(f"首板→二板晋级率偏低，{ban1}只一板中仅{ban2}只晋级")
        else:
            base -= 20
            signals.append(f"晋级率={rate:.1f}%，极低")
            warnings.append("晋级率<20%，首板追入风险极大")
    else:
        # ban1==0 时：既无法算晋级率，也是极弱信号
        signals.append("晋级率=0%（无首板），极度虚弱")
        warnings.append("无首板股，市场极度虚弱")

    # 断层检测（连续断层=大断层）
    gap_count = 0
    max_consecutive_gap = 0
    current_consecutive = 0

    for height in range(7, 1, -1):  # 只检查7~2层，ban1不可能是断层
        cnt = ban_counts.get(height, 0)
        if cnt == 0:
            has_upper = any(ban_counts.get(h, 0) > 0 for h in range(height + 1, 8))  # height+1~7层
            has_lower = ban_counts.get(height - 1, 0) > 0  # 紧邻下层
            if has_upper and has_lower:
                gap_count += 1
                current_consecutive += 1
                max_consecutive_gap = max(max_consecutive_gap, current_consecutive)
        else:
            current_consecutive = 0

    if gap_count > 0:
        if max_consecutive_gap >= 3:
            base -= 30
            warnings.append(f"大断层：连续{max_consecutive_gap}级断层，梯队严重断裂")
            signals.append(f"断层{max_consecutive_gap}处（连续{max_consecutive_gap}层）")
        elif max_consecutive_gap == 2:
            base -= 20
            warnings.append(f"连续断层：连续{max_consecutive_gap}级断层，晋级链条中断")
            signals.append(f"断层{max_consecutive_gap}处（连续{max_consecutive_gap}层）")
        else:
            base -= gap_count * 15
            warnings.append(f"断层：{gap_count}处断层")
            signals.append(f"断层{gap_count}处")
    else:
        signals.append("梯队完整，无断层")

    # 梯队空虚
    if ban1 < 20:
        base -= 10
        warnings.append(f"首板家数={ban1}，首板资金不活跃")
    if ban2 < 3 and ban1 >= 10:
        base -= 10
        warnings.append(f"二板家数={ban2}，二板接力极差")

    score = max(0, min(100, base))
    return {
        "score": score,
        "signals": signals,
        "warnings": warnings,
        "detail": {
            "ban1_to_ban2_rate": round(ban2 / max(ban1, 1) * 100, 1),
            "gap_count": gap_count,
            "max_consecutive_gap": max_consecutive_gap,
        }
    }


# ─────────────────────────────────────────────────────────
# 维度三：涨停质量（权重 35%）
# ─────────────────────────────────────────────────────────
def _score_seal_quality(fupan: Dict) -> Dict[str, Any]:
    """
    评估封板质量、昨日溢价、高开率。
    权重最高，直接反映当日亏钱效应。
    """
    top_rate = float(fupan.get("top_rate", 75))
    yesterday_top_rate = float(fupan.get("yesterday_top_rate", 0))
    highopen_rate = float(fupan.get("highopen_rate", 60))
    damian = int(fupan.get("damian", 0))

    base = 60
    signals = []
    warnings = []

    # 封板率
    if top_rate >= 80:
        base += 20
        signals.append(f"封板率={top_rate:.0f}%，封板坚定")
    elif top_rate >= 60:
        signals.append(f"封板率={top_rate:.0f}%，正常")
    elif top_rate >= 40:
        base -= 15
        signals.append(f"封板率={top_rate:.0f}%，偏低")
        warnings.append("封板率<60%，炸板率高，次日溢价压力大")
    else:
        base -= 30
        signals.append(f"封板率={top_rate:.0f}%，极低")
        warnings.append("封板率<40%，炸板潮，亏钱效应严重")

    # 昨日涨停溢价
    if yesterday_top_rate >= 3:
        base += 10
        signals.append(f"昨日涨停溢价={yesterday_top_rate:.2f}%，溢价好")
    elif yesterday_top_rate >= 1:
        signals.append(f"昨日涨停溢价={yesterday_top_rate:.2f}%，正常")
    elif yesterday_top_rate >= 0:
        base -= 10
        signals.append(f"昨日涨停溢价={yesterday_top_rate:.2f}%，偏低")
        warnings.append("昨日涨停溢价<1%，追板资金次日无利")
    else:
        base -= 20
        signals.append(f"昨日涨停溢价={yesterday_top_rate:.2f}%，负溢价")
        warnings.append("昨日涨停负溢价，最强资金被闷杀！")

    # 高开率
    if highopen_rate >= 70:
        base += 10
        signals.append(f"高开率={highopen_rate:.0f}%，高开接力好")
    elif highopen_rate < 50:
        base -= 10
        signals.append(f"高开率={highopen_rate:.0f}%，接力不足")
        warnings.append("高开率<50%，市场风险偏好低")

    # ⚠️ damian不在此处扣分（维度一已对大面扩散扣分，此处只反映涨停质量本身）
    # 涨停质量的直接指标：封板率、昨日溢价、高开率

    # 炸板数量扣分（直接反映当日封板质量）
    # zhaban = open_num（MongoDB fupan_data 字段，代表炸板家数）
    zhaban = int(fupan.get("open_num", 0) or 0)
    if zhaban >= 20:
        base -= 15
        warnings.append(f"炸板{zhaban}只，封板质量差")
    elif zhaban >= 12:
        base -= 10
        warnings.append(f"炸板{zhaban}只，封板质量偏弱")
    elif zhaban >= 8:
        base -= 5
        signals.append(f"炸板{zhaban}只")

    score = max(0, min(100, base))
    return {"score": score, "signals": signals, "warnings": warnings}


# ─────────────────────────────────────────────────────────
# 维度四：市场整体（权重 15%）
# ─────────────────────────────────────────────────────────
def _score_market_breadth(fupan: Dict) -> Dict[str, Any]:
    """
    评估涨跌家数比、量能变化。
    注意：大盘指数≠情绪，题材小票的涨跌家数比才是核心。
    """
    up_num = int(fupan.get("up_num", 0))
    down_num = int(fupan.get("down_num", 1))
    diff_amount = float(fupan.get("diff_amount", 0))
    amount = float(fupan.get("amount", 0))

    base = 60
    signals = []
    warnings = []

    # 涨跌比
    zhangdie_ratio = up_num / max(down_num, 1)
    if zhangdie_ratio >= 1.5:
        base += 20
        signals.append(f"涨跌比={zhangdie_ratio:.2f}，多方强势")
    elif zhangdie_ratio >= 0.8:
        signals.append(f"涨跌比={zhangdie_ratio:.2f}，正常")
    elif zhangdie_ratio >= 0.5:
        base -= 15
        signals.append(f"涨跌比={zhangdie_ratio:.2f}，空方占优")
        warnings.append("涨跌比<0.8，空方主导，亏钱效应扩散中")
    else:
        base -= 30
        signals.append(f"涨跌比={zhangdie_ratio:.2f}，恐慌")
        warnings.append("涨跌比<0.5，市场全面恐慌！")

    # 量能变化
    if diff_amount < -5000:
        base -= 20
        warnings.append(f"大幅缩量{diff_amount:.0f}亿，资金撤退")
    elif diff_amount < -2000:
        base -= 10
        warnings.append(f"缩量{diff_amount:.0f}亿，活跃资金减少")
    elif diff_amount > 3000:
        base += 5
        signals.append(f"放量{diff_amount:.0f}亿，活跃资金入场")

    score = max(0, min(100, base))
    return {
        "score": score,
        "signals": signals,
        "warnings": warnings,
        "detail": {
            "zhangdie_ratio": round(zhangdie_ratio, 3),
            "diff_amount": round(diff_amount, 1),
        }
    }


# ─────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────
def run(fupan: Dict, history: Optional[Dict] = None) -> Dict[str, Any]:
    """
    亏钱效应综合分析。

    Args:
        fupan:   MongoDB fupan_data（情绪数据）
        history: 历史pain评分 dict，格式 {"YYYY-MM-DD": score}

    Returns:
        {
            "score": float,          # 综合评分 0-100
            "level": str,            # 四档文字
            "trend": str,            # 较昨日变化
            "signals": List[str],   # 关键信号
            "warnings": List[str],   # 风险警示
            "action": str,           # 操作建议
            "breakdown": Dict,      # 各维度得分
            "veto_triggered": bool,
            "veto_reasons": List[str],
            "date": str,
        }
    """
    if history is None:
        history = {}

    date = fupan.get("date", "N/A")
    all_signals = []
    all_warnings = []

    # 一票否决（含①b警示，但①b不触发hard veto，单独降权处理）
    override_score, all_veto_reasons = _check_veto(fupan)
    veto_triggered = override_score is not None

    # ①b 高温市场高位炸板警示：降权10分，不触发hard否决
    highheat_warning = [r for r in all_veto_reasons if "高温市场高位炸板警示" in r]
    if highheat_warning:
        all_warnings.extend(highheat_warning)
        # veto_reasons 只保留真正的否决原因（不含①b）
        veto_reasons = [r for r in all_veto_reasons if "高温市场高位炸板警示" not in r]
    else:
        veto_reasons = all_veto_reasons

    if veto_triggered:
        # 否决触发时直接用override分数，跳过四维度计算
        final_score = override_score
        all_signals = []
        all_warnings = []
        breakdown = {
            "龙头高位股": "N/A (veto)",
            "连板生态":   "N/A (veto)",
            "涨停质量":   "N/A (veto)",
            "市场整体":   "N/A (veto)",
        }
    else:
        # 四维度评分
        dim1 = _score_long_ban(fupan)
        dim2 = _score_ladder(fupan)
        dim3 = _score_seal_quality(fupan)
        dim4 = _score_market_breadth(fupan)

        all_signals.extend(dim1["signals"])
        all_warnings.extend(dim1["warnings"])
        all_signals.extend(dim2["signals"])
        all_warnings.extend(dim2["warnings"])
        all_signals.extend(dim3["signals"])
        all_warnings.extend(dim3["warnings"])
        all_signals.extend(dim4["signals"])
        all_warnings.extend(dim4["warnings"])

        breakdown = {
            "龙头高位股": dim1["score"],
            "连板生态":   dim2["score"],
            "涨停质量":   dim3["score"],
            "市场整体":   dim4["score"],
        }

        w1, w2, w3, w4 = 0.25, 0.25, 0.35, 0.15
        final_score = (dim1["score"] * w1 +
                       dim2["score"] * w2 +
                       dim3["score"] * w3 +
                       dim4["score"] * w4)

        # ①b 高温市场高位炸板：四维度算完后降权10分（不在否决，在最终分扣除）
        if highheat_warning:
            final_score = max(0, final_score - 10)

    final_score = round(final_score, 1)

    # 去重
    all_signals = list(dict.fromkeys(all_signals))
    all_warnings = list(dict.fromkeys(all_warnings))

    # 档位判断
    if final_score >= 80:
        level = "情绪健康"
        action = "积极操作，控仓参与主线龙头"
    elif final_score >= 60:
        level = "情绪分化"
        action = "控仓去弱留强，回避跟风高位股"
    elif final_score >= 40:
        level = "亏钱效应显现"
        damian = int(fupan.get("damian", 0))
        top_rate = float(fupan.get("top_rate", 75))
        if damian == 0 and top_rate >= 80:
            action = "控仓30%，关注情绪修复机会（damian=0+封板率高）"
        else:
            action = "控仓≤30%，不追高，观望为主"
    else:
        level = "退潮/冰点"
        action = "空仓观望，等待情绪企稳信号"

    # trend：history 中的日期均为当天之前的历史分数
    # history 格式 {"YYYY-MM-DD": score}，由 caller 保证只包含 < date 的数据
    today_str = str(date)
    yesterday_score = None
    if history:
        dates = sorted(history.keys())
        # 找前一日：在 sorted 列表中，today_str 必然不在 history 中（因为 caller 还没写入）
        # 取 dates[-1] 即最近的历史分数（因为 caller 传的是 date 之前的所有数据）
        if dates:
            yesterday_score = history[dates[-1]]
    if yesterday_score is not None:
        diff = final_score - yesterday_score
        if diff > 5:
            trend = f"↑上升(+{diff:.1f})"
        elif diff < -5:
            trend = f"↓下降({diff:.1f})"
        else:
            trend = "→平稳"
    else:
        trend = "数据不足"

    return {
        "score": final_score,
        "level": level,
        "trend": trend,
        "signals": all_signals,
        "warnings": all_warnings,
        "action": action,
        "breakdown": breakdown,
        "veto_triggered": veto_triggered,
        "veto_reasons": veto_reasons,
        "date": date,
    }


# ─────────────────────────────────────────────────────────
# 格式化报告
# ─────────────────────────────────────────────────────────
def print_report(result: Dict) -> str:
    lines = [
        f"\n{'='*56}",
        f"亏钱效应分析报告  {result['date']}",
        f"{'='*56}",
        f"  综合评分：{result['score']}  |  档位：{result['level']}  |  趋势：{result['trend']}",
    ]
    if result["veto_triggered"]:
        lines.append(f"  ⚠️ 一票否决！理由：{result['veto_reasons']}")

    lines.append(f"\n  【四维度得分】")
    bd = result["breakdown"]
    lines.append(f"    龙头高位股：{bd['龙头高位股']} 分")
    lines.append(f"    连板生态：  {bd['连板生态']} 分")
    lines.append(f"    涨停质量：  {bd['涨停质量']} 分")
    lines.append(f"    市场整体：  {bd['市场整体']} 分")

    if result["signals"]:
        lines.append(f"\n  【关键信号】")
        for s in result["signals"]:
            lines.append(f"    • {s}")

    if result["warnings"]:
        lines.append(f"\n  【风险警示】")
        for w in result["warnings"]:
            lines.append(f"    ⚠️ {w}")

    lines.append(f"\n  → 操作建议：{result['action']}")
    lines.append(f"{'='*56}\n")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    # 默认测试数据
    fupan_default = {
        "date": "2026-04-21",
        "bottom_num": 9,
        "damian": 3,
        "top_rate": 80,
        "yesterday_top_rate": 2.7,
        "highopen_rate": 66.32,
        "long_ban": 4,
        "continue_top_num": 8,
        "up_num": 2324,
        "down_num": 2810,
        "amount": 24768,
        "diff_amount": 509,
        "zdb": "1 : 1",    # 涨跌比（本模块不使用，但保留类型正确）
        "ban1": 55,
        "ban2": 13,
        "ban3": 1,
        "ban4": 1,
        "ban5": 1,
        "ban6": 0,
        "ban7": 0,
        # zhaban 已删除：炸板家数统一从 open_num 字段读取
    }

    # 从命令行参数读取 JSON（可选）
    if len(sys.argv) > 1:
        try:
            data = json.loads(sys.argv[1])
            fupan = {**fupan_default, **data}
        except json.JSONDecodeError:
            print("用法: python pain_effect_analyzer.py [json字符串]")
            sys.exit(1)
    else:
        fupan = fupan_default

    result = run(fupan)
    print(print_report(result))
