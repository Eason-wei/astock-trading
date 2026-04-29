"""
run_real.py — 真实数据完整运行脚本
用法: python3 run_real.py [2026-04-20]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from project.data.datasource import DataSource
from project.steps import (
    step1_global_scan, step2_main_line, step3_emotion_cycle,
    step4_lianban_health, step5_stock_filter, step6_t1_prediction,
    step7_verification, step8_closure,
)
from decision import MorphologyMatrix, PositionRules, ThreeQuestions, RiskController
from verify import LessonExtractor


TARGET_DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-04-17"  # 默认T日
T1_DATE = sys.argv[2] if len(sys.argv) > 2 else None                    # 不再设默认值，由自动查询决定

# 自动查询：T+1、T+2、T+3... 直到数据库有数据
def _find_t1_date(ds, target_date, max_days=5):
    """从T+1开始往后查，直到某天数据库有fupan数据"""
    from project.steps.step6_t1_prediction import _get_next_trade_date
    d = _get_next_trade_date(target_date)
    tried = []
    for _ in range(max_days):
        fupan = ds.get_fupan(d)
        if fupan and fupan.get('date'):
            return d, tried
        tried.append(d)
        d = _get_next_trade_date(d)
    return d, tried

ds = DataSource()
T1_DATE, tried = _find_t1_date(ds, TARGET_DATE)
if tried:
    print(f"⚠️ T日={TARGET_DATE}，T+1无数据，依次查询T+1...{T1_DATE}，实际验证日={T1_DATE}")
else:
    print(f"  T日={TARGET_DATE}，验证日={T1_DATE}")

print(f"\n{'='*60}")
print(f"  真实数据运行 | 日期: {TARGET_DATE}")
print(f"{'='*60}\n")

ds = DataSource()
mm = MorphologyMatrix()
pr = PositionRules()
tq = ThreeQuestions()
rc = RiskController()
extractor = LessonExtractor()

# ── Step 1: 全局扫描 ───────────────────────────────────────
print(f"[1/8] 全局扫描...")
s1 = step1_global_scan.run(ds.get_fupan(TARGET_DATE), ds.get_lianban(TARGET_DATE))
print(f"  date={s1.get('date')} | qingxu={s1.get('qingxu')} | degree_market={s1.get('degree_market')}")
print(f"  risk_signals={s1.get('risk_signals', [])[:3]}")
print(f"  opportunity_signals={s1.get('opportunity_signals', [])[:3]}")
print(f"  market_structure={s1.get('market_structure')}")
print(f"  position_label={s1.get('position_label')}")

# ── Step 2: 主线分析 ──────────────────────────────────────
print(f"\n[2/8] 主线分析...")
s2 = step2_main_line.run(ds.get_lianban(TARGET_DATE), ds.get_jiuyang(TARGET_DATE), ds.get_fupan(TARGET_DATE))
print(f"  tier1={s2.get('tier1')}")
print(f"  tier2={s2.get('tier2')}")
print(f"  tier3={s2.get('tier3')}")
print(f"  zhuxian count={len(s2.get('zhuxian', []))}")
lb_list = s2.get('lianban_list', {})
ban_counts = {t: lb_list.get(t, {}).get('cnt', 0) for t in ['ban7','ban6','ban5','ban4','ban3','ban2','ban1']}
print(f"  板数分布={ban_counts}")
print(f"  suggestion={s2.get('suggestion')}")
print(f"  strength_eval={s2.get('strength_eval')}")

# ── Step 3: 情绪周期 ──────────────────────────────────────
print(f"\n[3/8] 情绪周期...")
s3 = step3_emotion_cycle.run(ds.get_fupan(TARGET_DATE))
print(f"  qingxu={s3.get('qingxu')} | degree_market={s3.get('degree_market')}")
print(f"  stage={s3.get('cycle_position')} | base_position={s3.get('base_position')}")
print(f"  verdict={s3.get('verdict')}")
print(f"  strategy={s3.get('strategy')}")

# ── Step 4: 连板健康度 ────────────────────────────────────
print(f"\n[4/8] 连板健康度...")
s4 = step4_lianban_health.run(ds.get_lianban(TARGET_DATE), top_rate=s3.get('top_rate', 0))
print(f"  health_score={s4.get('health_score')}")
print(f"  verdict={s4.get('verdict')}")
print(f"  warnings={s4.get('warnings', [])[:3]}")
print(f"  ladder={list(s4.get('ladder', {}).keys())}")

# ── Step 5: 成分股筛选 ────────────────────────────────────
print(f"\n[5/8] 成分股筛选...")
s5 = step5_stock_filter.run(
    ds.get_lianban(TARGET_DATE),
    ds.get_jiuyang(TARGET_DATE),
    ds.get_mysql_minutes_fast(TARGET_DATE),
)
print(f"  candidates count={len(s5.get('candidates', []))}")

# ── Step 6: T+1预判 ───────────────────────────────────────
print(f"\n[6/8] T+1预判...")
s1['date'] = TARGET_DATE  # Step6 需要 date 字段
fupan_raw = ds.get_fupan(TARGET_DATE)  # 原始fupan数据，用于pain_effect_analyzer
s6 = step6_t1_prediction.run(s1, s2, s3, s4, s5, fupan=fupan_raw, ds=ds)
print(f"  t1_date={s6.get('t1_date')}")
print(f"  position_plan={s6.get('position_plan', {})}")
# 打印 pain_effect 分析结果
pe = s6.get('pain_effect')
if pe:
    print(f"  pain_effect: score={pe['score']} level={pe['level']} trend={pe['trend']}")
    print(f"    breakdown: {pe['breakdown']}")
    if pe['veto_triggered']:
        print(f"    ⚠️ veto_triggered: {pe['veto_reasons']}")
tq = s6.get('three_questions', {})
print(f"  三问: score={tq.get('overall_score')} verdict={tq.get('final_verdict')}")
print(f"  stock_predictions count={len(s6.get('stock_predictions', []))}")

# 每次运行后保存pain评分到MongoDB（跨日趋势计算）
if pe:
    ds.save_pain_score(TARGET_DATE, pe['score'], pe['level'])

# ── Step 7: 验证 ──────────────────────────────────────────
print(f"\n[7/8] 验证...")
s7 = step7_verification.run(
    t1_actual={'fupan': ds.get_fupan(T1_DATE), 'lianban': ds.get_lianban(T1_DATE)},
    predictions=s6.get('stock_predictions', []),
    position_plan=s6.get('position_plan', {}),
    step6_stock_preds=s6.get('stock_predictions', []),
    target_date=TARGET_DATE,
    t1_date=T1_DATE,
    market_stage=s3.get('qingxu', 'unknown'),  # T日情绪阶段，用于accuracy_tracker记录
)
t1_actual_date = s6.get('t1_date', T1_DATE)
print(f"  T+1验证日={t1_actual_date}（目标={T1_DATE}）")
print(f"  情绪预测 items: {len(s7.get('predictions', []))}")
print(f"  个股验证数: {len(s7.get('stock_verifications', []))}")
print(f"  总分: {s7.get('score', 0)}/{s7.get('total', 0)}")
print(f"  accuracy: {s7.get('accuracy', 'N/A')}")
print(f"  lessons count: {len(s7.get('lessons', []))}")
print(f"  cognition_updates count: {len(s7.get('cognition_updates', []))}")

# ── Step 8: 闭环 ──────────────────────────────────────────
print(f"\n[8/8] 闭环...")
s8 = step8_closure.run(s7, s1, s2, s3, s4, s5, s6)
print(f"  cognitive_analysis: {len(s8.get('cognitive_analysis', []))}")
print(f"  beliefstore_writes: {s8.get('beliefstore_writes', 0)}")
print(f"  report: {s8.get('report_path', 'N/A')}")
print(f"  summary[:300]: {str(s8.get('summary', ''))[:300]}")

print(f"\n{'='*60}")
print(f"  ✅ 完成! 日期={TARGET_DATE}")
print(f"{'='*60}")

ds.close()
