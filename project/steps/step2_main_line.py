"""
Step 2: 主线分析 - 题材拆解
输入：T日连板数据 + 题材数据
输出：三梯队分类 + 强度评估 + 驱动逻辑

重构（2026-04-22）：
  旧方案：用 lianban zhuxian 字段确定 tier1 → 板块名与 jiuyang 不一致
  新方案：以 jiuyang 板块为标准
    - 对每个 jiuyang 板块，统计其中有多少只在 lianban 里（lianban_num）
    - lianban_num >= 5 → tier1；lianban_num 3-4 → tier2；lianban_num 1-2 → tier3
    - zhuxian 输出改为从 jiuyang 板块构建，龙头信息来自 lianban
    - lianban_list 保留连板梯队原始数据
"""
from typing import Dict, Any, List
import logging


def _truncate_reason(text: str, max_chars: int = 80) -> str:
    """取第一句或前 max_chars 字，避免硬截断在半句话里"""
    if not text:
        return 'N/A'
    # 优先按中文句号/感叹号/问号切分
    for sep in ('。', '！', '？', '\n'):
        if sep in text:
            first = text.split(sep)[0].strip()
            if first:
                return first[:max_chars]
    # 无句号则直接截断
    return text[:max_chars].strip() if text else 'N/A'


def run(lianban: Dict, jiuyang: List[Dict] = None, fupan: Dict = None, full: bool = False, **kwargs) -> Dict[str, Any]:
    date = lianban.get('date')
    result = {
        'date': date,
        'long_code': lianban.get('long_code', 'N/A'),
        'zhuxian': [],       # 基于 jiuyang 板块重建（输出兼容旧格式）
        'lianban_list': {},  # 保留连板梯队原始数据
        'tier1': [],         # 第一梯队：核心主线（jiuyang lianban_num>=5）
        'tier2': [],         # 第二梯队：跟风题材（jiuyang lianban_num 3-4）
        'tier3': [],         # 第三梯队：杂毛（jiuyang lianban_num 1-2）
        'tier1_detail': [],  # [{name, lianban_num, longtou_code, longtou_tag, ...}]
        'suggestion': {},
        'strength_eval': {},
    }

    lianban_list = lianban.get('lianban_list', [])

    # ===== 建立 lianban code -> info 映射 =====
    lianban_code_map: Dict[str, Dict] = {}
    for grp in lianban_list:
        tag = grp.get('tag', '')
        grp_name = grp.get('name', '')
        for s in grp.get('list', []):
            raw = s.get('stock_code', '').replace('sz', '').replace('sh', '').strip()
            if not raw:
                continue
            lianban_code_map[raw] = {
                'tag': tag,
                'name': grp_name,
                'day_top': s.get('stock_day_top', 'N/A'),
                'hand_rate': s.get('stock_hand_rate', 0),
                'top_time': s.get('top_time_text', 'N/A'),
                'reason': s.get('top_reason', 'N/A'),
                'plates': s.get('plates', ''),
            }

    def ban_height(code: str) -> int:
        """计算连板高度，20cm/zhaban/fanbao 排除在外不参与龙头比较"""
        tag = lianban_code_map.get(code, {}).get('tag', '')
        if tag in ('bads', '20cm', 'zhaban', 'fanbao'):
            return -2  # 排除项，不参与龙头排序
        if tag == 'bads':
            return -1  # 晋级失败股
        try:
            return int(tag.replace('ban', '').replace('b', ''))
        except (ValueError, AttributeError):
            return 0

    # ===== 基于 jiuyang 重建三梯队 =====
    # lianban_num = 该 jiuyang 板块中有多少只股在 lianban 里
    plate_stats = []  # [{name, lianban_num, in_lb_codes, jiuyang_stocks}, ...]

    if jiuyang:
        for plate in jiuyang:
            pname = plate.get('name', '').strip()
            if not pname or pname in ('其他', '公告', 'ST板块', '新股'):
                continue  # 排除噪音板块

            stocks = plate.get('list', [])
            in_lb_codes = [
                s['code'].replace('sz', '').replace('sh', '').strip()
                for s in stocks
                if s.get('code') and s['code'].replace('sz', '').replace('sh', '').strip() in lianban_code_map
            ]
            lianban_num = len(in_lb_codes)
            if lianban_num == 0:
                continue

            # 龙头：lianban 里连板高度最高的
            top_code = max(in_lb_codes, key=ban_height) if in_lb_codes else None
            top_info = lianban_code_map.get(top_code, {}) if top_code else {}

            plate_stats.append({
                'name': pname,
                'lianban_num': lianban_num,
                'in_lb_codes': in_lb_codes,
                'top_code': top_code,
                'top_tag': top_info.get('tag', ''),
                'top_name': top_info.get('name', 'N/A'),
                'top_reason': top_info.get('reason', 'N/A'),
                'top_hand_rate': top_info.get('hand_rate', 0),
                'top_time': top_info.get('top_time', 'N/A'),
                'jiuyang_stocks': stocks,
            })

    # 按 lianban_num 降序
    plate_stats.sort(key=lambda x: -x['lianban_num'])

    # 三梯队
    for ps in plate_stats:
        if ps['lianban_num'] >= 5:
            result['tier1'].append(ps['name'])
        elif ps['lianban_num'] >= 3:
            result['tier2'].append(ps['name'])
        else:
            result['tier3'].append(ps['name'])

    # 保存 tier1_detail（供 step5/6 使用）
    result['tier1_detail'] = [
        {
            'name': ps['name'],
            'lianban_num': ps['lianban_num'],
            'longtou_code': ps['top_code'],
            'longtou_tag': ps['top_tag'],
            'longtou_name': ps['top_name'],
            'in_lianban_codes': ps['in_lb_codes'],
        }
        for ps in plate_stats if ps['lianban_num'] >= 5
    ]

    # ===== 重建 zhuxian（输出兼容旧格式，但数据来自 jiuyang）=====
    for rank, ps in enumerate(plate_stats[:6], 1):  # 取 top6
        lt = ps['top_code']
        lt_info = lianban_code_map.get(lt, {})
        entry = {
            'rank': rank,
            'plate_name': ps['name'],
            'lianban_num': ps['lianban_num'],
            'weight': ps['lianban_num'] * 4,  # 估算权重
            'reason': _truncate_reason(ps['top_reason'], 80),
            'longtou': {
                'name': ps['top_name'],
                'code': lt or 'N/A',
                'day_top': lt_info.get('day_top', 'N/A'),
                'hand_rate': lt_info.get('hand_rate', 0),
                'top_time': lt_info.get('top_time', 'N/A'),
                'reason': _truncate_reason(ps['top_reason'], 80),
            }
        }
        result['zhuxian'].append(entry)

    # ===== 连板梯队整理（保留原始数据）=====
    tier_map = {}
    for grp in lianban_list:
        tag = grp.get('tag', '')
        name = grp.get('name', '')
        cnt = len(grp.get('list', []))
        rate = grp.get('rate', 0)
        stocks = []
        for s in grp.get('list', []):
            raw = s.get('stock_code', '').replace('sz', '').replace('sh', '').strip()
            stocks.append({
                'name': s.get('stock_name', ''),
                'code': raw,
                'plates': s.get('plates', ''),
                'reason': s.get('top_reason', 'N/A')[:60],
            })
        tier_map[tag] = {'name': name, 'cnt': cnt, 'rate': rate, 'stocks': stocks if full else stocks[:5]}
    result['lianban_list'] = tier_map

    # ===== 操作建议 =====
    if result['tier1']:
        result['suggestion'] = {
            'focus': f"主线明确：{'/'.join(result['tier1'])}",
            'avoid': "不参与跟风题材",
            'note': f"第二梯队：{'/'.join(result['tier2']) if result['tier2'] else '无'}",
        }
    else:
        result['suggestion'] = {
            'focus': "主线不明确，观望为主",
            'avoid': "无明确主线",
            'note': "等待主线形成",
        }

    # ===== 强度评估 =====
    # zhaban（炸板家数）来自 fupan_data.open_num，不是 lianban_list['zhaban'].cnt
    zhaban_from_fupan = fupan.get('open_num', 0) if fupan else 0
    if fupan and 'open_num' not in fupan:
        logging.warning(f"[step2] fupan 缺少 open_num 字段，zhaban 设为0，可能漏判炸板")
    result['strength_eval'] = _eval_strength(tier_map, result['tier1'], result['tier2'], zhaban=zhaban_from_fupan)

    return result


def _eval_strength(tier_map: Dict, tier1: List, tier2: List, zhaban: int = 0) -> Dict[str, Any]:
    eval_result = {
        'verdict': 'N/A',
        'top_ban': 'N/A',
        'top_ban_height': 0,
        'zhaban_cnt': zhaban,   # 来自 fupan_data.zhaban（全市场炸板家数）
        'bads_cnt': 0,
    }

    # 最高板
    for tag in ['ban7', 'ban6', 'ban5', 'ban4', 'ban3', 'ban2']:
        if tag in tier_map and tier_map[tag]['cnt'] > 0:
            eval_result['top_ban'] = tier_map[tag]['name']
            h = tag.replace('ban', '').replace('b', '').replace('an', '')
            eval_result['top_ban_height'] = int(h) if h.isdigit() else 0
            break

    if 'bads' in tier_map:
        eval_result['bads_cnt'] = tier_map['bads']['cnt']

    if eval_result['zhaban_cnt'] >= 10:
        eval_result['verdict'] = "⚠️ 炸板较多，亏钱效应明显"
    elif eval_result['zhaban_cnt'] >= 5:
        eval_result['verdict'] = "⚠️ 有炸板，注意风险"
    elif eval_result['bads_cnt'] >= 5:
        eval_result['verdict'] = "⚠️ 晋级失败较多，情绪退潮"
    elif tier1:
        eval_result['verdict'] = "✅ 连板健康，晋级顺利"
    else:
        eval_result['verdict'] = "⚠️ 主线不明确"

    return eval_result
