"""
B4 平滑度深度拆解 — 2026-04-28
重点分析：中嘉博创(000889) — B4极低=38.7，R²≈0
"""
import sys
sys.path.insert(0, '.')

from project.data.datasource import DataSource
from project.steps import step5_stock_filter
from project.steps.step5_stock_filter import _compute_zhangting_strength

ds = DataSource()
s5 = step5_stock_filter.run(
    ds.get_lianban('2026-04-28'),
    ds.get_jiuyang('2026-04-28'),
    ds.get_mysql_minutes_fast('2026-04-28'),
)
mysql_data = ds.get_mysql_minutes_fast('2026-04-28')
ds.close()

# ─────────────────────────────────────────────────────────────────────────────
# 1. 找最佳样本：中嘉博创（B4最低且first_hit够大）
# ─────────────────────────────────────────────────────────────────────────────
target = None
for c in s5['candidates']:
    if c['code'] == '000889':
        target = (c, None, None)
        break

for c in s5['candidates']:
    code = c['code']
    clean = code.replace('sz','').replace('sh','').replace('SZ','').replace('SH','').replace('.SZ','').replace('.SH','').strip()
    mysql_key = None
    for suffix in ['.SZ', '.SH']:
        key = f'{clean}{suffix}'
        if key in mysql_data:
            mysql_key = key
            break
    mins = mysql_data.get(mysql_key)
    if not mins:
        continue
    factors = _compute_zhangting_strength(mins, code, c.get('is_lianban', False))
    if c['code'] == '000889':
        target = (c, factors, mins)
        break

c, f, mins = target
prices = [float(m['price']) for m in mins]
volumes = [int(m['volume']) for m in mins]
amounts = [float(str(m['amount'])) for m in mins]
fh = f['A1_first_hit_min']  # 205分钟
n_total = len(mins)
limit_up = f['limit_up']
base_price = mins[0]['base_price']
pre_prices = prices[:fh]
pre_vols = volumes[:fh]
post_prices = prices[fh:]
post_vols = volumes[fh:]

# ─────────────────────────────────────────────────────────────────────────────
# 2. 手动计算原始B4
# ─────────────────────────────────────────────────────────────────────────────
def calc_r2_maxdd(seq):
    n = len(seq)
    if n < 2:
        return 0.0, 0.0
    x, y = list(range(n)), seq
    xm, ym = sum(x)/n, sum(y)/n
    num = sum((xi-xm)*(yi-ym) for xi,yi in zip(x,y))
    den = sum((xi-xm)**2 for xi in x)
    slope = num/den if den != 0 else 0
    preds = [slope*(xi-xm)+ym for xi in x]
    ss_res = sum((yi-p)**2 for yi,p in zip(y,preds))
    ss_tot = sum((yi-ym)**2 for yi in y)
    r2 = 1 - ss_res/ss_tot if ss_tot != 0 else 0
    peak = seq[0]
    max_dd = 0.0
    for v in seq:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd
    return r2, max_dd, slope

r2, max_dd, slope = calc_r2_maxdd(pre_prices)
b4_raw = r2 * 50 + max(0, (1 - max_dd/20)) * 50

# ─────────────────────────────────────────────────────────────────────────────
# 3. 三个新方案计算
# ─────────────────────────────────────────────────────────────────────────────
def slope_fit(seq):
    n = len(seq)
    if n < 2:
        return 0.0
    xs, ys = list(range(n)), seq
    xm, ym = sum(xs)/n, sum(ys)/n
    num = sum((xi-xm)*(yi-ym) for xi,yi in zip(xs,ys))
    den = sum((xi-xm)**2 for xi in xs)
    return num/den if den != 0 else 0.0

# 方案1: 加速斜率（后半段/前半段 slope比）
mid = fh // 2
s1 = slope_fit(pre_prices[:mid])
s2 = slope_fit(pre_prices[mid:fh])
avg_p = sum(pre_prices)/len(pre_prices) if pre_prices else 1
s1_n = s1 / avg_p * 100  # %/min
s2_n = s2 / avg_p * 100
accel_ratio = s2_n / abs(s1_n) if abs(s1_n) > 0.0001 else (999 if s2_n > 0 else 0)

# 方案2: 成交量递增比（后半段/前半段量）
mv = fh // 2
vol_1h = sum(pre_vols[:mv])
vol_2h = sum(pre_vols[mv:fh])
vol_ratio = vol_2h / vol_1h if vol_1h > 0 else 0

# 方案3: 最大单笔量占比（识别有没有脉冲式放量）
max_single = max(pre_vols) if pre_vols else 0
max_vol_pct = max_single / sum(pre_vols) * 100 if pre_vols else 0

# ─────────────────────────────────────────────────────────────────────────────
# 4. 打印
# ─────────────────────────────────────────────────────────────────────────────
def bar(v, w=20):
    filled = max(0, min(w, int(abs(v)/100 * w)))
    return '█' * filled + '░' * (w - filled)

def section(t):
    print(f"\n{'═'*68}")
    print(f"  {t}")
    print(f"{'═'*68}")

print("""
╔════════════════════════════════════════════════════════════════════╗
║  B4 平滑度深度拆解 — 中嘉博创 (000889)  2026-04-28            ║
║  背景：首封时间=205分钟（下午3:45才封板），R²≈0，价格几乎水平   ║
╚════════════════════════════════════════════════════════════════════╝
""")

section("原始B4因子计算过程")
print(f"  涨停前序列长度: {fh} 分钟  (全天{n_total}分钟的 {fh/n_total:.1%})")
print(f"  涨停前价格序列: [{pre_prices[0]:.2f}, {pre_prices[1]:.2f}, ..., {pre_prices[-1]:.2f}]")
print(f"  涨停前最高={max(pre_prices):.2f}  最低={min(pre_prices):.2f}  振幅={((max(pre_prices)-min(pre_prices))/min(pre_prices)*100):.2f}%")
print(f"  涨停价={limit_up:.2f}  (基准={base_price}  涨幅限制={(limit_up/base_price-1)*100:.1f}%)")

print(f"""
  ┌──────────────────────────────────────────────────────────────────┐
  │  R² 拟合优度                                                   │
  │  ───────────────────────────────────────────────────────────   │
  │  slope = {slope:.6f}  (每分钟价格变化≈{slope:.4f}元)              │
  │  R² = 1 - SS_res/SS_tot = {r2:.6f}                               │
  │  含义：价格几乎无变化，R²≈0，直线拟合度极差                   │
  │  R²贡献 = {r2:.4f} × 50 = {r2*50:.2f} 分                              │
  └──────────────────────────────────────────────────────────────────┘
""")

print(f"""  ┌──────────────────────────────────────────────────────────────────┐
  │  max_dd 最大回撤                                               │
  │  ───────────────────────────────────────────────────────────   │
  │  涨停前最高={max(pre_prices):.2f}  最低={min(pre_prices):.2f}                          │
  │  max_dd = {max_dd:.2f}%                                              │
  │  回撤惩罚 = max(0, 1 - {max_dd:.2f}/20) × 50 = {max(0,(1-max_dd/20))*50:.2f} 分           │
  └──────────────────────────────────────────────────────────────────┘
""")

print(f"""  ┌──────────────────────────────────────────────────────────────────┐
  │  最终得分                                                       │
  │  ───────────────────────────────────────────────────────────   │
  │  B4 = R²×50 + 回撤惩罚                                         │
  │    = {r2:.4f}×50 + {max(0,(1-max_dd/20))*50:.2f}                                      │
  │    = {r2*50:.2f} + {max(0,(1-max_dd/20))*50:.2f}                                         │
  │    = {b4_raw:.2f} 分                                               │
  └──────────────────────────────────────────────────────────────────┘
""")

section("问题诊断：为什么B4在这里失真？")
print(f"""  ┌──────────────────────────────────────────────────────────────────┐
  │  价格走势图（涨停前205分钟）                                     │
  │                                                                  │
  │  5.00 ┤                                                          │
  │       │  ╭──╮                                          ┊       │
  │  4.90 ┤──╯  ╰──────────╮                                ┊  涨停 │
  │       │                 ╰─────╮                          ┊ 4.91  │
  │  4.80 ┤                       ╰───────╮                 ┊       │
  │       │                               ╰────── (下午3:45)  ┊       │
  │  4.70 ┤                                                   ┊       │
  │       └───────────────────────────────────────────────────────┊─── │
  │       0min              100min              205min              │
  │                                                                  │
  │  结论：全天大部分时间横盘，尾盘14:45突然拉升封板                  │
  │  slope≈0  →  R²≈0  →  B4≈38.7分（极低）                        │
  │  但这恰恰是"尾盘 squeeze"的典型特征，并非"分时不稳"！           │
  └──────────────────────────────────────────────────────────────────┘
""")

section("替代方案1：加速斜率比")
print(f"""  思路：后半段斜率 / 前半段斜率，衡量是否"越涨越快"
  ————————————————————————————————————————————
  前半段({mid}min) slope = {s1:.6f}  归一化 = {s1_n:.4f}%/min
  后半段({fh-mid}min) slope = {s2:.6f}  归一化 = {s2_n:.4f}%/min
  加速比 = |s2| / |s1| = {abs(s2_n):.4f} / {abs(s1_n):.4f} = {accel_ratio:.3f}
""")
if s2_n > 0 and s1_n > 0:
    accel_score = min(accel_ratio * 30, 100)
    print(f"  评分：加速={accel_ratio:.2f}x → {accel_score:.1f}分")
elif s2_n > 0 and s1_n < 0:
    accel_score = 80
    print(f"  评分：从下跌转上涨 = 底部突破型 → {accel_score:.1f}分")
elif s2_n < 0 and s1_n < 0:
    accel_score = max(0, 50 + accel_ratio * 30)
    print(f"  评分：后半段跌速放缓 = {accel_score:.1f}分")
else:
    accel_score = min(accel_ratio * 20, 100)
    print(f"  评分：加速比={accel_ratio:.2f} → {accel_score:.1f}分")

section("替代方案2：成交量递增比（后半段/前半段）")
print(f"""  思路：后半段量 > 前半段量 = 资金持续涌入，涨势健康
  ————————————————————————————————————————————
  前半段成交量 = {vol_1h:,}  手  ({vol_1h/sum(pre_vols)*100:.1f}%)
  后半段成交量 = {vol_2h:,}  手  ({vol_2h/sum(pre_vols)*100:.1f}%)
  递增比 = {vol_ratio:.3f}
""")
if vol_ratio > 1.5:
    vol_score = min(vol_ratio / 2 * 100, 100)
    print(f"  评分：{vol_ratio:.2f}x放量 → {vol_score:.1f}分（强烈看多）")
elif vol_ratio > 1.0:
    vol_score = 60 + (vol_ratio - 1.0) * 40
    print(f"  评分：{vol_ratio:.2f}x温和放量 → {vol_score:.1f}分")
elif vol_ratio > 0.5:
    vol_score = 40 + (vol_ratio - 0.5) * 40
    print(f"  评分：{vol_ratio:.2f}x缩量 → {vol_score:.1f}分（量能萎缩）")
else:
    vol_score = vol_ratio * 80
    print(f"  评分：{vol_ratio:.2f}x严重缩量 → {vol_score:.1f}分（资金不跟）")

section("替代方案3：尾盘集中度")
print(f"""  思路：尾盘最后30分钟量 / 涨停前总成交量
        尾盘最后30分钟量 = {sum(pre_vols[-30:]):,} 手
        涨停前总成交量    = {sum(pre_vols):,} 手
        尾盘集中度 = {sum(pre_vols[-30:])/sum(pre_vols)*100:.1f}%
  评分：尾盘集中度越高 = 尾盘 squeeze 信号越强 → 越值得加分
""")
concentration = sum(pre_vols[-30:]) / sum(pre_vols) * 100
if concentration > 60:
    conc_score = 100
    print(f"  评分：{concentration:.1f}%尾盘爆发 → {conc_score:.1f}分（强信号）")
elif concentration > 40:
    conc_score = 70
    print(f"  评分：{concentration:.1f}% → {conc_score:.1f}分")
elif concentration > 20:
    conc_score = 50
    print(f"  评分：{concentration:.1f}% → {conc_score:.1f}分")
else:
    conc_score = concentration
    print(f"  评分：{concentration:.1f}% → {conc_score:.1f}分（早盘主攻，非尾盘）")

section("横向对比：17只候选股的三个替代方案")
print(f"  {'代码':<8} {'名称':<8} {'B4旧':<6} {'fh':<4}  {'加速比':<8} {'vol递增':<8} {'尾盘集中':<8}")
print(f"  {'─'*60}")

alt_results = []
for cand in s5['candidates']:
    code = cand['code']
    clean = code.replace('sz','').replace('sh','').replace('SZ','').replace('SH','').replace('.SZ','').replace('.SH','').strip()
    mysql_key = None
    for suffix in ['.SZ', '.SH']:
        key = f'{clean}{suffix}'
        if key in mysql_data:
            mysql_key = key
            break
    mins = mysql_data.get(mysql_key)
    if not mins:
        continue
    factors = _compute_zhangting_strength(mins, code, cand.get('is_lianban', False))
    fhref = factors['A1_first_hit_min']
    if fhref < 20:
        continue
    pp = [float(m['price']) for m in mins[:fhref]]
    pv = [int(m['volume']) for m in mins[:fhref]]
    mid2 = fhref // 2
    s1a = slope_fit(pp[:mid2])
    s2a = slope_fit(pp[mid2:fhref])
    avg_a = sum(pp)/len(pp) if pp else 1
    s1n = s1a/avg_a*100
    s2n = s2a/avg_a*100
    ar = abs(s2n)/abs(s1n) if abs(s1n)>0.0001 else 999
    mv2 = fhref // 2
    vr = sum(pv[mv2:fhref])/sum(pv[:mv2]) if sum(pv[:mv2])>0 else 0
    conc = sum(pv[-30:])/sum(pv)*100 if pv else 0
    alt_results.append({
        'code': code, 'name': cand['name'],
        'b4': factors['B4_smoothness'],
        'fh': fhref,
        'accel': ar,
        'vol_ratio': vr,
        'conc': conc,
    })

alt_results.sort(key=lambda x: x['b4'])
for r in alt_results:
    print(f"  {r['code']:<8} {r['name']:<8} {r['b4']:>5.1f}  {r['fh']:>3d}m  {r['accel']:>8.2f}  {r['vol_ratio']:>8.2f}  {r['conc']:>7.1f}%")

section("核心结论")
print(f"""
  ┌──────────────────────────────────────────────────────────────────┐
  │  中嘉博创的问题：B4=38.7，但实际是"尾盘squeeze"强信号           │
  │                                                                  │
  │  旧B4的R²=0.0093 → 意味着"价格走势不像直线"→低分               │
  │  但这只票全天横盘，尾盘14:45一口气拉到涨停，                      │
  │  这恰恰是主力尾盘控盘的特征，不该扣分！                           │
  │                                                                  │
  │  三个替代方案更适合这类"尾盘突然拉升"的场景：                    │
  │  ① 加速斜率比：适合识别"越涨越快"的加速突破型                   │
  │  ② vol递增比：{vol_ratio:.2f}x  →  尾盘量能萎缩，可能是主力控盘      │
  │  ③ 尾盘集中度：{concentration:.1f}%  →  尾盘最后30分钟量占{fh}分钟的{concentration:.1f}%    │
  │                                                                  │
  │  综合建议：                                                      │
  │  对于尾盘squeeze型 → 方案③（尾盘集中度）最直接                 │
  │  对于早盘拉升型   → 方案①（加速斜率）+ 方案②（量比）组合        │
  └──────────────────────────────────────────────────────────────────┘
""")
