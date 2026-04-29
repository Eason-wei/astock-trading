"""Step 7: Verification Loop (Rewritten)Inputs: T+1 actual data + T+1 predictions from step6Outputs: Verification results + calls CognitionUpdater for cognitive loopCore:  - Uses PredictionVerifier for structured scoring  - Uses LessonExtractor to trigger CognitionUpdater  - Writes verification results to cognition system"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from verify import PredictionVerifier, LessonExtractor
from decision import MorphologyMatrix
from decision.accuracy_tracker import AccuracyTracker
import pymysql
from datetime import datetime, timedelta


def run(t1_actual: dict, predictions: list, position_plan: dict, step6_stock_preds: list = None, **kwargs) -> dict:
    """
    Step 7 - Verification Loop (Rewritten)
    
    Key change from old version:
      - OLD: Simple string matching, wrote to nowhere
      - NEW: Uses PredictionVerifier + LessonExtractor + CognitionUpdater
    kwargs:
      - target_date: T日日期（run_real.py传入，用于MySQL价格查询）
      - t1_date: T+1日期（run_real.py传入，t1_actual可能没有date键）
    """
    target_date = kwargs.get('target_date', '')
    t1_date_override = kwargs.get('t1_date', '')
    # T日情绪阶段（用于accuracy_tracker记录）；优先从kwargs传入，否则降级
    market_stage = kwargs.get('market_stage', 'unknown')
    verifier = PredictionVerifier()
    extractor = LessonExtractor()
    mm = MorphologyMatrix()

    result = {
        'date': t1_date_override,           # P3-②fix: 用kwargs传入的t1_date，不再依赖t1_actual.get('date')
        't1_date': t1_date_override,        # 同上
        'predictions': [],
        'stock_verifications': [],
        'score': 0,
        'total': 0,
        'lessons': [],
        'cognition_updates': [],   # NEW: will be written by CognitionUpdater
    }

    fupan = t1_actual.get('fupan', {})
    lianban_t1 = t1_actual.get('lianban', {})

    # ===== 1. Emotion verification =====
    emotion_pred = None
    for p in predictions:
        if p.get('item') == '\u5168\u4f53\u60c5\u7ee9':
            emotion_pred = p
            break

    if emotion_pred and fupan:
        actual_qingxu = fupan.get('qingxu', 'N/A')
        actual_degree = fupan.get('degree_market', 0)

        verdict = _verdict_emotion(emotion_pred['prediction'], actual_qingxu)

        pred_dict = {'direction': _emotion_direction(emotion_pred['prediction']), 'expected': actual_qingxu}
        actual_dict = {'close_pct': actual_degree - 50}  # approximate mapping

        vr = verifier.verify(pred_dict, actual_dict)
        lesson = extractor.extract(
            verify_result=vr,
            market_stage=actual_qingxu,
            stock_name='整体情绪',   # P2-B修复：加标识key，区分个股与情绪验证的认知记录
            prediction={'direction': pred_dict['direction'], 'expected_change': emotion_pred['prediction']},
            data_quality='real',   # V8修复：情绪验证用真实MongoDB数据
        )
        # Bug D/F 修复：emotion lessons 标记 lesson_type，避免在 step8 中被误处理
        if lesson:
            lesson['lesson_type'] = 'emotion'

        result['predictions'].append({
            'item': '\u5168\u4f53\u60c5\u7ee9',
            'predicted': emotion_pred['prediction'],
            'actual': f"{actual_qingxu}(degree={actual_degree})",
            'verdict': verdict,
            'score': vr.score,
            'correct': vr.correct,
        })
        result['total'] += 1
        if vr.correct:
            result['score'] += 1
        if lesson:
            result['lessons'].append(lesson)
            # P1-②修复：从lesson中提取update_result，追加到cognition_updates
            ur = lesson.get('update_result')
            if ur:
                result['cognition_updates'].append(ur)

    # ===== 2. Stock-level T+1 verification (NEW: core loop) =====
    # P2-③修复：t1_date_str在_verify_stocks外部获取并传入，避免作用域问题
    # P2-⑤修复：同时计算base_date_str并传入（MySQL价格查询需要T日日期）
    # P2-⑦修复：优先用t1_date_override（kwargs传入），否则用t1_actual.get('date')
    t1_date_str = t1_date_override or t1_actual.get('date', '')
    base_date_str = target_date  # 从kwargs传入（run_real.py的TARGET_DATE）
    if step6_stock_preds and lianban_t1:
        stock_verifications = _verify_stocks(verifier, extractor, mm, step6_stock_preds, lianban_t1, fupan, t1_date_str, base_date_str, market_stage)
        result['stock_verifications'] = stock_verifications

        # 统计real vs estimated比例
        real_count = sum(1 for sv in stock_verifications if sv.get('data_quality') == 'real')
        result['data_quality'] = {
            'overall': 'mixed',
            'emotion': 'real',
            'stock': f'real({real_count}/{len(stock_verifications)})_mysql_estimated' if real_count < len(stock_verifications) else 'real_mysql',
            'note': f'情绪验证=真实MongoDB；个股={real_count}条MySQL真实价格+{len(stock_verifications)-real_count}条fallback估算'
        }

        for sv in stock_verifications:
            result['total'] += 1
            if sv['correct']:
                result['score'] += 1
            lesson = sv.get('lesson')
            if lesson:
                result['lessons'].append(lesson)
                # P1-②修复：从lesson中提取update_result，追加到cognition_updates
                ur = lesson.get('update_result')
                if ur:
                    result['cognition_updates'].append(ur)

    # ===== 3. Ladder verification (P1修复: long_ban来自fupan，不是lianban) =====
    if lianban_t1 and fupan:
        _verify_ladder(result, lianban_t1, fupan)

    # ===== 4. Market overview =====
    if fupan:
        result['predictions'].append({
            'item': '\u5e02\u573a\u6982\u51b5',
            'predicted': position_plan.get('adjustment', 'N/A'),
            'actual': f"\u6da8\u505c{fupan.get('top_num', 0)}\u53ea \u5c0f\u5e02{fupan.get('same_num', 0)}\u53ea \u5927\u9762{fupan.get('stop_num', 0)}\u53ea",
            'verdict': '\u2014',
        })

    # ===== Score =====
    if result['total'] > 0:
        result['score_rate'] = f"{result['score']}/{result['total']} = {result['score']/result['total']*100:.0f}%"
        result['accuracy'] = round(result['score'] / result['total'], 3)

    # P0-④ 修复：添加顶层 data_quality 字段（文档要求）
    # 情绪验证用真实MongoDB数据，个股验证为估算数据
    result['data_quality'] = {
        'overall': 'mixed',  # step7同时包含real(情绪)和estimated(个股)
        'emotion': 'real',   # 情绪验证来自真实MongoDB fupan_data
        'stock': 'estimated_grade_approx' if result['stock_verifications'] else None,
        'note': '情绪验证为真实数据；个股验证为基于连板状态的估算值'
    }

    return result


def _verdict_emotion(prediction: str, actual: str) -> str:
    """Check emotion prediction match"""
    keywords = ['\u51b0\u70b9', '\u9ad8\u6f6e', '\u9000\u6f6e', '\u6e29\u5e02', '\u4fee\u590d', '\u5347\u6e29']
    pred_kw = next((k for k in keywords if k in prediction), None)
    actual_kw = next((k for k in keywords if k in actual), None)
    if pred_kw and actual_kw and pred_kw == actual_kw:
        return '\u2714\u5bf9'
    if pred_kw and actual_kw:
        return '\u2718\u9519'
    if actual in prediction or prediction in actual:
        return '\u2714\u5bf9'
    return '\u2718\u9519'


def _emotion_direction(prediction: str) -> str:
    """Map emotion prediction to direction"""
    if not prediction:
        return 'neutral'
    if any(k in prediction for k in ['\u6e29\u5e02', '\u9ad8\u6f6e\u5ef6\u7eed', '\u8fdb\u5165\u9ad8\u6f6e', '\u5347\u6e29\u5ef6\u7eed', '\u6e29\u5ef6\u7eed', '\u5347\u6e29\u671f', '\u6e29\u5e02\u671f']):
        return 'positive'
    if any(k in prediction for k in ['\u9000\u6f6e', '\u7a7a\u4ed3\u89c2\u671b', '\u4e0b\u8dcc', '\u51b0\u70b9', '\u51b0\u70b9\u671f']):
        return 'negative'
    return 'neutral'


def _get_real_change_pure(code: str, base_date: str, t1_date: str, pred_ban: int, actual_tag: str, index_chg: float):
    """
    从MySQL查询T+1真实涨跌幅。
    基准：T+1每条记录的base_price字段 = T日收盘价（前一交易日收盘）。
    所以直接用 T+1收盘价 / base_price - 1 即可，无需查T日开盘价。
    """
    suffix = '.SZ' if code.startswith(('0', '3')) else '.SH'
    ts_code = code + suffix
    try:
        conn2 = pymysql.connect(
            host='localhost', user='root', password='675452716zm',
            database='stock_data', charset='utf8mb4', connect_timeout=3
        )
        cur2 = conn2.cursor()
        # T+1收盘价（fetch_date=T+1日期的最后一条分钟记录）
        cur2.execute(
            "SELECT price, base_price FROM stock_mins_data "
            "WHERE ts_code=%s AND fetch_date=%s ORDER BY price_time DESC LIMIT 1",
            (ts_code, t1_date))
        r2 = cur2.fetchone()
        conn2.close()
        if r2:
            close_p = float(r2[0])
            base_p = float(r2[1])  # base_price = T日收盘
            chg = (close_p - base_p) / base_p * 100
            return round(chg, 2), 'real', 'MySQL_ok'
    except Exception:
        pass
    # 回退：连板晋级状态估算（仅在MySQL查不到时）
    actual_ban = int(actual_tag.replace('ban', '')) if actual_tag and actual_tag.startswith('ban') else 0
    if actual_ban > 0 and pred_ban > 0:
        if actual_ban >= pred_ban:
            chg_est = min(actual_ban * 2.0, index_chg + 10.0)
        else:
            chg_est = max(index_chg - 3.0, -10.0)
    else:
        chg_est = round(index_chg, 2)
    return round(chg_est, 2), 'estimated', 'fallback_used'


def _verify_stocks(verifier, extractor, mm, stock_preds, lianban_t1, fupan, t1_date_str: str = '', base_date_str: str = '', market_stage: str = '') -> list:
    """
    Verify each stock prediction from step6 against T+1 actual data.
    market_stage: T日情绪阶段（如'冰点期'），用于写入accuracy_tracker。
    """
    verifications = []
    # 类级别单例 tracker（避免重复IO）
    _tracker = AccuracyTracker()

    # Build T+1 stock map: code -> actual performance
    # P3-fix: 4/7数据用stock_list而非lianban_list，保持与step4/step5一致的fallback逻辑
    actual_map = {}
    for grp in (lianban_t1.get('lianban_list') or lianban_t1.get('stock_list') or []):
        tag = grp.get('tag', '')
        for s in grp.get('list', []):
            code = s.get('stock_code', '').replace('sz', '').replace('sh', '')
            actual_map[code] = {
                'tag': tag,
                'status': 'lianban',
                'name': s.get('stock_name', ''),
            }

    # 获取T+1指数涨跌幅作为基准
    index_change = fupan.get('index', {}).get('sh', {}).get('pct_chg', 0.0) if fupan else 0.0

    for sp in stock_preds:
        code = sp.get('code', '')
        name = sp.get('name', '')
        morph = sp.get('morphology', '')
        pred_direction = sp.get('t1_direction', 'neutral')
        pred_expected = sp.get('t1_expected_change', '')
        conf = sp.get('confidence', 0.5)
        ban_tag = sp.get('ban_tag', '')
        pred_ban = int(ban_tag.replace('ban', '').replace('b', '')) if ban_tag.startswith('ban') else 0

        # Find actual T+1 state
        actual = actual_map.get(code)
        actual_tag = actual.get('tag', '') if actual else ''

        # P2-②修复：用真实MySQL价格（显式传入base_date）
        actual_close, data_source, mysql_status = _get_real_change_pure(
            code, base_date_str, t1_date_str, pred_ban, actual_tag, index_change
        )
        actual_direction = 'positive' if actual_close > 3 else ('negative' if actual_close < -3 else 'neutral')

        data_quality = 'real' if data_source == 'real' else 'estimated'
        data_quality_note = f'close_pct={actual_close}% via {data_source}({mysql_status})'

        vr = verifier.verify(
            {'direction': pred_direction, 'expected_change': pred_expected, 'confidence': conf},
            {'close_pct': actual_close, 'limit_down': actual_close < -9.5 and actual_tag != 'N/A'}
        )

        # Extract lesson
        lesson = extractor.extract(
            verify_result=vr,
            morphology=morph,
            market_stage=market_stage,  # P5-fix: 传T日情绪阶段（'高潮期'），不传板块名
            stock_name=name,
            prediction=sp,
            root_cause_hint=_infer_root_cause(vr, sp, actual_tag),
            data_quality=data_quality,   # V8修复：个股验证传data_quality，触发降权逻辑
        )

        verifications.append({
            'code': code,
            'name': name,
            'ban_tag': ban_tag,
            'morphology': morph,           # P3-fix补写：形态字段
            'predicted_direction': pred_direction,
            'actual_direction': actual_direction,
            'actual_tag': actual_tag,
            'actual_close_pct': actual_close,  # P2-⑧修复：补充缺失的actual_close_pct字段
            'correct': vr.correct,
            'score': vr.score,
            'lesson': lesson,
            'profit_ratio': vr.profit_ratio,   # 建议1新增：盈利比
            'data_quality': data_quality,      # P1-①/P1-②修复：标注数据质量
            'data_quality_note': data_quality_note,  # 便于后续追溯
        })

        # ── 写入 accuracy_tracker（闭环反馈）───────────────
        # 用 T日 stage，不是 T+1 stage
        if morph and market_stage and market_stage != 'unknown':
            _tracker.record(
                morphology=morph,
                market_stage=market_stage,
                correct=bool(vr.correct),
                profit_ratio=vr.profit_ratio,  # 建议1新增
            )

    return verifications


def _infer_root_cause(vr, sp, actual_tag) -> str:
    """Infer root cause of prediction error

    Priority: correct > board_exit > morph_specific > direction_mismatch > generic
    """
    if vr.correct:
        return '预测准确，认知有效'

    morph = sp.get('morphology', '')
    pred_dir = sp.get('t1_direction', '')
    code = sp.get('code', '')
    name = sp.get('name', '')

    # 1. Board exit: 股票脱离连板（最强信号）
    if actual_tag == 'N/A':
        return f'股票{name or code}T+1已脱称板块，预测失贤'

    # 2. Morphology-specific errors
    if morph in ('C1',) and not vr.correct:
        return f'C1形态高潮期风险被低估'
    if morph in ('D2',) and not vr.correct:
        return f'D2形态f30>80%未被重视'

    # 3. Direction mismatch: 晋级幅度不符预期（精细化，不合并）
    if pred_dir == 'positive' and actual_tag.startswith('ban'):
        # Tag went up: prediction too conservative
        pred_ban = int(sp.get('ban_tag', 'ban0').replace('ban', '').replace('b', '') or '0')
        actual_ban = int(actual_tag.replace('ban', ''))
        if actual_ban > pred_ban:
            return f'{morph}晋级幅度超预期（预期{pred_ban}板→实际{actual_ban}板）'
        elif actual_ban == pred_ban:
            return f'{morph}维持{pred_ban}板但涨幅低于预期'
        else:
            return f'{morph}晋级失败（预期{pred_ban}板→实际降为{actual_ban}板）'

    # 4. Direction mismatch (negative)
    if pred_dir == 'negative' and vr.lesson_key not in ('undershoot', 'correct'):
        return f'{morph}下跌幅度超预期'

    # 5. Generic fallback (narrower)
    return f'{morph}形态有效性需重新评估'


def _verify_ladder(result: dict, lianban_t1: dict, fupan: dict = None) -> None:
    """Verify ladder structure (P1修复: long_ban从fupan_data.long_ban读取，不是lianban_data.long_ban)"""
    ban_counts = {}
    for grp in lianban_t1.get('lianban_list', []):
        tag = grp.get('tag', '')
        if tag.startswith('ban'):
            ban_counts[tag] = len(grp.get('list', []))

    # P1修复: long_ban 在 fupan_data 里，T日来自step4传入的fupan，T+1日来自T+1的fupan
    # lianban_data 集合本身没有 long_ban 字段
    predicted_long_ban = fupan.get('long_ban', 0) if fupan else 0

    top_ban = 'N/A'
    for tag in ['ban7', 'ban6', 'ban5', 'ban4', 'ban3', 'ban2']:
        if tag in ban_counts and ban_counts[tag] > 0:
            top_ban = f"{ban_counts[tag]}只{tag}"
            break

    result['predictions'].append({
        'item': '最高板',
        'predicted': f'参考龙头预判（long_ban={predicted_long_ban}板）',
        'actual': top_ban,
        'verdict': '—',
    })
