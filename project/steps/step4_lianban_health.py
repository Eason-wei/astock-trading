"""
Step 4: 连板健康度分析
输入：lianban_data的连板天梯
输出：梯队健康度 + 晋级率 + 断层检测 + 龙头状态
认知执行：梯队断层=空间受限；跟风判断=高位看跟风充分性非绝对高度；
          晋级率<30%说明首板→二板淘汰率高，追首板风险大

建议6新增：相对健康度（relative_health）
  - 绝对健康度（health_score）：当日评分，绝对值
  - 相对健康度（relative_health）：近10天排第几名，判断"今日是否相对更好"
  - 百分位（percentile_rank）：今日超过历史百分之几的日子
"""
from typing import Dict, Any, List


def _compute_relative_health(current_score: int, current_date: str) -> dict:
    """
    建议6：计算相对健康度。
    【2026-04-23 禁用本地文件缓存】强制从 MongoDB 实时查询，不写本地文件。
    当前实现：直接返回"数据不足"，避免缓存干扰。
    """
    # TODO: 未来从 MongoDB pain_effect_scores 集合实时查询近10天数据
    return {'rank': 0, 'percentile': 50, 'label': '数据不足'}


def run(lianban: Dict, **kwargs) -> Dict[str, Any]:
    """
    连板健康度分析
    """
    result = {
        'date': lianban.get('date'),
        'long_code': lianban.get('long_code', 'N/A'),
        'ladder': {},
        'health_score': 0,
        'warnings': [],
        'verdict': 'N/A',
        'suggestion': 'N/A',
    }

    lianban_list = lianban.get('lianban_list') or lianban.get('stock_list', [])  # P3-④fix: 4/7数据用stock_list字段

    # ===== 构建梯队结构 =====
    tier_map = {}
    for grp in lianban_list:
        tag = grp.get('tag', '')
        name = grp.get('name', '')
        cnt = len(grp.get('list', []))
        rate = grp.get('rate', 0)
        stocks = [
            {
                'name': s.get('stock_name', ''),
                'code': s.get('stock_code', ''),
                'plates': s.get('plates', ''),
                'top_time': s.get('top_time_text', 'N/A'),
                'reason': s.get('top_reason', 'N/A')[:60],
            }
            for s in grp.get('list', [])
        ]
        tier_map[tag] = {
            'name': name,
            'tag': tag,
            'cnt': cnt,
            'rate': rate,
            'stocks': stocks,
        }

    result['ladder'] = tier_map

    # ===== 晋级率分析 =====
    ban_sequence = ['ban7', 'ban6', 'ban5', 'ban4', 'ban3', 'ban2', 'ban1']
    ban_counts = {}
    for tag in ban_sequence:
        ban_counts[tag] = tier_map.get(tag, {}).get('cnt', 0)

    # 计算各层晋级率
    progression = []
    for i in range(len(ban_sequence) - 1):
        higher = ban_sequence[i]     # 上层（如ban6）
        lower = ban_sequence[i + 1]   # 下层（如ban5）
        higher_cnt = ban_counts.get(higher, 0)
        lower_cnt = ban_counts.get(lower, 0)

        # 晋级率来源：lianban[N].rate = 昨日ban(N-1)→今日banN的晋级率（%）
        # ban1.rate=187 是首板增长倍数（今日/昨日），不是晋级率，无意义
        # 只有 ban2+ 才有真正的晋级率
        raw_rate = tier_map.get(lower, {}).get('rate', 0) or 0
        actual_rate = None if lower == 'ban1' else raw_rate

        progression.append({
            'from': ban_sequence[i + 1],
            'to': ban_sequence[i],
            'from_cnt': lower_cnt,
            'to_cnt': higher_cnt,
            'rate': actual_rate,  # None for ban1（无晋级率），数字 for ban2+
        })

    result['progression'] = progression

    # ===== 健康度评分（初始化）=====
    health_score = 100
    warnings = []

    # ===== 晋级率链惩罚（层深加权 + 数量门控 + 对数衰减）=====
    # 层深权重：高层晋级成功比低层更能反映市场信心
    DEPTH_WEIGHTS = {
        'ban1': 1.0, 'ban2': 1.1, 'ban3': 1.2,
        'ban4': 1.3, 'ban5': 1.4, 'ban6': 1.5,
    }
    import math as _math

    for prog in progression:
        r = prog['rate']
        lower_cnt = prog['from_cnt']
        depth_key = prog['from']
        depth_weight = DEPTH_WEIGHTS.get(depth_key, 1.0)

        # ban1.rate=None（无晋级率），跳过
        if r is None:
            continue
        # 样本太少时不置信
        if lower_cnt < 5:
            continue  # 样本不足，跳过

        if r == 0:
            continue  # 无数据跳过

        # 对数衰减惩罚：晋级率越低，惩罚越重（接近指数级）
        if r < 30:
            base_penalty = max(0, 20 + 10 * _math.log10(30 / max(r, 1)))
            penalty = round(base_penalty * depth_weight)
            health_score -= penalty
            if r < 10:
                warnings.append(f"⚠️ {prog['from']}→{prog['to']}晋级率{r}%，极低（样本{lower_cnt}只），淘汰严重")
            elif r < 20:
                warnings.append(f"⚠️ {prog['from']}→{prog['to']}晋级率{r}%，偏低（样本{lower_cnt}只）")
            else:
                warnings.append(f"⚠️ {prog['from']}→{prog['to']}晋级率{r}%，偏弱（样本{lower_cnt}只）")

    # ===== 断层检测 =====
    duanceng_flags = []
    for i in range(len(ban_sequence) - 1):
        higher = ban_counts.get(ban_sequence[i], 0)
        lower = ban_counts.get(ban_sequence[i + 1], 0)
        if lower > 0 and higher == 0:
            duanceng_name = tier_map.get(ban_sequence[i], {}).get('name', ban_sequence[i])
            duanceng_flags.append(f"⚠️ {lower}只{ban_sequence[i+1]}晋级到0只{ban_sequence[i]}，{duanceng_name}断层")
            health_score -= 15
            warnings.append(f"⚠️ {ban_sequence[i+1]}断层，空间封顶，上涨高度受限")

    result['duanceng'] = duanceng_flags

    # ===== 跟风不足判断（精细化）=====
    top_ban_tag = None
    top_ban_height = 0
    for tag in ban_sequence:
        if ban_counts.get(tag, 0) > 0:
            top_ban_tag = tag
            top_ban_height = int(tag.replace('ban', ''))
            break

    if top_ban_tag and top_ban_height >= 4:
        top_ban_cnt = ban_counts.get(top_ban_tag, 0)
        lower_cnt = sum(ban_counts.get(t, 0) for t in ban_sequence[ban_sequence.index(top_ban_tag)+1:])
        wind_ratio = lower_cnt / max(top_ban_cnt, 1)
        if wind_ratio < 0.5:
            health_score -= 15
            warnings.append(f"⚠️ 跟风严重不足（跟风比{wind_ratio:.1f}），龙头孤身一人，小心见顶")
        elif wind_ratio < 1.0:
            health_score -= 8
            warnings.append(f"⚠️ 跟风偏少（跟风比{wind_ratio:.1f}），板块支撑不够")

    # ===== 最高板警告（仅文案）=====
    top_ban_warning = ''
    if top_ban_height >= 7:
        top_ban_warning = f"最高板{top_ban_height}板，位置极高，小心退潮"
    elif top_ban_height >= 5:
        top_ban_warning = f"最高板{top_ban_height}板，注意高位风险"
    if top_ban_warning:
        warnings.append(f"⚠️ {top_ban_warning}")

    # ===== 封板率惩罚（参考 top_rate）=====
    # top_rate 通过 kwargs 传入，由 run_real.py 在调用 step4 时传入
    top_rate = kwargs.get('top_rate', 0)
    if top_rate > 0 and top_rate < 60:
        health_score -= 10
        warnings.append(f"⚠️ 封板率仅{top_rate}%，说明炸板较多，高潮末期特征")

    result['health_score'] = max(0, health_score)
    result['warnings'] = warnings

    # ===== 建议6：相对健康度（近10天百分位）=====
    relative = _compute_relative_health(result['health_score'], result.get('date', ''))
    result['relative_health'] = relative['rank']
    result['percentile_rank'] = relative['percentile']
    result['relative_label'] = relative['label']

    # ===== 最终判决（收紧阈值 + 高位板提示）=====
    if result['health_score'] >= 85:
        if top_ban_height >= 6:
            result['verdict'] = f"✅ 连板梯队健康（{result['health_score']}分），最高{top_ban_height}板，注意撤退节奏"
        else:
            result['verdict'] = f"✅ 连板梯队健康（{result['health_score']}分），结构健康"
        result['suggestion'] = "可积极参与连板股"
    elif result['health_score'] >= 50:
        result['verdict'] = f"⚠️ 连板梯队一般，有分化（{result['health_score']}分）"
        result['suggestion'] = "精选主线龙头，跟风股不参与"
    else:
        result['verdict'] = f"🚨 连板梯队不健康，风险大于机会（{result['health_score']}分）"
        result['suggestion'] = "空仓观望，不追高"

    return result
