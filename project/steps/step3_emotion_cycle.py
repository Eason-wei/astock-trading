"""
Step 3: 情绪周期定位
输入：fupan_data的情绪字段
输出：情绪周期 + 仓位规则 + 操作节奏
认知执行：冰点期认知（4/03→4/07→4/08完整循环）已固化到cognitions，
          不同情绪阶段有不同的操作节奏

P0-2 修复：position 字段运行时从 PositionRules 获取，
          不再与 step1 / config.py / position_rules.py 四处重复定义造成不一致
"""
from typing import Dict, Any
import sys
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'decision'))
from decision import PositionRules


# 仓位配置统一从 decision/position_rules.py 获取，避免四处硬编码不一致
_pr = PositionRules()

EMOTION_CONFIG = {
    '冰点期': {
        'position': _pr.get_stage_config('冰点期').base,  # 运行时从 PositionRules 读取
        'strategy': '结构性机会轻仓参与，不追高，等回调',
        'key_signal': '强势龙头穿越冰点',
        'cycle_position': '退潮末期/下一波启动前',
        'op_rhythm': 'T日确认冰点→T+1跟龙头→T+2~T+3高低切换',
    },
    '退潮期': {
        'position': _pr.get_stage_config('退潮期').base,
        'strategy': '空仓观望，不参与补涨',
        'key_signal': '跌停扩散/炸板增多',
        'cycle_position': '退潮中段',
        'op_rhythm': '休息为主，等待冰点',
    },
    '修复期': {
        'position': _pr.get_stage_config('修复期').base,
        'strategy': '轻仓试探，寻找率先反弹的标的',
        'key_signal': '昨日强势股不再补跌',
        'cycle_position': '退潮末期/启动前期',
        'op_rhythm': '试探性建仓，快进快出',
    },
    '升温期': {
        'position': _pr.get_stage_config('升温期').base,
        'strategy': '积极操作，聚焦主线龙头',
        'key_signal': '涨停扩散/情绪持续回暖',
        'cycle_position': '启动/发酵阶段',
        'op_rhythm': '持仓待涨，跟随趋势',
    },
    '高潮期': {
        'position': _pr.get_stage_config('高潮期').base,
        'strategy': '积极但注意撤退信号，分批止盈',
        'key_signal': '一字板增多/连板加速',
        'cycle_position': '高潮/赶顶阶段',
        'op_rhythm': '边打边退，锁定利润',
    },
    '降温期': {
        'position': _pr.get_stage_config('降温期').base,
        'strategy': '分歧加剧，观望或轻仓',
        'key_signal': '炸板增多/龙头开板',
        'cycle_position': '分歧阶段',
        'op_rhythm': '高位股分批离场',
    },
}


def run(fupan: Dict, **kwargs) -> Dict[str, Any]:
    """
    情绪周期定位
    """
    qingxu = fupan.get('qingxu', 'N/A')
    degree_market = fupan.get('degree_market', 0)
    degree_top = fupan.get('degree_top', 0)
    top_rate = fupan.get('top_rate', 0)

    config = EMOTION_CONFIG.get(qingxu)
    if config is None:
        logging.warning(f"[step3] 未知情绪 '{qingxu}'，回退到'修复期'")
        config = EMOTION_CONFIG['修复期']

    result = {
        'date': fupan.get('date'),
        'qingxu': qingxu,
        'degree_market': degree_market,
        'degree_top': degree_top,
        'top_rate': top_rate,
        'cycle_position': config['cycle_position'],
        'strategy': config['strategy'],
        'key_signal': config['key_signal'],
        'op_rhythm': config['op_rhythm'],
        'base_position': config['position'],
        'verdict': _get_emotion_verdict(qingxu, degree_market, top_rate),
    }

    # ===== 冰点期特殊检查 =====
    if qingxu == '冰点期':
        result['ice_check'] = _ice_point_check(fupan)

    # ===== 高潮期特殊检查 =====
    if qingxu == '高潮期':
        result['peak_check'] = _peak_check(fupan)

    return result


def _ice_point_check(fupan: Dict) -> Dict[str, Any]:
    """冰点期特殊检查"""
    up = fupan.get('up_num', 0)
    down = fupan.get('down_num', 0)
    # stop_num（停牌数）：暂不参与冰点判断，保留供未来扩展
    bottom = fupan.get('bottom_num', 0)  # 跌停数（真正的亏钱效应）
    top = fupan.get('top_num', 0)
    zhuxian = fupan.get('zhuxian', [])

    check = {}

    # 全停牌防护
    if up == 0 and down == 0:
        check['ice_type'] = '市场失效'
        check['ice_warning'] = '涨跌停家数均为0，市场可能停牌或数据异常'
        return check

    # 冰点类型判断（使用真正的跌停家数bottom_num）
    if bottom > 20:
        check['ice_type'] = '踩踏冰点'
        check['ice_warning'] = '跌停家数过多，是踩踏型冰点，反弹后更要快跑'
    elif down / max(up, 1) > 5:
        check['ice_type'] = '扩散冰点'
        check['ice_warning'] = '下跌家数是上涨5倍以上，典型的扩散冰点，有结构性机会'
    else:
        check['ice_type'] = '温和冰点'
        check['ice_warning'] = '冰点温和，可能快速转向修复期'

    # 强势龙头信号
    if zhuxian:
        top_plate = zhuxian[0]
        if top_plate.get('plate_topnum', 0) >= 5:
            check['strong_dragon'] = True
            check['dragon_info'] = f"主线{top_plate.get('plate_name')}有{top_plate.get('plate_topnum')}只涨停，强势龙头穿越冰点"
        else:
            check['strong_dragon'] = False
            check['dragon_info'] = '无明确主线龙头穿越冰点'

    return check


def _peak_check(fupan: Dict) -> Dict[str, Any]:
    """高潮期特殊检查"""
    top = fupan.get('top_num', 0)
    top_rate = fupan.get('top_rate', 0)

    check = {}

    if top >= 80:
        check['overheated'] = True
        check['peak_warning'] = '涨停数过多(>80)，高潮过热的信号，注意撤退'
    elif top >= 60:
        check['overheated'] = False
        check['peak_warning'] = '涨停数仍高，高潮持续中'
    else:
        check['overheated'] = False
        check['peak_warning'] = '涨停数有所回落，高潮可能降温'

    if top_rate < 70:
        check['封板率低'] = True
        check['封板率_warning'] = f'封板率仅{top_rate:.0f}%，说明炸板较多，高潮末期特征'
    else:
        check['封板率低'] = False

    return check


def _get_emotion_verdict(qingxu: str, degree: int, top_rate: float) -> str:
    """生成情绪判决"""
    if qingxu == '冰点期' and degree < 15:
        return "🚨 极度冰点，轻仓或空仓观望"
    elif qingxu == '冰点期':
        return "⚠️ 冰点期，结构性机会轻仓参与"
    elif qingxu == '高潮期' and top_rate < 70:
        return "🔥 高潮期末端，封板率低，注意撤退"
    elif qingxu == '高潮期' and degree >= 80:
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
    return f"❓ 未定义情绪({qingxu})"
