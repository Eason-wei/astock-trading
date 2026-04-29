"""
Step5 可视化 — 4月28日
展示：候选股 → MySQL分钟数据 → 涨停强度因子计算全流程
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from project.data.datasource import DataSource
from project.steps import step5_stock_filter
from project.steps.step5_stock_filter import (
    _compute_zhangting_strength, _get_limit_ratio, _check_minute_pattern
)

# ── 数据加载 ──────────────────────────────────────────────
ds = DataSource()
s5 = step5_stock_filter.run(
    ds.get_lianban('2026-04-28'),
    ds.get_jiuyang('2026-04-28'),
    ds.get_mysql_minutes_fast('2026-04-28'),
)
mysql_stocks = ds.get_mysql_minutes_fast('2026-04-28')
ds.close()

# ── 打印 S5 概览 ──────────────────────────────────────────
print("=" * 70)
print("STEP5 成分股筛选 — 2026-04-28")
print("=" * 70)
print(f"\n主线板块（tier1）: {s5['tier1_plates']}")
for d in s5.get('tier1_detail', []):
    print(f"  {d['name']}: {d['topnum']}只在板  龙头={d['longtou_name']}({d['longtou_tag']})")

print(f"\n候选股总数: {len(s5['candidates'])}")
print(f"包含数: {s5['filter_summary']['included']}")
print(f"排除数: {s5['filter_summary']['excluded']}")

# ── 核心：逐只计算涨停强度因子 ────────────────────────────
def bar(v, width=10, fill='█', empty='░'):
    """打印一条横杠，v in [0,100]"""
    w = int(v / 100 * width)
    return fill * w + empty * (width - w)

FACTOR_INFO = {
    'A1': ('首封时间',  '越早越好  ≤60min=100分',  0.10),
    'A2': ('封板时长',  '触板后全程封板满分',       0.08),
    'A3': ('炸板次数',  '0次=100  每炸1次-30',    0.10),
    'A4': ('最大开板',  '0min=100  >30min=0分',   0.07),
    'B1': ('开盘涨幅',  '0~5%=100  高开低分',      0.08),
    'B2': ('振幅',     '5~15%=100  太小/大扣分',   0.08),
    'B3': ('均价偏离',  '偏离越小越好',             0.12),
    'B4': ('分时平滑',  'R²×50 + (1-max_dd/20)×50', 0.07),
    'C1': ('封板量比',  '封板量/触板后总量 >50%=满分', 0.12),
    'C2': ('触板量比',  '触板量/均量 >5倍满分',     0.10),
    'C3': ('后量占比',  '触板后量/全天量 <30%=满分', 0.08),
}

print("\n" + "=" * 70)
print("涨停强度因子详情")
print("=" * 70)

results = []
for c in s5['candidates']:
    code = c['code']
    name = c['name']
    plate = c['plate']
    ban = c.get('ban_tag', 'N/A')
    form = c.get('minute_pattern', {}).get('form', 'N/A')
    is_lb = c.get('is_lianban', False)

    # 找 MySQL 分钟数据（复用 step5 的 find_mysql_key 逻辑）
    clean = code.replace('sz', '').replace('sh', '').replace('SZ', '').replace('SH', '').replace('.SZ','').replace('.SH','').strip()
    mysql_key = None
    for suffix in ['.SZ', '.SH']:
        key = f'{clean}{suffix}'
        if key in mysql_stocks:
            mysql_key = key
            break
    mins = mysql_stocks.get(mysql_key) if mysql_key else None
    if not mins:
        print(f"\n⚠️ {code} {name} 无MySQL分钟数据，跳过因子计算")
        continue

    # 计算因子
    try:
        factors = _compute_zhangting_strength(mins, code, is_lb)
    except Exception as e:
        print(f"\n⚠️ {code} {name} analyze_limit_strength 报错: {e}")
        continue

    sub = factors['_sub_scores']
    total = factors['score']

    results.append({
        'code': code, 'name': name, 'plate': plate,
        'ban': ban, 'form': form, 'is_lb': is_lb,
        'factors': factors, 'sub': sub, 'total': total
    })

# 按总分降序
results.sort(key=lambda x: x['total'], reverse=True)

# 打印前10只
for rank, r in enumerate(results[:10], 1):
    print(f"\n{'─'*70}")
    print(f" #{rank}  {r['code']} {r['name']}  板块={r['plate']}  {r['ban']}  形态={r['form']}  {'📈在板' if r['is_lb'] else '📊跟风'}")
    print(f" 总分: {r['total']:.1f}  {'★'*int(r['total']//20)}")
    print(f" {'因子':<14} {'原始值':<18} {'得分':<8} {'权重':<6} {'贡献分':<8} {'分布'}")
    print(f" {'─'*14} {'─'*18} {'─'*8} {'─'*6} {'─'*8} {'─'*10}")

    for fk, (fname, fdesc, fw) in FACTOR_INFO.items():
        # 原始值key（_compute_zhangting_strength 返回的真实字段名）
        raw_key_map = {
            'A1': 'A1_first_hit_min', 'A2': 'A2_total_seal_min',
            'A3': 'A3_zhaban_cnt',    'A4': 'A4_max_open_min',
            'B1': 'B1_open_chg',      'B2': 'B2_amp',
            'B3': 'B3_relative_vwap',  'B4': 'B4_smoothness',
            'C1': 'C1_seal_vol_ratio', 'C2': 'C2_limit_ratio',
            'C3': 'C3_post_limit_vol',
        }
        # 子分key（_sub_scores 里的键名）
        sub_key_map = {
            'A1': 'A1_first_hit', 'A2': 'A2_seal_dur',
            'A3': 'A3_zhaban',    'A4': 'A4_max_open',
            'B1': 'B1_open_chg',  'B2': 'B2_amp',
            'B3': 'B3_vwap',      'B4': 'B4_smooth',
            'C1': 'C1_seal',      'C2': 'C2_ratio',
            'C3': 'C3_post_vol',
        }
        raw_key = raw_key_map.get(fk, '')
        sub_key = sub_key_map.get(fk, '')

        raw_val = r['factors'].get(raw_key, 'N/A')
        sub_score = r['sub'].get(sub_key, 0)
        contrib = sub_score * fw

        if isinstance(raw_val, float):
            raw_str = f'{raw_val:.2f}'
        elif isinstance(raw_val, int):
            raw_str = f'{raw_val}'
        else:
            raw_str = str(raw_val)

        bar_str = bar(sub_score, width=10)
        print(f" {fname:<14} {raw_str:<18} {sub_score:>6.1f}   {fw:.0%}    {contrib:>5.2f}   {bar_str}")

print(f"\n{'='*70}")
print(f"共 {len(results)} 只候选股参与因子计算")
print("=" * 70)

# ── 附加：分钟形态检查结果 ────────────────────────────────
print("\n\n分钟形态检查（分钟数据质量）:")
print("-" * 60)
for r in results[:10]:
    mp = r['factors']
    print(f"  {r['code']} {r['name']:<8} 开={mp.get('limit_up','?')}  "
          f"首封={r['factors'].get('A1_first_hit_min','?')}min  "
          f"振幅={r['factors'].get('B2_amp','?')}%  "
          f"f30={r['factors'].get('B4_smoothness','?')}")

# ── 统计 ──────────────────────────────────────────────────
print("\n\n板块分布:")
from collections import Counter
plate_cnt = Counter(r['plate'] for r in results)
for p, cnt in plate_cnt.most_common():
    print(f"  {p}: {cnt}只")

print("\n形态分布:")
form_cnt = Counter(r['form'] for r in results)
for f, cnt in form_cnt.most_common():
    print(f"  {f}: {cnt}只")

print("\n在板/跟风分布:")
lb_cnt = Counter('在板' if r['is_lb'] else '跟风' for r in results)
for k, cnt in lb_cnt.most_common():
    print(f"  {k}: {cnt}只")
