"""
Step 5: 成分股筛选
输入：lianban_data连板股 + jiuyangongshe题材数据 + MySQL分钟数据
输出：候选标的列表 + 筛选理由 + 分钟形态检查

重大重构（2026-04-22）：
  旧方案：用 lianban zhuxian 的板块名去 jiuyang 匹配 → 板块名不一致导致匹配失败
  新方案：直接以 jiuyang 板块名为标准
    - 对每个 jiuyang 板块，统计其中有多少只在 lianban 里 → topnum
    - topnum >= 5 成为 tier1 板块（与 lianban 共用同一数据源，天然对齐）
    - 龙头 = lianban 里连板高度最高的股
    - 候选股 = jiuyang 板块的全部成分股（去重）
    - lianban 数据仅用于标记"在板/跟风"和找龙头
"""
import math
from typing import Dict, Any, List, Optional, Union


# =============================================================================
# 涨跌停价计算（A股规则：round half up 到分）
# =============================================================================

def _get_limit_ratio(code: str, is_st: bool) -> tuple:
    """
    根据股票代码判断涨跌停比例（内部共享逻辑）。
    返回：(limit_ratio, limit_down_ratio, market)
        market: 'main' | 'cyb' | 'kcb' | 'bj'
    """
    pure = code.strip().split('.')[0]
    if pure.startswith('688'):
        market = 'kcb'
        return (0.20, 0.20, market) if not is_st else (0.10, 0.10, market)
    elif pure.startswith('300') or pure.startswith('301'):
        market = 'cyb'
        return (0.20, 0.20, market) if not is_st else (0.10, 0.10, market)
    elif pure.startswith('9') and len(pure) == 6:
        market = 'bj'
        return (0.30, 0.30, market) if not is_st else (0.15, 0.15, market)
    elif pure.startswith('8'):
        market = 'bj'
        return (0.30, 0.30, market) if not is_st else (0.15, 0.15, market)
    else:
        market = 'main'
        return (0.10, 0.10, market) if not is_st else (0.05, 0.05, market)


def _round_limit_price(
    price: float,
    decimals: int,
    market: str,
    prev_close: float
) -> float:
    """
    涨停价四舍五入的核心逻辑。
    A股规则：
        1. 价格必须 >= 0.01元
        2. 对于低价股（prev_close < 2.0），需在基准价上下浮动若干tick，
           找涨幅最接近规定比例的候选价（防止四舍五入导致实际涨幅偏离规定值超过0.5%）
    """
    multiplier = 10 ** decimals
    rounded_price = math.floor(price * multiplier + 0.5) / multiplier
    if rounded_price < 0.01:
        return 0.01
    # 低价股特殊处理
    if prev_close < 2.0:
        return _round_low_price_stock(price, prev_close, market, decimals)
    return rounded_price


def _round_low_price_stock(
    raw_price: float,
    prev_close: float,
    market: str,
    decimals: int
) -> float:
    """
    处理低价股（昨收<2元）的涨停价计算。
    规则：在基准价格上下浮动若干最小变动单位，
          找到涨幅最接近规定比例的候选价。
    """
    # 确定最小变动单位（tick_size）
    if prev_close >= 1.0:
        tick_size = 0.01
    elif prev_close >= 0.1:
        tick_size = 0.001
    else:
        tick_size = 0.0001

    multiplier = 10 ** decimals
    base_price = round(raw_price, decimals)
    candidates = []

    # 在基准价格上下浮动 3 个 tick，穷举候选
    for i in range(-3, 4):
        candidate = base_price + i * tick_size
        if candidate <= 0:
            continue
        actual_ratio = (candidate - prev_close) / prev_close
        # 根据市场确定目标涨幅
        if market == 'bj':
            target_ratio = 0.30 if prev_close >= 1.0 else 0.20
        elif market in ('cyb', 'kcb'):
            target_ratio = 0.20 if prev_close >= 1.0 else 0.10
        else:
            target_ratio = 0.10 if prev_close >= 1.0 else 0.05
        ratio_diff = abs(actual_ratio - target_ratio)
        candidates.append((candidate, ratio_diff))

    if candidates:
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]
    return round(raw_price, decimals)


def calculate_limit_price(
    prev_close: Union[int, float],
    code: str,
    is_st: bool = False,
    round_to: int = 2
) -> float:
    """
    计算A股股票的涨停价（精确四舍五入）。

    Args:
        prev_close: 前收盘价（昨收）
        code:       股票代码，纯数字如 '000062' / '300001' / '688001'
        is_st:     是否为ST/*ST
        round_to:  价格小数位数，默认2位

    Returns:
        涨停价（精确到分）
    """
    if prev_close <= 0:
        raise ValueError(f"前收盘价必须为正数，当前值: {prev_close}")

    limit_ratio, _, market = _get_limit_ratio(code, is_st)
    raw = prev_close * (1.0 + limit_ratio)
    return _round_limit_price(raw, round_to, market, prev_close)


def calculate_price_limits(
    prev_close: Union[int, float],
    code: str,
    is_st: bool = False
) -> tuple:
    """
    同时计算涨停价和跌停价。

    Returns:
        (limit_up, limit_down) — 精确到分的涨停价和跌停价
    """
    limit_up = calculate_limit_price(prev_close, code, is_st)

    _, down_ratio, market = _get_limit_ratio(code, is_st)
    raw_down = prev_close * (1.0 - down_ratio)
    limit_down = _round_limit_price(raw_down, 2, market, prev_close)
    limit_down = max(limit_down, 0.01)
    return limit_up, limit_down


def run(lianban: Dict, jiuyang: List[Dict] = None, mysql_data: Dict = None,
         ds=None, **kwargs) -> Dict[str, Any]:
    """
    Step 5: 成分股筛选
    输入：lianban_data连板股 + jiuyangongshe题材数据 + MySQL分钟数据
    输出：候选标的列表 + 筛选理由 + 分钟形态检查 + 涨停强度因子

    新增（2026-05-02）：
      - ds 参数可选，若提供则将所有候选股的 ABC 因子结果批量写入 MongoDB
        zhangting_strength 集合，供 step6 T+1 预判实时查询。
    """
    date = lianban.get('date')
    result = {
        'date': date,
        'tier1_plates': [],       # ['光模块', '算力', '国产芯片']
        'tier1_detail': [],       # [{name, topnum, longtou_code, longtou_tag}, ...]
        'candidates': [],         # 入选的候选股
        'excluded': [],           # 被排除的（只保留前10）
        'filter_summary': {},
        'verdict': 'N/A',
    }

    if not jiuyang:
        result['verdict'] = '无jiuyang数据，跳过'
        return result

    mysql_stocks = mysql_data or {}

    # ===== 过滤非真实题材板块（与step2保持一致）=====
    GARBAGE_PLATES = {'其他', '公告', 'ST板块', '新股'}

    # ===== 建立 jiuyang plate_name -> plate_doc 映射（消除 O(n²) 遍历）=====
    # P0-⑤修复：jiuyang 实际字段是 'name'（不是 'plate_name'）
    jiuyang_name_map: Dict[str, Dict] = {p.get('name', '').strip(): p for p in jiuyang if p.get('name', '').strip()}

    # ===== 建立 lianban code -> info 映射 =====
    lianban_code_map: Dict[str, Dict] = {}  # code -> {tag, name, day_top, reason, top_time}
    for grp in lianban.get('lianban_list') or lianban.get('stock_list') or []:
        tag = grp.get('tag', '')
        grp_name = grp.get('name', '')
        for s in grp.get('list', []):
            raw = s.get('stock_code', '').replace('sz', '').replace('sh', '').replace('SZ', '').replace('SH', '').strip()
            if not raw:
                continue
            lianban_code_map[raw] = {
                'tag': tag,
                'name': s.get('stock_name', grp_name),  # 优先用真实股票名，没有才用分组名
                'stock_name': s.get('stock_name', ''),
                'day_top': s.get('stock_day_top', 'N/A'),
                'reason': s.get('top_reason', 'N/A'),
                'top_time': s.get('top_time_text', 'N/A'),
            }

    def _ban_height_tag(tag: str) -> int:
        """从 tag 提取数字高度（健壮版），无法解析返回 -1（排最后）"""
        import re
        if not tag:
            return -1
        match = re.search(r'\d+', tag)
        return int(match.group()) if match else -1

    def ban_height(code: str) -> int:
        """从 tag 计算连板高度"""
        tag = lianban_code_map.get(code, {}).get('tag', '')
        return _ban_height_tag(tag)

    def in_lianban(code: str) -> bool:
        return code in lianban_code_map

    def _parse_ban_height(tag: str) -> int:
        """辅助函数：解析连板高度（用于综合评分），统一用 regex 版"""
        return _ban_height_tag(tag)

    def find_mysql_key(code: str):
        """MySQL key 匹配（兼容 sz000720 / 000720 / SZ000720 等格式）

        P2-④修复：jiuyang代码格式是 sz000720（已观察到的实际格式），
        MySQL keys 是 000720.SZ/600488.SH，需要统一去前缀再匹配。
        """
        # P2-④+P3修复：统一去大小写前缀和后缀（sz/SZ/sh/SH/.SZ/.SH）
        clean = code.replace('sz', '').replace('sh', '').replace('SZ', '').replace('SH', '').replace('.SZ','').replace('.SH','').strip()
        for suffix in ['.SZ', '.SH']:
            key = f'{clean}{suffix}'
            if key in mysql_stocks:
                return key
        return None

    # ===== 第一步：找 tier1 板块（jiuyang 里 topnum >= 5，过滤垃圾板块）=====
    # topnum = 该 jiuyang 板块中有多少只股在 lianban 里
    tier1_detail = []
    for plate in jiuyang:
        # P0-⑤修复：jiuyang 实际字段是 'name'（不是 'plate_name'）
        pname = plate.get('name', '').strip()
        if not pname:
            continue

        # P0修复：过滤非真实题材板块
        if pname in GARBAGE_PLATES:
            continue

        # P0-⑤修复：jiuyang 实际字段是 'name'（不是 'plate_name'）
        # P0-⑥修复：plate_topnum 不存在于 jiuyang 数据，需动态计算 in_lb_codes 长度
        stocks = plate.get('list', [])
        if not stocks:
            # list为空时用 longtou 撑候选池（仅用于后续 MySQL 匹配）
            lt = plate.get('longtou', {})
            stocks = [{'code': lt.get('stock_code', '')}] if lt else []
        if not stocks:
            continue

        # 该板块在 lianban 里的股票（仅在 list 非空时有意义；longtou fallback 时为空列表）
        in_lb_codes = []
        if stocks and stocks[0].get('code'):
            in_lb_codes = [
                s['code'].replace('sz', '').replace('sh', '').replace('SZ', '').replace('SH', '').strip()
                for s in stocks
                if s.get('code') and (s['code'].replace('sz', '').replace('sh', '').replace('SZ', '').replace('SH', '').strip() in lianban_code_map)
            ]

        # P0-⑥：用动态计算的 in_lb_codes 长度判断是否满足 tier1（≥5只连板股）
        # 不再依赖 plate_topnum（该字段在 jiuyang 数据中不存在）
        if len(in_lb_codes) < 5:
            continue

        # 龙头：lianban 里连板高度最高的；list 为空时直接用 longtou
        if in_lb_codes:
            top_code = max(in_lb_codes, key=ban_height)
        else:
            lt = plate.get('longtou', {})
            top_code = lt.get('stock_code', '').replace('sz','').replace('sh','').strip()
        top_tag = lianban_code_map.get(top_code, {}).get('tag', '')
        top_name = lianban_code_map.get(top_code, {}).get('name', '') or plate.get('longtou', {}).get('stock_name', '')

        tier1_detail.append({
            'name': pname,
            'topnum': len(in_lb_codes),
            'longtou_code': top_code,
            'longtou_tag': top_tag,
            'longtou_name': top_name,
            'in_lianban_codes': in_lb_codes,
        })

    # 按 topnum 降序
    tier1_detail.sort(key=lambda x: -x['topnum'])
    result['tier1_plates'] = [x['name'] for x in tier1_detail]
    result['tier1_detail'] = tier1_detail

    # ===== 第二步：收集候选股 =====
    # 候选股 = 所有 tier1 jiuyang 板块的成分股（去重）
    all_candidates: Dict[str, Dict] = {}  # code -> candidate_info

    for tier1 in tier1_detail:
        pname = tier1['name']

        # 找该 jiuyang 板块（O(1) 查表替代遍历）
        plate_doc = jiuyang_name_map.get(pname)
        if not plate_doc:
            continue

        # 收集成分股列表
        stock_list = plate_doc.get('list', [])
        # 防御：list为空时降级用longtou单只（某些MongoDB数据只有longtou无完整成分股）
        if not stock_list:
            lt = plate_doc.get('longtou', {})
            if lt:
                stock_list = [{
                    'code': lt.get('stock_code', ''),
                    'name': lt.get('stock_name', ''),
                    'expound': lt.get('top_reason', ''),
                }]
            else:
                continue  # 既无list也无longtou，跳过

        for s in stock_list:
            raw = s.get('code', '').replace('sz', '').replace('sh', '').replace('SZ', '').replace('SH', '').strip()
            if not raw or raw in all_candidates:
                continue

            is_lb = in_lianban(raw)
            lb_info = lianban_code_map.get(raw, {})

            all_candidates[raw] = {
                'code': raw,
                'raw_code': s.get('code', ''),
                'name': lb_info.get('name', s.get('name', 'N/A')),
                'plate': pname,
                'reason': lb_info.get('reason', s.get('expound', 'N/A'))[:80],
                'top_time': lb_info.get('top_time', 'N/A'),
                'ban_tag': lb_info.get('tag', ''),
                'day_top': lb_info.get('day_top', 'N/A'),
                'is_lianban': is_lb,
                'is_longtou': False,
                'plate_weight': tier1['topnum'],
            }

    # 标记龙头（ban >= 4）
    for code, info in all_candidates.items():
        if info['is_lianban']:
            try:
                bh = int(info['ban_tag'].replace('ban', '').replace('b', ''))
                if bh >= 4:
                    info['is_longtou'] = True
            except (ValueError, AttributeError):
                pass

    # ===== 第三步：筛选 + 分钟形态检查 =====
    HARD_CAP = 60

    candidates_out = []
    for code, s in all_candidates.items():
        exclude = False
        exclude_reason = ''

        # 1. 龙头（ban >= 4）不追
        if s.get('is_longtou', False):
            exclude = True
            exclude_reason = f"龙头{s.get('ban_tag')}太高，不参与"

        # 2. MySQL 无分钟数据排除
        mysql_key = find_mysql_key(code)
        if not mysql_key:
            exclude = True
            exclude_reason = 'MySQL无分钟数据'

        # 3. 分钟形态检查 + 涨停强度计算
        form = None
        form_warn = None
        zts = {}   # _compute_zhangting_strength 结果
        if not exclude and mysql_key:
            mins = mysql_stocks[mysql_key]
            form = _check_minute_pattern(mins)
            # prev_close（昨收）从分钟数据的 base_price 字段读取，传给 _compute_zhangting_strength
            prev_close = float(mins[0].get('base_price', 0)) if mins else 0.0
            zts = _compute_zhangting_strength(mins, code, prev_close)
            # 形态不健康标记（不排除，仅标记，供step6参考）
            if form and form['form'] in ('一字板', '尾盘砸盘'):
                form_warn = form['form']

        candidates_out.append({
            'code': code,
            'name': s['name'],
            'plate': s['plate'],
            'ban_tag': s['ban_tag'] or 'N/A',
            'day_top': s['day_top'],
            'reason': s['reason'][:60],
            'is_lianban': s['is_lianban'],
            'is_longtou': s['is_longtou'],
            'exclude': exclude,
            'exclude_reason': exclude_reason,
            'minute_pattern': form,
            'form_warn': form_warn,   # '一字板'/'尾盘砸盘'/None
            'plate_weight': s['plate_weight'],
            # 涨停强度因子（来自 _compute_zhangting_strength）
            'strength': zts.get('score', 0) or 0,
            'B4_smoothness': zts.get('B4_smoothness', 0) or 0,
            'B4_scheme': zts.get('B4_scheme', 'none'),
            'f30_vol_pct': form.get('f30_vol_pct', 0) if form else 0,
            'is_on_limit': zts.get('is_on_limit', False),
            'form_score': 0,  # 留空，由 step6 综合评分填充
            # 完整涨停强度因子（供 step6/可视化使用）
            '_zts': zts,
        })

    included = [c for c in candidates_out if not c['exclude']]
    excluded = [c for c in candidates_out if c['exclude']]

    # ===== 第四步：HARD_CAP 截断（综合评分排序）=====
    # 审计修复：原 tuple 排序第二维 not is_on_board 逻辑反向，
    # 导致非在板跟风股反而排在在板跟风股前面，违背"优先启动个股"原则。
    # 新方案：综合评分，形态 + 板高 + 板块权重 + 启动信号 + 量能质量
    FORM_SCORES = {
        '早盘脉冲': 3.0,
        '早盘拉升': 2.0,
        '正常波动': 0.5,
        '尾盘拉升': -1.0,
        '尾盘砸盘': -3.0,
        '一字板': -5.0,
    }

    def _composite_score(c: Dict) -> float:
        score = 0.0
        # 连板高度（高层晋级成功是强势信号）
        bh = _parse_ban_height(c.get('ban_tag', 'ban0'))
        score += bh * 2.0
        # 板块强度
        score += c.get('plate_weight', 0) * 0.5
        # 分钟形态
        pattern = c.get('minute_pattern') or {}
        form = pattern.get('form', '')
        score += FORM_SCORES.get(form, 0.0)
        # 在板跟风信号（已启动但非龙头）
        if c.get('is_lianban') and not c.get('is_longtou'):
            score += 1.5
        # 前30分钟量能质量（40%-80%为健康放量区间）
        f30 = pattern.get('f30', 0) or 0
        if 40 < f30 < 80:
            score += 1.0
        return score

    # P4修复：始终按综合评分排序，无论是否超过HARD_CAP
    # 旧逻辑只在 len>HARD_CAP 时排序 → len≤60 时候选顺序不稳定
    # 新逻辑：先排序，再截断
    included.sort(key=_composite_score, reverse=True)

    if len(included) > HARD_CAP:
        excluded_overflow = included[HARD_CAP:]
        included = included[:HARD_CAP]
        for c in excluded_overflow:
            c['exclude'] = True
            c['exclude_reason'] = f'候选超过{HARD_CAP}只上限，按综合分截断'
        excluded.extend(excluded_overflow)

    result['candidates'] = included
    result['excluded'] = excluded[:10]
    result['filter_summary'] = {
        'total': len(candidates_out),
        'included': len(included),
        'excluded': len(excluded),
        'reason_counts': {
            k: sum(1 for c in excluded if c['exclude_reason'] == k)
            for k in set(c['exclude_reason'] for c in excluded)
        },
    }

    if included:
        result['verdict'] = f"找到{len(included)}只候选标的，可结合分钟形态进一步筛选"
    else:
        result['verdict'] = "无符合条件候选标的，主线龙头位置过高，建议观望"

    # ── ABC因子批量写入 MongoDB（改善1-修正）───────────────────────────
    # 遍历全部 MySQL 股票（不只是候选股），只要触板就写入 MongoDB。
    # candidates_out 只是 jiuyang 题材成分股，不等于"全部涨停股"。
    # lianban_code_map 包含当日全部连板股信息（用于判断 ST 等属性）。
    if ds is not None and date and mysql_stocks:
        saved = 0
        skipped_no_mysql = 0
        for mysql_key, mins in mysql_stocks.items():
            if len(mins) < 60:
                continue

            # 从 mysql_key 反推纯数字 code（如 '603095.SH' → '603095', '920011.BJ' → '920011'）
            clean = mysql_key.replace('.SZ', '').replace('.SH', '').replace('.BJ', '').strip()

            # 判断是否 ST（ST股涨跌停比例不同）
            lb_info = lianban_code_map.get(clean, {})

            # 昨收价从分钟数据 base_price 读取
            prev_close = float(mins[0].get('base_price', 0)) if mins else 0.0
            if prev_close <= 0:
                prev_close = float(mins[0].get('price', 0)) if mins else 0.0

            # 计算 ABC 因子（可能已有内存结果则复用，减少重复计算）
            existing_zts = None
            for c in candidates_out:
                if c.get('code') == clean and c.get('_zts', {}).get('score', 0) > 0:
                    existing_zts = c['_zts']
                    break

            if existing_zts:
                zts = existing_zts
            else:
                zts = _compute_zhangting_strength(mins, clean, prev_close)

            # 只写入确实触板的（score > 0），避免噪音
            if zts and zts.get('score', 0) > 0:
                try:
                    ds.save_zhangting_strength(
                        date=date,
                        stock_code=clean,
                        stock_name=ds.get_stock_name_map().get(clean, '') or mysql_key,  # tushare 5000只全覆盖
                        zts=zts,
                    )
                    saved += 1
                except Exception:
                    pass
            else:
                skipped_no_mysql += 1

        result['_mongo_save'] = {
            'zhangting_strength_saved': saved,
            'mysql_total_stocks': len(mysql_stocks),
            'skipped_no_limit': skipped_no_mysql,
        }

    return result


def _check_minute_pattern(mins: List[Dict]) -> Dict[str, Any]:
    if not mins or len(mins) < 60:
        return None

    prices  = [float(m['price']) for m in mins]
    volumes = [int(m['volume']) for m in mins]
    if not prices or not volumes:
        return None

    open_price  = prices[0]
    close_price = prices[-1]
    max_price  = max(prices)
    min_price  = min(prices)
    vol_sum    = sum(volumes) or 1

    # amp 用 base_price（昨收）做分母，避免高开/低开时振幅失真
    raw_base = mins[0].get('base_price')
    base_price = float(raw_base) if raw_base is not None else open_price
    if base_price == 0:
        base_price = open_price

    amp = (max_price - min_price) / base_price * 100
    f30 = sum(volumes[:30]) / vol_sum * 100
    # q4 尾盘涨跌幅：最后1分钟相对倒数第60分钟的变化率
    # P5注：假设 prices 正好 241 条（9:30~15:00 连续）。若中间停牌导致数据缺失，
    #       索引 -60 不再对应"距离开盘60分钟前"的物理时刻，结果失真。须确保数据连续性。
    q4  = (prices[-1] - prices[-60]) / prices[-60] * 100 if len(prices) >= 60 and prices[-60] > 0 else 0

    if amp < 0.5:
        form = '一字板'
    elif f30 > 85 and amp < 3:
        form = '早盘脉冲'
    elif f30 > 70:
        form = '早盘拉升'
    elif q4 > 1:
        form = '尾盘拉升'
    elif q4 < -1:
        form = '尾盘砸盘'
    else:
        form = '正常波动'

    return {
        'open_px': round(open_price, 2),
        'high_px': round(max_price, 2),
        'low_px': round(min_price, 2),
        'close_px': round(close_price, 2),
        'base_price': round(base_price, 2),
        'amp': round(amp, 2),
        'f30': round(f30, 1),
        'q4': round(q4, 2),
        'f30_vol_pct': round(f30, 1),   # 前30分钟成交量占比（q1_vol_pct为旧名，已废弃）
        'q4_vol_pct': round(_q4_vol_pct(volumes) if len(volumes) >= 240 else 15.0, 1),
        'form': form,
    }


def _q4_vol_pct(volumes: list) -> float:
    n = len(volumes)
    if n < 240 or sum(volumes) == 0:
        return 15.0
    q4_vol = sum(volumes[210:min(241, n)])
    return q4_vol / sum(volumes) * 100


# =============================================================================
# 个股涨停强度因子（A1~A4 时间结构 / B1~B4 价格形态 / C1~C3 量能结构）
# =============================================================================
# MySQL 分钟数据字段：price_time, price, volume, amount, base_price
# 注意：无买一档数据，C1 封成比需用成交量占比替代


def _compute_zhangting_strength(
    mins: List[Dict],
    code: str,
    prev_close: float,
) -> Dict[str, Any]:
    """
    计算个股涨停强度因子。

    参数：
        mins       — MySQL 分钟数据列表（241条，9:30~15:00）
        code       — 股票代码，纯数字如 '000062'
        prev_close — 昨收价（base_price）

    返回：{
        limit_up:   涨停价（精确到分）
        A1_first_hit_min:   首次触板时间（距9:30的分钟数，-1表示未触板）
        A2_total_seal_min:  全天封板总时长（分钟）
        A3_zhaban_cnt:      炸板次数
        A4_max_open_min:    单次最长开板时长（分钟）
        B1_open_chg:        开盘涨幅（%）
        B2_amp:             日内振幅（%，用base_price作分母）
        B3_post_limit_vwap: 涨停后成交量加权均价（relative to limit_up）
        B4_smoothness:      涨停前分时线平滑度
        C1_seal_pct:       封板量/全天量（%，越高封板越实）
        C2_limit_ratio:    触板瞬间量比（触板量/触板前均量，倍）
        C3_pre_touch_pct: 触板前量/全天量（%，越高主力提前建仓越充分）
        score:               涨停强度综合分（0~100）
    }
    """
    if not mins or len(mins) < 60:
        return {}

    # ---------- 基础数据 ----------
    prices      = [float(m['price']) for m in mins]
    volumes     = [int(m['volume']) for m in mins]
    if not prices or not volumes:
        return {}
    amounts     = [float(m.get('amount', 0)) for m in mins]
    base_price  = float(mins[0].get('base_price', prices[0])) if mins[0].get('base_price') else float(prices[0])
    if base_price == 0:
        base_price = float(prices[0])
    open_price  = prices[0]
    close_price = prices[-1]
    vol_sum     = sum(volumes) or 1
    n           = len(mins)

    # ---------- 涨停价（精确四舍五入）----------
    limit_up = calculate_limit_price(base_price, code)

    # ---------- 辅助：判断某分钟是否在涨停板上 ----------
    def is_on_limit(idx: int) -> bool:
        # 精确相等（允许 < 0.005 元浮点误差）
        return abs(prices[idx] - limit_up) < 0.005

    # =========================================================================
    # A. 时间结构
    # =========================================================================

    # A1. 首次触板时间（分钟数，-1表示未触板）
    first_hit = -1
    for i, p in enumerate(prices):
        # 精确相等（允许 < 0.005 元浮点误差）
        if abs(p - limit_up) < 0.005:
            first_hit = i
            break

    # A2. 全天封板总时长（分钟数，只统计触板后）
    total_seal = 0
    if first_hit >= 0:
        for i in range(first_hit, n):
            if is_on_limit(i):
                total_seal += 1

    # A3. 炸板次数（触板后从涨停价跌下来的次数）
    # A4. 单次最长开板时长（与 A3 共用一次扫描，避免重复遍历）
    zhaban_cnt = 0
    in_seal = False
    max_open = 0
    cur_open = 0
    if first_hit >= 0:
        for i in range(first_hit, n):
            if is_on_limit(i):
                if not in_seal:
                    in_seal = True        # 从开板→封板，重置开板计数器
                    if cur_open > 0:
                        max_open = max(max_open, cur_open)
                        cur_open = 0
            else:
                if in_seal:
                    in_seal = False       # 从封板→开板 = 炸板一次
                    zhaban_cnt += 1
                cur_open += 1
        if cur_open > 0:
            max_open = max(max_open, cur_open)

    # =========================================================================
    # B. 价格形态
    # =========================================================================

    # B1. 开盘涨幅（%）
    open_chg = (open_price - base_price) / base_price * 100

    # B2. 日内振幅（%，用base_price作分母）
    max_price = max(prices)
    min_price = min(prices)
    amp = (max_price - min_price) / base_price * 100

    # B3. 涨停后均价偏离（统计触板后所有成交，不限涨停价）
    #     衡量标准：触板后均价偏离涨停价越多 → 资金承接越弱
    #     若从未触板（first_hit=-1），无法计算，返回 None
    #     P0-2修复：旧版只统计涨停价上的成交 → 均价必然=涨停价，relative_vwap永远=0
    #               新版统计触板后所有分钟（含开板期间），开板低价成交拉低均价，才有区分度
    post_limit_amounts = []
    post_limit_volumes = []
    for i in range(first_hit if first_hit >= 0 else n, n):
        post_limit_amounts.append(float(amounts[i]))
        post_limit_volumes.append(int(volumes[i]))
    post_limit_vol_sum = sum(post_limit_volumes) or 1
    post_limit_amt_sum = sum(post_limit_amounts)
    if post_limit_vol_sum <= 1:
        # 从未触板，均价无法计算
        post_limit_vwap = None
        relative_vwap = None
    else:
        post_limit_vwap = post_limit_amt_sum / post_limit_vol_sum
        # relative_vwap：均价相对涨停价的偏离，越小越好（接近涨停=强）
        relative_vwap = (limit_up - post_limit_vwap) / limit_up * 100

    # B4. 涨停前分时线平滑度（V2：三方案组合）
    #     解决旧版R²的"尾盘squeeze误判"问题
    #     方案① 加速斜率比（前半段vs后半段slope比）→ 识别"越涨越快"
    #     方案② 量能递增比（后半段量/前半段量）  → 识别资金持续涌入
    #     方案③ 尾盘集中度（最后30分钟量/涨停前总量）→ 识别尾盘squeeze
    #     按first_hit分场景：
    #       first_hit <= 60  → 方案① 为主（早盘中盘型）
    #       60 < first_hit <= 180 → 方案① + 方案② 加权
    #       first_hit > 180  → 方案② + 方案③（尾盘squeeze型）
    smoothness = 0.0
    max_dd = 0.0
    b4_accel_score = 0.0
    b4_vol_score = 0.0
    b4_squeeze_score = 0.0
    b4_accel_ratio = None
    b4_vol_ratio = None
    b4_squeeze_ratio = None
    b4_scheme = 'none'

    if first_hit == 0:
        # 开盘即封板，无涨停前分时数据，视为最强信号 → 满分
        smoothness = 100.0
        max_dd = 0.0
        b4_scheme = 'instant'
    elif first_hit > 0:
        pre_prices = prices[:first_hit]
        pre_volumes = volumes[:first_hit]
        n_pre = len(pre_prices)
        if n_pre >= 5:
            # ── 方案①：加速斜率比 ─────────────────────────────────────────
            # 将涨停前分成前后两半，比较斜率变化
            half = n_pre // 2
            x1 = list(range(half))
            y1 = pre_prices[:half]
            x2 = list(range(half, n_pre))
            y2 = pre_prices[half:]
            x1_mean = sum(x1) / len(x1)
            y1_mean = sum(y1) / len(y1)
            num1 = sum((xi - x1_mean) * (yi - y1_mean) for xi, yi in zip(x1, y1))
            den1 = sum((xi - x1_mean) ** 2 for xi in x1)
            slope1 = num1 / den1 if den1 != 0 else 0

            x2_mean = sum(x2) / len(x2)
            y2_mean = sum(y2) / len(y2)
            num2 = sum((xi - x2_mean) * (yi - y2_mean) for xi, yi in zip(x2, y2))
            den2 = sum((xi - x2_mean) ** 2 for xi in x2)
            slope2 = num2 / den2 if den2 != 0 else 0

            # 斜率归一化（转换为"相对价格均值的% per minute"），避免量纲干扰
            y1_mean_safe = y1_mean if y1_mean != 0 else 0.001
            y2_mean_safe = y2_mean if y2_mean != 0 else 0.001
            norm_s1 = slope1 / y1_mean_safe * 100  # %/min
            norm_s2 = slope2 / y2_mean_safe * 100

            if abs(norm_s1) < 1e-9 and abs(norm_s2) < 1e-9:
                # 前后斜率均≈0（横盘），给中等分
                b4_accel_score = 50.0
                b4_accel_ratio = 1.0
            elif abs(norm_s1) < 1e-9:
                # 前半段横盘，后半段有方向 → 有方向的一段说明有趋势
                b4_accel_score = 60.0
                b4_accel_ratio = abs(norm_s2)  # 与横盘基线比，无溢出风险
            elif norm_s2 > 0 and norm_s1 <= 0:
                # 前半段下跌/横盘 → 后半段上涨 = 最强加速信号
                b4_accel_score = 100.0
                b4_accel_ratio = abs(norm_s2) / abs(norm_s1) if norm_s1 != 0 else 999.0
            elif norm_s1 > 0 and norm_s2 > 0:
                # 前后均上涨：加速比
                # ratio = norm_s2 / norm_s1
                #   ratio=0.5 → 减速(前半涨得比后半快) → 低分 0-40
                #   ratio=1.0 → 匀速 → 40分
                #   ratio=2.0 → 加速翻倍 → 100分
                ratio = norm_s2 / norm_s1
                b4_accel_ratio = ratio
                b4_accel_score = max(0, min((ratio - 0.5) / 1.5 * 100, 100))
            else:
                # norm_s1 > 0, norm_s2 <= 0：后半段减速（正→负，或正→零）
                ratio = abs(norm_s2) / abs(norm_s1) if norm_s1 != 0 else 0.0
                b4_accel_ratio = ratio
                b4_accel_score = max(0, 100 - ratio * 60)

            # ── 方案②：量能递增比（后半段量/前半段量）────────────────────
            vol1 = sum(pre_volumes[:half])
            vol2 = sum(pre_volumes[half:])
            b4_vol_ratio = vol2 / vol1 if vol1 > 0 else 0.0
            # vol_ratio ≥ 1.2 = 后半段量比前半段多20%以上 = 资金持续涌入 → 100分
            # vol_ratio < 1.2 时，越低说明后半段量能相对萎缩 → 递减扣分
            b4_vol_score = min(b4_vol_ratio / 1.2 * 100, 100)

            # ── 方案③：尾盘集中度（最后30分钟量/涨停前总量）────────────────
            # squeeze_ratio 衡量"尾盘量占涨停前总量的比例"
            #   ratio < 0.2 → 尾盘温和 → mid场景下健康(高分)，squeeze场景下无squeeze特征(低分)
            #   ratio > 0.2 → 尾盘爆量 → mid场景下不健康(低分)，squeeze场景下是squeeze特征(高分)
            tail = min(30, n_pre)
            tail_vol = sum(pre_volumes[-tail:])
            pre_total_vol = sum(pre_volumes)
            b4_squeeze_ratio = min(tail_vol / pre_total_vol if pre_total_vol > 0 else 0.0, 1.0)
            # mid scheme：低 ratio = 健康，高 ratio = 尾盘诱多
            if b4_squeeze_ratio <= 0.2:
                b4_squeeze_score = min(b4_squeeze_ratio / 0.2 * 100, 100)  # ratio=0→0分，0.2→100分
            else:
                b4_squeeze_score = max(0, 100 - (b4_squeeze_ratio - 0.2) / 0.8 * 100)  # 0.2→100分，1.0→0分
            # squeeze scheme：高 ratio = 真正的squeeze特征，应该高分
            if b4_squeeze_ratio >= 0.5:
                b4_squeeze_score_for_squeeze = 100.0  # 50%以上量在尾盘 = 典型squeeze
            elif b4_squeeze_ratio >= 0.2:
                b4_squeeze_score_for_squeeze = (b4_squeeze_ratio - 0.2) / 0.3 * 100  # 0.2→0分，0.5→100分
            else:
                b4_squeeze_score_for_squeeze = 0.0  # 尾盘没放量，不是squeeze型

            # ── 最大回撤（保留用于诊断，不参与B4评分）─────────────────────
            peak = pre_prices[0]
            for v in pre_prices:
                if v > peak:
                    peak = v
                dd = (peak - v) / peak * 100
                if dd > max_dd:
                    max_dd = dd

            # ── 三方案组合 ────────────────────────────────────────────────
            if first_hit <= 60:
                # 早中盘封板 → 方案①为主（60%），方案②为辅（40%）
                b4_scheme = 'early'
                # scheme2_valid：前半段至少有5分钟有效数据（确保量能递增比有统计意义）
                scheme2_valid = half >= 5
                if scheme2_valid:
                    smoothness = b4_accel_score * 0.60 + b4_vol_score * 0.40
                else:
                    smoothness = b4_accel_score  # 只有方案①有效时，100%权重
            elif first_hit <= 180:
                # 中后盘封板 → 方案①（40%）+ 方案②（40%）+ 方案③（20%）
                b4_scheme = 'mid'
                scheme2_valid = half >= 5
                scheme3_valid = tail >= 5
                w2 = 0.40 if scheme2_valid else 0.0
                w3 = 0.20 if scheme3_valid else 0.0
                w1 = 1.0 - w2 - w3
                smoothness = (b4_accel_score * w1 +
                              b4_vol_score * w2 +
                              b4_squeeze_score * w3)
            else:
                # 尾盘squeeze型（>180分钟）→ 方案②（35%）+ 方案③（65%）
                # 尾盘爆量正是squeeze型特征，用 b4_squeeze_score_for_squeeze
                b4_scheme = 'squeeze'
                scheme2_valid = half >= 5
                scheme3_valid = tail >= 5
                smoothness = (b4_vol_score * (0.35 if scheme2_valid else 0.0) +
                             b4_squeeze_score_for_squeeze * (0.65 if scheme3_valid else 0.0))
                if not scheme2_valid and not scheme3_valid:
                    smoothness = 40.0  # 兜底最低分
        else:
            # n_pre < 5：涨停前数据不足以可靠计算三方案组合
            # n_pre=1-4（如fh=1,2,3,4）→ 秒封板，数据很少但分时本身已反映质量
            # → 给中等分，不因数据不足而全罚
            if n_pre <= 4:
                smoothness = 80.0
                b4_scheme = 'near_instant'
            else:
                smoothness = 40.0
                b4_scheme = 'insufficient'

    # =========================================================================
    # C. 量能结构
    #
    # 数学冗余分析（2026-04-28）：
    #   C1(旧) = seal_vol / post_limit_vol
    #   C2(旧) = limit_ratio = hit_vol / pre_avg（独立）
    #   C3(旧) = post_limit_vol / vol_sum
    #   发现：C1(旧) = C3(旧) / C2(旧) — 三者不独立，C1是冗余派生量
    #
    # 新三因子（独立）：
    #   C1 = seal_vol / vol_sum       封板量/全天量（越高封板越实）
    #   C2 = limit_ratio = hit_vol/pre_avg  触板瞬间量比（越高封板越强）
    #   C3 = pre_vol / vol_sum        触板前量/全天量（越高主力提前建仓越充分）
    # =========================================================================

    post_limit_vol = sum(volumes[first_hit:]) if first_hit >= 0 else 0
    post_limit_vol = max(post_limit_vol, 1)
    pre_vol = sum(volumes[:first_hit]) if first_hit >= 0 else 0
    seal_vol = sum(v for i, v in enumerate(volumes) if i >= first_hit and is_on_limit(i)) if first_hit >= 0 else 0

    # C1. 封板量/全天量（%）：全天成交中有多少发生在板上；越高越实
    c1_seal_pct = seal_vol / vol_sum * 100

    # C2. 触板瞬间量比：触板那一分钟的量 / 触板前平均每分钟量
    limit_ratio = 0.0
    if first_hit >= 0:
        hit_vol = volumes[first_hit]
        pre_avg = sum(volumes[:first_hit]) / first_hit if first_hit > 0 else hit_vol
        limit_ratio = hit_vol / pre_avg if pre_avg > 0 else 0.0

    # C3. 触板前量/全天量（%）：主力在触板前建仓的程度；>60%满分，<30%扣光
    c3_pre_touch_pct = pre_vol / vol_sum * 100

    # =========================================================================
    # 综合评分（0~100）
    # =========================================================================
    score = 0.0

    # A1：首封时间，越早越好（反比例：0分钟→100分，241分钟→0分）
    # 公式：A1 = max(0, 100 - 100/241 × first_hit)
    a1_score = max(0, round(100 - 100 / 241 * first_hit, 1)) if first_hit >= 0 else 0

    # A2：封板时长，正比例 100/241 × total_seal
    a2_score = round(100 / 241 * total_seal, 1) if first_hit >= 0 else 0

    # A3：炸板次数，越少越好
    a3_score = max(0, 100 - zhaban_cnt * 30)

    # A4：最大开板时长，越短越好（>30分钟0分）
    a4_score = max(0, 100 - max_open * 3)

    # B1：开盘涨幅，线性比例：-1%=-10分, 5%=50分, 10%=100分
    # 公式：B1 = -10 + (open_chg + 1) × 10，clamp到[-100, 100]
    b1_score = max(-100, min(100, round(-10 + (open_chg + 1) * 10, 1)))

    # B2：振幅，一字板(<0.5%)满分100分，每超1%扣5分
    if amp < 0.5:
        b2_score = 100
    else:
        b2_score = max(0, round(100 - (amp - 0.5) * 5, 1))

    # B3：涨停后均价偏离，越接近涨停价越好（<1%满分，>5%扣光）；未触板→0分
    b3_score = max(0, 100 - relative_vwap * 20) if relative_vwap is not None else 0

    # B4：分时平滑度（0~100）
    b4_score = smoothness

    # C1：封板量/全天量（>50%满分，<20%扣光）
    c1_score = min(c1_seal_pct / 50 * 100, 100) if first_hit >= 0 else 0.0

    # C2：触板瞬间量比，>5倍满分，<1倍扣光
    c2_score = min(limit_ratio / 5 * 100, 100)

    # C3：触板前量/全天量（>60%满分说明主力提前建仓充分，<30%说明是板上吸筹型）
    if first_hit < 0:
        c3_score = 0.0
    else:
        # 60%以上满分，每低1%扣2分
        c3_score = max(0, min(100, (c3_pre_touch_pct - 30) / 30 * 100))

    score = (a1_score * 0.10 + a2_score * 0.08 + a3_score * 0.10 + a4_score * 0.07
             + b1_score * 0.08 + b2_score * 0.08 + b3_score * 0.12 + b4_score * 0.07
             + c1_score * 0.12 + c2_score * 0.10 + c3_score * 0.08)

    return {
        # 涨跌停价
        'limit_up':  limit_up,
        # A 时间结构
        'A1_first_hit_min':   first_hit,
        'A2_total_seal_min':  total_seal,
        'A3_zhaban_cnt':      zhaban_cnt,
        'A4_max_open_min':    max_open,
        # B 价格形态
        'B1_open_chg':        round(open_chg, 2),
        'B2_amp':             round(amp, 2),
        'B3_post_limit_vwap': round(post_limit_vwap, 2) if post_limit_vwap is not None else None,
        'B3_relative_vwap':   round(relative_vwap, 2) if relative_vwap is not None else None,
        'B4_smoothness':      round(smoothness, 1),
        'B4_max_dd':          round(max_dd, 2),
        'B4_scheme':          b4_scheme,
        'B4_accel_ratio':     round(b4_accel_ratio, 4) if b4_accel_ratio is not None else None,
        'B4_vol_ratio':       round(b4_vol_ratio, 4) if b4_vol_ratio is not None else None,
        'B4_squeeze_ratio':   round(b4_squeeze_ratio, 4) if b4_squeeze_ratio is not None else None,
        # C 量能结构
        'C1_seal_pct':      round(c1_seal_pct, 2),       # 封板量/全天量（%）
        'C2_limit_ratio':    round(limit_ratio, 1),        # 触板瞬间量比（倍）
        'C3_pre_touch_pct': round(c3_pre_touch_pct, 2),  # 触板前量/全天量（%）
        # 综合
        'score':              round(score, 1),
        # 收盘是否在涨停价（供 step6 判断"在板跟风"用）
        'is_on_limit':        abs(close_price - limit_up) < 0.005,
        # 分项得分（供调试）
        '_sub_scores': {
            'A1_first_hit': a1_score,
            'A2_seal_dur': a2_score,
            'A3_zhaban':   a3_score,
            'A4_max_open': a4_score,
            'B1_open_chg': b1_score,
            'B2_amp':      b2_score,
            'B3_vwap':     b3_score,
            'B4_smooth':   b4_score,
            'C1_seal':     c1_score,
            'C2_ratio':    c2_score,
            'C3_post_vol': c3_score,
        },
    }
