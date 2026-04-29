"""
Step 1: 全局扫描 - 快速诊断
输入：T日数据快照
输出：当日市场画像 + 风险信号 + 机会信号
认知执行：用冰点期认知验证特征，非冰点期用仓位规则

P0-2 修复：仓位规则统一从 decision/position_rules.py 获取，
不再在 step1 里重复硬编码（避免4处定义不一致的问题）
"""
from typing import Dict, Any
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'decision'))
from decision import PositionRules

# 模块级单例（与 step3 的 _pr 一致）
_pr = PositionRules()


def run(fupan: Dict, lianban: Dict = None, **kwargs) -> Dict[str, Any]:
    """
    全局扫描 - 30秒诊断市场状态
    """
    def _parse_up_down_ratio(val):
        """解析zdb字段，返回上涨/下跌比值
        '1.7 : 1' -> 1.7（上涨是下跌的1.7倍）
        '1 : 1.5' -> 0.67（上涨是下跌的0.67倍）
        '1 : 3.6' -> 0.278（上涨是下跌的0.278倍）
        """
        if val is None:
            return 1.0
        if isinstance(val, str) and ':' in val:
            try:
                parts = val.split(':')
                left, right = float(parts[0].strip()), float(parts[1].strip())
                return left / right if right != 0 else 1.0
            except:
                return 1.0
        return 1.0

    # ===== 构建梯队数据（从 lianban.lianban_list，与 Step4 保持一致）=====
    ladder = {}
    if lianban:
        tier_map = {}
        for grp in lianban.get('lianban_list', []):
            tag = grp.get('tag', '')
            tier_map[tag] = {
                'name': grp.get('name', ''),
                'cnt': len(grp.get('list', [])),
                'stocks': [{'name': s.get('stock_name', ''), 'code': s.get('stock_code', ''),
                            'plates': s.get('plates', ''), 'days_top': s.get('stock_day_top', '')}
                           for s in grp.get('list', [])],
            }
        ladder = tier_map

    result = {
        'date': fupan.get('date'),
        'qingxu': fupan.get('qingxu', 'N/A'),
        'degree_market': int(fupan.get('degree_market', 0)),
        'degree_top': int(fupan.get('degree_top', 0)),
        'up_num': int(fupan.get('up_num', 0)),
        'down_num': int(fupan.get('down_num', 0)),
        'same_num': int(fupan.get('same_num', 0)),
        'stop_num': int(fupan.get('stop_num', 0)),   # 停牌家数
        'bottom_num': int(fupan.get('bottom_num', 0)), # 跌停家数（真实亏钱效应指标）
        'top_num': int(fupan.get('top_num', 0)),
        'up_down_ratio': _parse_up_down_ratio(fupan.get('zdb')),  # 从fupan.zdb解析，格式"X:Y"→X/Y
        'top_rate': float(fupan.get('top_rate', 0)),
        'amount': float(fupan.get('amount', 0)),
        'diff_amount': float(fupan.get('diff_amount', 0)),
        'ladder': ladder,  # P1-12修复：之前用fupan.get('ladder')永远为空，改为从lianban.lianban_list构建
        'zhuxian': fupan.get('zhuxian', []),
    }

    # P0-A 修复：用 _pr 实例（已在模块级定义）
    # P0-B 修复：先初始化 risk_signals，避免 NameError
    risk_signals = []
    qingxu = result['qingxu']
    base_position = _pr.get_stage_config(qingxu).base
    position_label = _pr.get_position_label(qingxu)

    # ===== 风险信号评估 =====

    degree = result['degree_market']
    if degree < 30:
        risk_signals.append(f"⚠️ 情绪冰点(degree={degree})，极度谨慎")
    if result['down_num'] / max(result['up_num'], 1) > 3:
        risk_signals.append(f"🚨 下跌/上涨={result['down_num']/result['up_num']:.1f}倍，亏钱效应极强")
    if result['bottom_num'] > 30:
        risk_signals.append(f"🚨 跌停{result['bottom_num']}只，市场极弱")
    if result['up_down_ratio'] > 5:
        risk_signals.append(f"⚠️ 上涨/下跌={result['up_down_ratio']:.1f}倍，极端偏多谨慎")
    elif result['up_down_ratio'] < 0.33:  # 下跌是上涨的3倍以上
        inverse = 1 / result['up_down_ratio']
        inverse = min(inverse, 10.0)  # 最多显示10倍，避免极端值失真
        risk_signals.append(f"⚠️ 下跌/上涨={inverse:.1f}倍，极端偏空谨慎")

    # ===== 机会信号评估 =====
    opportunity_signals = []
    if result['top_num'] >= 50:
        opportunity_signals.append(f"✅ 涨停{result['top_num']}只，结构性机会存在")
    if degree >= 50 and degree < 80:
        opportunity_signals.append(f"✅ 情绪适中(degree={degree})，可操作")
    if result['zhuxian'] and len(result['zhuxian']) >= 3:
        opportunity_signals.append(f"✅ 主线明确({result['zhuxian'][0]['plate_name']}领涨)，资金有聚焦")

    # ===== 仓位建议 =====
    if risk_signals:
        base_position = min(base_position, 0.10)  # 有风险信号则仓位不超10%

    if risk_signals:
        position_adjustment = '降仓'
    elif qingxu in ('退潮期', '降温期'):
        # 弱势阶段：有机会信号则"降仓观察"，无机会信号才"空仓观望"
        position_adjustment = '降仓观察' if opportunity_signals else '空仓观望'
    elif qingxu in ('升温期', '高潮期'):
        position_adjustment = '可适度积极'
    else:
        position_adjustment = '正常'

    result['risk_signals'] = risk_signals
    result['opportunity_signals'] = opportunity_signals
    result['base_position'] = base_position
    result['position_adjustment'] = position_adjustment
    result['position_label'] = position_label
    result['market_structure'] = lianban.get('market_structure') if lianban else None

    # ===== 市场画像 =====
    up_down_ratio = result['up_num'] / max(result['down_num'], 1)
    result['market_picture'] = {
        'up_down_ratio': round(up_down_ratio, 2),
        'verdict': _get_market_verdict(result),
    }

    return result


def _get_market_verdict(data: Dict) -> str:
    """生成市场判决"""
    qingxu = data['qingxu']
    degree = data['degree_market']
    top = data['top_num']
    stop = data['stop_num']

    if qingxu == '冰点期' and degree < 20:
        return "🚨 极度冰点，轻仓或空仓观望"
    elif qingxu == '冰点期':
        return "⚠️ 冰点期，结构性机会轻仓参与"
    elif qingxu == '高潮期' and top >= 80:
        return "🔥 高潮期，仓位积极但注意撤退信号"
    elif qingxu == '高潮期':
        return "📈 高潮期，积极操作但控制节奏"
    elif qingxu == '退潮期':
        return "📉 退潮期，空仓观望"
    elif qingxu == '升温期':
        return "⬆️ 启动期，择机积极"
    elif qingxu == '修复期':
        return "🔧 修复期，轻仓试探"
    elif qingxu == '降温期':
        return "📊 分歧期，观望为主"
    else:
        return f"❓ 未定义情绪({qingxu})"
