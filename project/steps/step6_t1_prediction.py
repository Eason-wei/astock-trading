"""
Step 6: T+1 预判矩阵（已重构）
输入：Step1-5的输出
输出：T+1各维度预判 + 置信度 + 仓位建议 + 操作计划
核心升级：
  - MorphologyMatrix: 5条T+1规则代码化（A/C1/D2/F1/板块共振）
  - PositionRules: 各阶段仓位配置
  - ThreeQuestions: 三问定乾坤过滤
  - RiskController: RR计算 + 风控
  - 所有预判带形态分类 + 方向 + 置信度
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from decision import MorphologyMatrix, Morphology, PositionRules, ThreeQuestions, RiskController
from decision.pain_effect_analyzer import run as pain_run, print_report as pain_print


def _infer_trends(step3: dict, step4: dict, step2: dict) -> dict:
    """
    从当日数据推断 ladder 和 main_line 两个时间维度趋势。
    pain_trend 已迁移到 pain_effect_analyzer，不在此计算。
    """
    ladder = step4.get('ladder', {})

    # ladder_trend 推断
    high_ban = sum(1 for tag in ['ban5', 'ban6', 'ban7']
                   if ladder.get(tag, {}).get('cnt', 0) > 0)
    mid_ban = sum(1 for tag in ['ban3', 'ban4']
                  if ladder.get(tag, {}).get('cnt', 0) > 0)
    health = step4.get('health_score', 50)
    if high_ban >= 2 and health >= 60:
        ladder_trend = 'accelerating'
    elif mid_ban >= 2 and health < 40:
        ladder_trend = 'decelerating'
    else:
        ladder_trend = 'stable'

    # main_line_trend 推断（qingxu 来自 step3，step4 没有 qingxu 键）
    qingxu = step3.get('qingxu', '')
    tier1 = step2.get('tier1', [])
    has_main_line = bool(tier1)
    if qingxu == '升温期' and has_main_line:
        main_line_trend = 'emerging'
    elif qingxu == '高潮期' and high_ban >= 1:
        main_line_trend = 'peak'
    elif qingxu == '退潮期' and not has_main_line:
        main_line_trend = 'dying'
    elif qingxu == '降温期' and has_main_line:
        main_line_trend = 'rotating'
    else:
        main_line_trend = 'stable'

    return {
        'ladder': ladder_trend,
        'main_line': main_line_trend,
    }


def run(step1, step2, step3, step4, step5, fupan=None, ds=None, **kwargs) -> dict:
    """
    T+1 预判矩阵（重构版）
    接入 pain_effect_analyzer 替换旧的粗糙亏钱效应推断。
    fupan: MongoDB fupan_data 原始数据（用于 pain_effect_analyzer.run）
    ds: DataSource 实例（用于从 pain_effect_scores 集合构建 history）
    """
    date = step1.get('date', 'N/A')
    qingxu = step3.get('qingxu', 'N/A')
    degree = step3.get('degree_market', 50)

    mm = MorphologyMatrix()
    pr = PositionRules()
    tq = ThreeQuestions()
    rc = RiskController()

    result = {
        'date': date,
        't1_date': _get_next_trade_date(date),
        'qingxu': qingxu,
        'degree': degree,
        'predictions': [],
        'stock_predictions': [],   # 每只候选股的T+1预判
        'position_plan': {},
        'action_items': [],
        'three_questions': {},
    }

    # ===== 1. 整体情绪预判 =====
    emotion_pred = _predict_emotion(qingxu, degree, step1, step4, step2)
    result['predictions'].append(emotion_pred)

    # ===== 2. 三问定乾坤检查 =====
    board_health = step4.get('health_score', 50)
    # P0-①修复：zhuxian在step2和step1来源不同，step2只取top6且要求lianban_num>=5
    # 改用tier1判断（更稳定）：tier1有值=有主线，tier1为空=无主线
    tier1 = step2.get('tier1', [])
    main_line_status = '明确' if tier1 else '无'
    main_line_strength = 0.7 if tier1 else 0.3

    # 建议5：从step4/step2推断ladder和main_line趋势
    trends = _infer_trends(step3, step4, step2)

    # ===== 接入 pain_effect_analyzer：替换旧版粗糙的pain_trend推断 =====
    # 从MongoDB pain_effect_scores构建history，跨日计算趋势
    pain_result = None
    if fupan:
        history = {}
        if ds:
            prev_date = _get_prev_trade_date(date)
            if prev_date:
                past_scores = ds.get_pain_scores('2020-01-01', prev_date)
                history = {s['date']: s['score'] for s in past_scores}
        pain_result = pain_run(fupan, history)
        # analyzer.score: 越高=市场越健康（不是"越痛"）
        # trend=↑上升 = 健康分上升 = 亏钱效应收敛 = pain_trend=improving
        # trend=↓下降 = 健康分下降 = 亏钱效应扩散 = pain_trend=worsening
        if pain_result['trend'].startswith('↑'):
            pain_trend = 'improving'
        elif pain_result['trend'].startswith('↓'):
            pain_trend = 'worsening'
        else:
            pain_trend = 'stable'
    else:
        # 没有fupan数据时，退回旧推断逻辑（兜底）
        pain_trend = 'stable'

    # P0-②接入风控：系统级风险检查（turnover/跌停数/情绪高潮）
    sys_risk = rc.check_system_risk(
        turnover=step1.get('amount', 10000),
        fall_count=step1.get('bottom_num', 0),
        emotion_score=step3.get('degree_market')
    )

    # P1-②修复：兜底公式语义已修正（之前100-degree语义反转但效果合理，改为直接用degree）
    # degree_market本身就反映市场情绪：高=健康，低=危险
    health_score = pain_result['score'] if pain_result else step3.get('degree_market', 50)
    tq_result = tq.check(
        board_health=board_health,
        main_line={'status': main_line_status, 'strength': main_line_strength,
                   'theme': step2.get('tier1', ['未知'])[0] if step2.get('tier1') else '未知'},
        pain_score=health_score,
        top_rate=step3.get('top_rate'),
        dadian_count=step1.get('bottom_num', 0),
        # 建议5：时间维度趋势
        ladder_trend=trends['ladder'],
        main_line_trend=trends['main_line'],
        pain_trend=pain_trend,
    )
    result['three_questions'] = {
        'passed': tq_result.passed,
        'overall_score': tq_result.overall_score,
        'final_verdict': tq_result.final_verdict,
        'warnings': tq_result.warnings,
        'q1': {'passed': tq_result.questions[0].passed, 'score': tq_result.questions[0].score, 'detail': tq_result.questions[0].detail},
        'q2': {'passed': tq_result.questions[1].passed, 'score': tq_result.questions[1].score, 'detail': tq_result.questions[1].detail},
        'q3': {'passed': tq_result.questions[2].passed, 'score': tq_result.questions[2].score, 'detail': tq_result.questions[2].detail},
    }

    # ===== 3. 亏钱效应分析结果（接入 pain_effect_analyzer）=====
    if pain_result:
        result['pain_effect'] = {
            'score': pain_result['score'],
            'level': pain_result['level'],
            'trend': pain_result['trend'],
            'signals': pain_result['signals'],
            'warnings': pain_result['warnings'],
            'action': pain_result['action'],
            'breakdown': pain_result['breakdown'],
            'veto_triggered': pain_result['veto_triggered'],
            'veto_reasons': pain_result['veto_reasons'],
        }

    # ===== 3. 候选股T+1形态预判（核心新增）=====
    # 改善2+3：传入 ds + date，使 _predict_stocks 能查询 MongoDB ABC 因子
    stock_preds = _predict_stocks(mm, step5, qingxu, step2, step4, ds=ds, date=date)
    result['stock_predictions'] = stock_preds

    # P0-②：将系统风控结果注入result（供下游step7/8使用）
    result['system_risk'] = sys_risk

    # ===== 4. 仓位计划 =====
    # P0-②：将sys_risk传入仓位计划，系统风控触发时降低仓位
    position_plan = _build_position_plan(
        step1, step2, step3, step4, step5, pr, tq_result, stock_preds, sys_risk
    )
    result['position_plan'] = position_plan

    # ===== 5. 操作清单 =====
    action_items = _build_action_items(step5, stock_preds, position_plan, qingxu, tq_result)
    result['action_items'] = action_items

    result['predictions'].append({
        'item': '候选股T+1',
        'prediction': f"共{len(stock_preds)}只，平均置信度={_avg_confidence(stock_preds):.0%}",
        'confidence': '—',
        'reason': f"最优:{_best_prediction(stock_preds)}",
    })

    # ===== 预判矩阵总览 =====
    result['matrix'] = _build_matrix(result['predictions'], position_plan, tq_result)

    return result


def _get_next_trade_date(date: str, available_dates: list = None) -> str:
    """
    计算下一个交易日（P1-②修复：不只跳周末，还跳节假日）

    Args:
        date: 当前交易日
        available_dates: 可用日期列表（如从MongoDB lianban_data获取），
                         如果提供，优先使用列表中的下一个日期
    """
    from datetime import datetime, timedelta

    # 方案A：从可用日期列表中查找（利用MongoDB数据作为交易日历）
    if available_dates:
        try:
            idx = available_dates.index(date)
            if idx + 1 < len(available_dates):
                return available_dates[idx + 1]
        except (ValueError, AttributeError):
            pass

    # 方案B：跳周末 + 已知节假日（2026年完整法定节假日）
    # P2-④修复：补全2026全年法定节假日，避免交易日推断错误
    KNOWN_HOLIDAYS = {
        # 元旦
        '2026-01-01',
        # 春节（2月16日-22日，放假2月16-28日，调休3天+周末=13天）
        '2026-02-16', '2026-02-17', '2026-02-18', '2026-02-19', '2026-02-20',
        '2026-02-21', '2026-02-22', '2026-02-23', '2026-02-24', '2026-02-25',
        '2026-02-26', '2026-02-27', '2026-02-28',
        # 清明（4月4日-6日）
        '2026-04-04', '2026-04-05', '2026-04-06',
        # 劳动节（5月1日-3日）
        '2026-05-01', '2026-05-02', '2026-05-03',
        # 端午节（6月20日-22日）
        '2026-06-20', '2026-06-21', '2026-06-22',
        # 中秋节（10月8日-10日）
        '2026-10-08', '2026-10-09', '2026-10-10',
        # 国庆节（10月1日-7日）
        '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04',
        '2026-10-05', '2026-10-06', '2026-10-07',
    }
    d = datetime.strptime(date, '%Y-%m-%d')
    next_d = d + timedelta(days=1)
    while True:
        wd = next_d.weekday()
        date_str = next_d.strftime('%Y-%m-%d')
        # 跳过周末(5=周六,6=周日)和已知节假日
        if wd < 5 and date_str not in KNOWN_HOLIDAYS:
            return date_str
        next_d += timedelta(days=1)


def _get_prev_trade_date(date: str) -> str | None:
    """
    计算上一个交易日（往前找，跳过周末和节假日）。
    """
    from datetime import datetime, timedelta
    KNOWN_HOLIDAYS = {
        '2026-01-01',
        '2026-02-16', '2026-02-17', '2026-02-18', '2026-02-19', '2026-02-20',
        '2026-02-21', '2026-02-22', '2026-02-23', '2026-02-24', '2026-02-25',
        '2026-02-26', '2026-02-27', '2026-02-28',
        '2026-04-04', '2026-04-05', '2026-04-06',
        '2026-05-01', '2026-05-02', '2026-05-03',
        '2026-06-20', '2026-06-21', '2026-06-22',
        '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04',
        '2026-10-05', '2026-10-06', '2026-10-07',
        '2026-10-08', '2026-10-09', '2026-10-10',
    }
    d = datetime.strptime(date, '%Y-%m-%d')
    prev_d = d - timedelta(days=1)
    for _ in range(30):  # 最多回查30天
        wd = prev_d.weekday()
        date_str = prev_d.strftime('%Y-%m-%d')
        if wd < 5 and date_str not in KNOWN_HOLIDAYS:
            return date_str
        prev_d -= timedelta(days=1)
    return None


def _predict_emotion(qingxu: str, degree: int, step1, step4, step2) -> dict:
    """预判T+1情绪"""
    if qingxu == '冰点期':
        pred = "可能延续冰点或快速转修复"
        conf = "中"
        reason = "冰点期情绪可能快速修复，但需新催化剂"
    # P2-⑥修复：zhaban应从step2的strength_eval取（step2从fupan重建），step4的ladder没有zhaban key
    # zhaban = step4.get('ladder', {}).get('zhaban', {}).get('cnt', 0)  # 永远为0的旧代码
    zhaban = step2.get('strength_eval', {}).get('zhaban_cnt', 0)  # 来自fupan_data.open_num
    if qingxu == '高潮期':
        if zhaban >= 10:
            pred = "高潮退潮信号，高位股分批离场"
            conf = "高"
            reason = f"炸板{zhaban}只，高潮退潮特征明显"
        else:
            pred = "高潮延续，但注意随时撤退"
            conf = "中"
            reason = "无明显退潮信号，但高潮期本身是撤退信号"
    elif qingxu == '退潮期':
        pred = "延续退潮，空仓观望"
        conf = "高"
        reason = "退潮期无操作价值"
    elif qingxu == '升温期':
        pred = "情绪继续升温，积极操作"
        conf = "中"
        reason = "升温期是最佳操作窗口"
    else:
        pred = "情绪不明，观望为主"
        conf = "低"
        reason = "无明确定义"

    return {
        'item': '整体情绪',
        'prediction': pred,
        'confidence': conf,
        'reason': reason,
    }


def _predict_stocks(mm: MorphologyMatrix, step5, qingxu: str, step2, step4,
                    ds=None, date: str = None) -> list:
    """
    对候选股进行T+1形态预判（核心函数）
    使用MorphologyMatrix的5条规则

    新增（2026-05-02 改善2+3）：
      - ds 参数：DataSource实例，用于查询 MongoDB zhangting_strength 集合
      - date 参数：交易日期，用于构造 MongoDB 查询条件
      - strength 前置过滤：ABC总分 < 30 → 直接排除（封板质量不过关不参与）
      - ABC因子注入：结果中包含完整11因子，供仓位决策参考
    """
    candidates = step5.get('candidates', [])
    if not candidates:
        return []

    # P1-①修复：conf_floor应基于原始候选数量，不是在循环中动态变化
    total_candidates = len(candidates)

    # 板块共振强度
    tier1 = step2.get('tier1', [])
    sector_strength = 0.8 if tier1 else 0.4

    # 连板高度影响
    ladder = step4.get('ladder', {})
    top_ban = 0
    for tag in ['ban7', 'ban6', 'ban5', 'ban4', 'ban3']:
        if tag in ladder and ladder[tag].get('stocks'):
            top_ban = int(tag.replace('ban', ''))
            break

    stock_preds = []
    for c in candidates:
        code = c.get('code', '')

        # ── 改善2：从 MongoDB 查询涨停强度因子（ABC体系）───────────────
        # 优先用 MongoDB 数据（当日计算结果），其次用 step5 内存数据
        zts = None
        if ds and date and code:
            try:
                zts = ds.get_zhangting_strength(date, code)
            except Exception:
                pass
        if zts is None:
            # MongoDB 无数据时降级用 step5 内存结果（_zts 字段）
            zts = c.get('_zts', {})
        strength = zts.get('score', c.get('strength', 0)) if zts else c.get('strength', 0)

        # ── 改善3：ABC总分前置过滤器（封板质量不过关直接排除）─────────
        # score < 30 → 封板质量差，不参与 T+1 预判
        if strength < 30:
            continue

        mp = c.get('minute_pattern')
        code = c.get('code', '')
        # 从股票名称判断是否ST（用于涨跌停比例计算）
        name = c.get('name', c.get('stock_name', ''))
        is_st = name.startswith(('ST', '*ST', 'S*'))

        # 有分钟数据（字典）→ 用MorphologyMatrix精细预测
        if mp and isinstance(mp, dict):
            features = mm.extract_from_ohlc(
                open_px=mp.get('open_px', 0),
                high_px=mp.get('high_px', 0),
                low_px=mp.get('low_px', 0),
                close_px=mp.get('close_px', 0),
                base_price=mp.get('base_price', mp.get('open_px', 1)),
                q1_vol_pct=mp.get('q1_vol_pct'),
                q4_vol_pct=mp.get('q4_vol_pct'),
                code=code,       # 传入code以正确计算涨跌停价（科创板20%/ST 5%等）
                is_st=is_st,     # 传入is_st以区分ST股的涨跌停比例
            )
            morph = mm.classify(features)
            pred = mm.predict(features, morph, qingxu, sector_strength=sector_strength)
        else:
            # 无完整分钟数据但有形态字符串 → 用MorphologyMatrix.from_string()转换
            # minute_pattern可能是: None / '一字板' / '早盘拉升' 等字符串
            form_str = mp if isinstance(mp, str) else '普通波动'
            morph = mm.string_to_morphology(form_str)
            # Bug2修复：hardcoded dict的key必须与Morphology.value完全一致
            # Morphology.F1.value='F1温和放量'（不是'F1温和放量稳步推进'）
            est_confidence = {
                'F1温和放量': 0.88,  # 注意：Morphology.F1.value = 'F1温和放量'
                'A类一字板': 0.85,
                'B类正常涨停': 0.65,
            }.get(morph.value, 0.45)
            pred = {
                'morphology': morph.value,
                # Bug1修复：config的key是字符串（morph.value），不是Morphology枚举
                't1_direction': mm.config.get(morph.value, {}).get('t1_bias', 'neutral'),
                't1_expected_change': '-2%~+2%',
                'confidence': est_confidence,
                'rule_applied': f'无分钟数据，使用{morph.value}形态规则',
                'warnings': [],
                'sector_boost': 0.0,
                'final_confidence': est_confidence,
            }

        # 连板高度加成（越高越谨慎）
        ban_height = int(c.get('ban_tag', 'ban0').replace('ban', '').replace('b', '')) if c.get('ban_tag', '').startswith('ban') else 0
        if ban_height >= 4:
            pred['warnings'].append(f"连板{ban_height}板，位置过高，谨慎追涨")
            pred['final_confidence'] = max(0.3, pred['final_confidence'] - 0.2)

        # 建议2：候选股数量 → 动态置信度门槛
        # candidates 越多 → 市场越混乱 → 提高门槛过滤噪音
        # P1-①修复：使用循环前计算的total_candidates（原始候选数量），不用stock_preds动态计数
        if total_candidates > 80:
            conf_floor = 0.75   # 高噪音市场，只留高置信度
        elif total_candidates > 60:
            conf_floor = 0.70
        else:
            conf_floor = 0.65   # 正常市场
        pred['conf_floor'] = conf_floor  # 建议2：透传给下游

        # can_enter 精细化判断（综合方向+confidence+阶段+位置）
        direction = pred.get('t1_direction', 'neutral')
        conf = pred.get('final_confidence', 0.5)
        # 连板高度惩罚（4板以上降权，6板以上不给进场）
        ban_penalty = 0.0
        if ban_height >= 6:
            can_enter = False  # 6板以上无论形态都不进场
        elif ban_height >= 4:
            ban_penalty = 0.15
            can_enter = (direction == 'positive' and conf >= conf_floor + 0.05)
        else:
            # 普通连板：positive + confidence门槛（建议2：动态门槛）
            if direction == 'positive':
                can_enter = conf >= conf_floor
            elif direction == 'neutral':
                can_enter = conf >= conf_floor + 0.10  # neutral 要更高confidence才考虑
            else:
                can_enter = False  # negative方向不开仓

        stock_preds.append({
            'code': code,
            'name': c.get('name', ''),
            'plate': c.get('plate', ''),
            'ban_tag': c.get('ban_tag', ''),
            'morphology': morph.value if hasattr(morph, 'value') else str(morph),
            'morphology_name': pred.get('morphology', ''),
            't1_direction': pred.get('t1_direction', 'neutral'),
            't1_expected_change': pred.get('t1_expected_change', 'N/A'),
            'confidence': pred.get('final_confidence', 0.5),
            'rule_applied': pred.get('rule_applied', ''),
            'warnings': pred.get('warnings', []),
            'sector_boost': pred.get('sector_boost', 0.0),
            'can_enter': can_enter,
            # ── 改善2+3：ABC涨停强度因子（来自MongoDB，注入结果）────────
            'strength': strength,
            'B4_smoothness': zts.get('B4_smoothness', c.get('B4_smoothness', 0)) if zts else c.get('B4_smoothness', 0),
            'B4_scheme': zts.get('B4_scheme', c.get('B4_scheme', 'none')) if zts else c.get('B4_scheme', 'none'),
            'is_on_limit': zts.get('is_on_limit', c.get('is_on_limit', False)) if zts else c.get('is_on_limit', False),
            # 11因子分项（供仓位决策参考）
            '_abc': {
                'A1_first_hit_min': zts.get('A1_first_hit_min') if zts else None,
                'A2_total_seal_min': zts.get('A2_total_seal_min') if zts else None,
                'A3_zhaban_cnt': zts.get('A3_zhaban_cnt') if zts else None,
                'A4_max_open_min': zts.get('A4_max_open_min') if zts else None,
                'B1_open_chg': zts.get('B1_open_chg') if zts else None,
                'B2_amp': zts.get('B2_amp') if zts else None,
                'B3_relative_vwap': zts.get('B3_relative_vwap') if zts else None,
                'B4_smoothness': zts.get('B4_smoothness') if zts else None,
                'C1_seal_pct': zts.get('C1_seal_pct') if zts else None,
                'C2_limit_ratio': zts.get('C2_limit_ratio') if zts else None,
                'C3_pre_touch_pct': zts.get('C3_pre_touch_pct') if zts else None,
                '_sub_scores': zts.get('_sub_scores', {}) if zts else {},
            },
        })

    # 按置信度排序
    stock_preds.sort(key=lambda x: x['confidence'], reverse=True)
    return stock_preds


def _build_position_plan(step1, step2, step3, step4, step5, pr: PositionRules, tq_result, stock_preds, sys_risk=None) -> dict:
    """构建仓位计划（使用PositionRules）"""
    base_pos = step3.get('base_position', 0.10)
    health = step4.get('health_score', 50)
    qingxu = step3.get('qingxu', '修复期')
    candidates = step5.get('candidates', [])

    # 用PositionRules精确计算
    lianban_days = _get_top_lianban_days(step4)
    # P2-③修复：sector_strength不再硬编码0.7，与_predict_stocks保持一致
    tier1 = step2.get('tier1', [])
    sector_strength = 0.8 if tier1 else 0.4
    pc = pr.calculate(
        stage=qingxu,
        lianban_days=lianban_days,
        sector_strength=sector_strength,
        is_main_line=bool(step2.get('tier1')),
        emotion_score=step3.get('degree_market'),
    )

    # 三问过滤
    if not tq_result.passed:
        pc.final_position = min(pc.final_position, 0.10)

    # 有候选标的才考虑开仓
    has_candidates = len([p for p in stock_preds if p.get('can_enter')]) > 0

    # 调整说明（先定义，后填充）
    adjustments = []

    final_pos = pc.final_position if has_candidates else 0.0
    # P0-②：系统风控触发时按position_limit降低仓位
    if sys_risk and sys_risk.get('has_risk'):
        position_limit = sys_risk.get('position_limit', 1.0)
        final_pos *= position_limit
        adjustments.append(f"系统风控降权×{position_limit:.0%}")

    if not tq_result.passed:
        adjustments.append('三问未通过')
    if health < 50:
        adjustments.append('梯队不健康')
    if not has_candidates:
        adjustments.append('无合格候选')
    if lianban_days >= 4:
        adjustments.append(f'龙头{lianban_days}板位置过高')
    # P0-②：系统风控触发时记录原因
    if sys_risk and sys_risk.get('has_risk'):
        adjustments.append(f"系统风控:{'; '.join(sys_risk.get('risks', []))}")

    return {
        'base_position': f"{pc.base*100:.0f}%",
        'final_position': f"{final_pos*100:.0f}%",
        'position_raw': final_pos,
        'has_candidates': has_candidates,
        'candidate_count': len([p for p in stock_preds if p.get('can_enter')]),
        'stage_config': {'stage': qingxu, 'note': pc.note},
        'multipliers': {'lianban': pc.lianban_multiplier, 'sector': pc.sector_multiplier},
        'adjustment': ' + '.join(adjustments) if adjustments else '正常仓位',
        'tq_passed': tq_result.passed,
        'tq_score': tq_result.overall_score,
    }


def _get_top_lianban_days(step4) -> int:
    """获取最高连板天数"""
    ladder = step4.get('ladder', {})
    for tag in ['ban7', 'ban6', 'ban5', 'ban4', 'ban3', 'ban2']:
        if tag in ladder and ladder[tag].get('stocks'):
            return int(tag.replace('ban', ''))
    return 0


def _build_action_items(step5, stock_preds, position_plan, qingxu, tq_result) -> list:
    """构建操作清单"""
    items = []
    final_pos = position_plan.get('final_position', '0%')

    if final_pos == '0%' or not tq_result.passed:
        items.append("[禁止] 三问未通过或仓位0%，今日不开仓")

    # 按置信度列出候选
    enter_candidates = [p for p in stock_preds if p.get('can_enter')]
    for p in enter_candidates[:3]:
        rule = p.get('rule_applied', '')[:30]
        items.append(
            f"[关注] {p['name']}({p['plate']}) "
            f"{p['morphology_name']} | "
            f"T+1:{p['t1_direction']} {p['t1_expected_change']} | "
            f"置信度:{p['confidence']:.0%} | {rule}"
        )

    # 高风险警告
    danger = [p for p in stock_preds if p.get('warnings')]
    for p in danger[:2]:
        items.append(f"[警告] {p['name']}: {' '.join(p['warnings'])}")

    # 三问摘要（防御性：questions 可能为空或少于3个）
    qs = tq_result.questions
    if qs and len(qs) >= 3:
        q_str = f"q1={qs[0].score:.0f} q2={qs[1].score:.0f} q3={qs[2].score:.0f}"
    else:
        q_str = "N/A"
    items.append(f"[三问] score={tq_result.overall_score:.0f} verdict={tq_result.final_verdict} {q_str}")

    return items


def _avg_confidence(stock_preds: list) -> float:
    if not stock_preds:
        return 0.0
    return sum(p['confidence'] for p in stock_preds) / len(stock_preds)


def _best_prediction(stock_preds: list) -> str:
    if not stock_preds:
        return 'N/A'
    best = stock_preds[0]
    return f"{best['name']} {best['morphology_name']} {best['confidence']:.0%}"


def _build_matrix(predictions: list, position_plan: dict, tq_result) -> str:
    """构建预判矩阵表格"""
    lines = [
        "\n┌─────────────────────────────────────────────────────────────────────┐",
        "│                     T+1 预判矩阵（重构版）                             │",
        "├─────────────────────────────────────────────────────────────────────┤",
    ]
    for p in predictions:
        lines.append(f"│ {p['item']:<15} │ {p['prediction']:<28} │ 置信:{p['confidence']} │")
    lines.append(f"│ 三问定乾坤     │ score={tq_result.overall_score:.0f} verdict={tq_result.final_verdict:<15} │ — │")
    lines.append(f"│ 仓位计划       │ {position_plan.get('final_position','N/A'):<28} │ {position_plan.get('adjustment','N/A'):<15} │")
    lines.append("└─────────────────────────────────────────────────────────────────────┘")
    return "\n".join(lines)
