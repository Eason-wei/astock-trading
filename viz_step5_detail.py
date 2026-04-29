"""
Step5 涨停强度因子 — 逐因子手算演示（群兴玩具 002575）
新公式：首封时间/封板时长用 100/241 比例，开盘涨幅用线性比例，振幅改为一字板满分
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

# 找群兴玩具
for c in s5['candidates']:
    if c['code'] != '002575':
        continue
    mins_key = None
    for suffix in ['.SZ', '.SH']:
        key = f'002575{suffix}'
        if key in mysql_data:
            mins_key = key
            break
    mins = mysql_data.get(mins_key)
    factors = _compute_zhangting_strength(mins, '002575', c.get('is_lianban', False))
    break

n = len(mins)
prices = [float(m['price']) for m in mins]
volumes = [int(m['volume']) for m in mins]
amounts = [float(str(m['amount'])) for m in mins]
base_price = mins[0]['base_price']
limit_up = factors['limit_up']
first_hit = factors['A1_first_hit_min']
total_seal = factors['A2_total_seal_min']
zhaban = factors['A3_zhaban_cnt']
max_open = factors['A4_max_open_min']
open_chg = factors['B1_open_chg']
amp = factors['B2_amp']
vwap = factors['B3_post_limit_vwap']
rel_vwap = factors['B3_relative_vwap']
smooth = factors['B4_smoothness']
max_dd = factors['B4_max_dd']
c1 = factors['C1_seal_vol_ratio']
c2 = factors['C2_limit_ratio']
c3 = factors['C3_post_limit_vol']

sub = factors['_sub_scores']

# ─────────────────────────────────────────────────────────────────────────────
# 原始公式（代码里的）
# ─────────────────────────────────────────────────────────────────────────────
def old_a1(fh):
    if fh < 0: return 0
    elif fh <= 60: return 100
    elif fh <= 120: return 80
    elif fh <= 180: return 50
    else: return 20

def old_a2(fh, ts, avail):
    if fh < 0 or avail <= 0: return 0
    return min(ts / avail * 100, 100)

def old_b1(ochg):
    if 0 <= ochg <= 5: return 100
    elif ochg > 5: return max(0, 100 - (ochg - 5) * 15)
    else: return max(0, 80 + ochg * 4)

def old_b2(amp):
    if 0.5 <= amp < 5: return 50
    elif 5 <= amp <= 15: return 100
    elif amp > 15: return max(0, 100 - (amp - 15) * 5)
    else: return 30  # amp < 0.5

# ─────────────────────────────────────────────────────────────────────────────
# 新公式（用户要求）
# ─────────────────────────────────────────────────────────────────────────────
def new_a1(fh):
    """首封时间：越早越好，100 - 100/n × first_hit，最高100"""
    return max(0, 100 - 100 / n * fh)

def new_a2(ts):
    """封板时长：100/241 × 时长，最高100"""
    return min(100 / n * ts, 100)

def new_b1(ochg):
    """开盘涨幅：-1%=−10分，5%=50分，10%=100分，线性比例，clamp[-100,100]"""
    score = -10 + (ochg + 1) * 10
    return max(-100, min(100, score))

def new_b2(amp):
    """振幅：<0.5%(一字板)=100分，每超1%扣5分，上限100"""
    if amp < 0.5:
        return 100
    return max(0, min(100, 100 - (amp - 0.5) * 5))

# ─────────────────────────────────────────────────────────────────────────────
# 辅助：打印进度条
# ─────────────────────────────────────────────────────────────────────────────
def bar(v, mx, w=20):
    filled = max(0, min(w, int(v / mx * w)))
    return '█' * filled + '░' * (w - filled)

def section(title):
    print(f"\n{'═'*70}")
    print(f"  {title}")
    print(f"{'═'*70}")

def subsection(title):
    print(f"\n┌{'─'*68}┐")
    print(f"│ {title:<66} │")
    print(f"└{'─'*68}┘")

# ─────────────────────────────────────────────────────────────────────────────
# 打印原始分钟数据
# ─────────────────────────────────────────────────────────────────────────────
print("""
╔══════════════════════════════════════════════════════════════════════╗
║     Step5 涨停强度因子 — 逐因子手算演示                               ║
║     股票：群兴玩具 (002575)   日期：2026-04-28                       ║
╚══════════════════════════════════════════════════════════════════════╝
""")

section("分钟数据概览")
print(f"  全天分钟数 n = {n}  (4小时×60+1)")
print(f"  昨收基准价  = {base_price:.2f}")
print(f"  今日涨停价  = {limit_up:.2f}  (涨幅限制 = {limit_up/base_price:.1%})")
print(f"  开盘价      = {prices[0]:.2f}  →  开盘涨幅 = {open_chg:.2f}%")
print(f"  最高价      = {max(prices):.2f}  最低价 = {min(prices):.2f}")
print(f"  收盘价      = {prices[-1]:.2f}  {'← 涨停' if prices[-1] >= limit_up * 0.999 else ''}")
print(f"  振幅        = {amp:.2f}%")
print(f"  全天总成交量 = {sum(volumes):,}  手")
print(f"  全天总成交额 = {sum(amounts):,.0f}  元")
print(f"  首封时间     = {first_hit} 分钟（第 {first_hit} 分钟时价格触及涨停价）")
print(f"  封板时长     = {total_seal} 分钟（触板后持续在涨停价的分钟数）")
print(f"  炸板次数     = {zhaban} 次")
print(f"  最大开板时长 = {max_open} 分钟")

# ─────────────────────────────────────────────────────────────────────────────
# A1 首封时间
# ─────────────────────────────────────────────────────────────────────────────
section("A1 首封时间  [权重 10%]")
subsection("原始公式（分段阶梯）")
print(f"  规则：≤60min=100分  61~120=80分  121~180=50分  >180=20分")
old_a1_score = old_a1(first_hit)
print(f"  首封时间 = {first_hit}min → 落在 61~120 区间")
print(f"  原始得分 = {old_a1_score} 分")
print(f"  ┌{'─'*60}┐")
print(f"  │  0    60   120   180   240                       min │")
print(f"  │{'░'*20}████████████{'░'*20}                    │")
print(f"  │  100  100  [80]  50   20                        │")
print(f"  └{'─'*60}┘")

subsection("新公式（反比例：越早分越高）")
print(f"  公式：A1 = max(0, 100 - 100/n × first_hit)  = max(0, 100 - 100/{n} × {first_hit})")
a1_val = 100/n*first_hit
print(f"  A1 = max(0, 100 - {a1_val:.2f}) = {100-a1_val:.2f}")
new_a1_score = new_a1(first_hit)
print(f"  clamp后 = {new_a1_score:.2f} 分")

print(f"\n  ┌{'─'*60}┐")
print(f"  │  0              {first_hit}              {n}    min  │")
print(f"  │             [{'░'*int((1-first_hit/n)*30):<30}]██████████  │")
print(f"  │  100分                                          0分  │")
print(f"  │  A1={new_a1_score:.2f}分（越早封板 → 分越高）                      │")
print(f"  └{'─'*60}┘")

contrib_old_a1 = old_a1_score * 0.10
contrib_new_a1 = new_a1_score * 0.10
print(f"\n  贡献分：旧 {old_a1_score}×10% = {contrib_old_a1:.2f}    新 {new_a1_score:.2f}×10% = {contrib_new_a1:.2f}")
print(f"  {'↓ 下降' if contrib_new_a1 < contrib_old_a1 else '↑ 上升'} {contrib_new_a1 - contrib_old_a1:.2f} 分")

# ─────────────────────────────────────────────────────────────────────────────
# A2 封板时长
# ─────────────────────────────────────────────────────────────────────────────
section("A2 封板时长  [权重 8%]")
subsection("原始公式（动态分母）")
avail_old = n - first_hit
old_a2_score = old_a2(first_hit, total_seal, avail_old)
print(f"  分母 available = n - first_hit = {n} - {first_hit} = {avail_old}")
print(f"  公式：A2 = min(total_seal / available × 100, 100)")
print(f"  A2 = min({total_seal} / {avail_old} × 100, 100) = min({total_seal/avail_old*100:.2f}, 100)")
print(f"  原始得分 = {old_a2_score:.2f} 分")

subsection("新公式（固定分母241）")
print(f"  公式：A2 = 100/n × total_seal  = 100/{n} × {total_seal}")
print(f"  A2 = {100/n:.4f} × {total_seal} = {100/n*total_seal:.2f}")
new_a2_score = new_a2(total_seal)
print(f"  clamp后 = {new_a2_score:.2f} 分")

print(f"\n  ┌{'─'*60}┐")
print(f"  │  0              {total_seal}              {n}    min  │")
print(f"  │  └────────────[{'█'*int(total_seal/n*30):<30}]             │")
print(f"  │  0分                                              100分  │")
print(f"  │  A2={new_a2_score:.2f}分                                          │")
print(f"  └{'─'*60}┘")
print(f"  （注：群兴玩具触板后全程封板，但因首封在91分钟，全天仅有{n-first_hit}分钟可封板）")

contrib_old_a2 = old_a2_score * 0.08
contrib_new_a2 = new_a2_score * 0.08
print(f"\n  贡献分：旧 {old_a2_score:.2f}×8% = {contrib_old_a2:.2f}    新 {new_a2_score:.2f}×8% = {contrib_new_a2:.2f}")
print(f"  {'↓ 下降' if contrib_new_a2 < contrib_old_a2 else '↑ 上升'} {contrib_new_a2 - contrib_old_a2:.2f} 分")

# ─────────────────────────────────────────────────────────────────────────────
# A3 炸板次数
# ─────────────────────────────────────────────────────────────────────────────
section("A3 炸板次数  [权重 10%]")
print(f"  公式：A3 = max(0, 100 - 炸板次数 × 30)")
print(f"  炸板次数 = {zhaban}")
a3_score = max(0, 100 - zhaban * 30)
print(f"  A3 = max(0, 100 - {zhaban}×30) = {a3_score}")
print(f"  贡献分 = {a3_score}×10% = {a3_score*0.10:.2f}")

# ─────────────────────────────────────────────────────────────────────────────
# A4 最大开板
# ─────────────────────────────────────────────────────────────────────────────
section("A4 最大开板时长  [权重 7%]")
print(f"  公式：A4 = max(0, 100 - max_open × 3)")
print(f"  最大开板时长 = {max_open} 分钟")
a4_score = max(0, 100 - max_open * 3)
print(f"  A4 = max(0, 100 - {max_open}×3) = {a4_score}")
print(f"  贡献分 = {a4_score}×7% = {a4_score*0.07:.2f}")

# ─────────────────────────────────────────────────────────────────────────────
# B1 开盘涨幅
# ─────────────────────────────────────────────────────────────────────────────
section("B1 开盘涨幅  [权重 8%]")
subsection("原始公式（区间台阶）")
old_b1_score = old_b1(open_chg)
print(f"  规则：0~5%开=100分  >5%每超1%扣15分  <0%最高80分")
print(f"  开盘涨幅 = +{open_chg}% → 在0~5%区间")
print(f"  原始得分 = {old_b1_score} 分")

subsection("新公式（线性比例）")
print(f"  锚点：")
print(f"    -1% 开  → -10分")
print(f"     5% 开  → +50分")
print(f"    10% 开  → +100分")
print(f"  公式：B1 = -10 + (开盘涨幅 + 1) × 10")
print(f"  B1 = -10 + ({open_chg} + 1) × 10")
print(f"     = -10 + {open_chg+1} × 10")
print(f"     = -10 + {(open_chg+1)*10}")
print(f"     = {new_b1(open_chg):.2f}")
new_b1_score = new_b1(open_chg)
print(f"  clamp到[-100, 100] = {new_b1_score:.2f} 分")

# 可视化
print(f"\n  数值轴（-100 ──────────── 0 ──────────── 100）")
print(f"         -10%         -1%        5%         10%")
print(f"  ──┼────┼────┼────┼────┼────┼────┼────┼────┼──  min")
zero_pos = 50  # -1%对应50
p5_pos = zero_pos + (5 - (-1)) * 5  # 5%在80
p10_pos = zero_pos + (10 - (-1)) * 5  # 10%在105（截断）
cur_pos = zero_pos + (open_chg - (-1)) * 5
cur_pos_clamped = max(0, min(100, cur_pos))
print(f"             {'░'*zero_pos}████████████{'░'*(100-zero_pos-10)}  ← -1%到10%区间")
print(f"  {'░'*int(cur_pos_clamped)}█ ← +{open_chg}%  B1={new_b1_score:.1f}分")

contrib_old_b1 = old_b1_score * 0.08
contrib_new_b1 = new_b1_score * 0.08
print(f"\n  贡献分：旧 {old_b1_score}×8% = {contrib_old_b1:.2f}    新 {new_b1_score:.2f}×8% = {contrib_new_b1:.2f}")
print(f"  {'↓ 下降' if contrib_new_b1 < contrib_old_b1 else '↑ 上升'} {contrib_new_b1 - contrib_old_b1:.2f} 分")

# ─────────────────────────────────────────────────────────────────────────────
# B2 振幅
# ─────────────────────────────────────────────────────────────────────────────
section("B2 振幅  [权重 8%]")
subsection("原始公式（区间台阶）")
old_b2_score = old_b2(amp)
print(f"  规则：<0.5%=30分  0.5~5%=50分  5~15%=100分  >15%每超扣5分")
print(f"  振幅 = {amp}% → 在5~15%区间")
print(f"  原始得分 = {old_b2_score} 分")

subsection("新公式（一字板满分）")
print(f"  锚点：")
print(f"    <0.5%（一字板）→ 100分（最强信号）")
print(f"     0.5%          → 100分")
print(f"    每超1%          → -5分")
print(f"  公式：B2 = 100 - max(0, amp - 0.5) × 5  clamp [0, 100]")
b2_raw = 100 - max(0, (amp - 0.5) * 5)
new_b2_score = min(100, b2_raw)
print(f"  B2 = 100 - max(0, {amp} - 0.5) × 5")
print(f"     = 100 - max(0, {amp-0.5:.2f}) × 5")
print(f"     = 100 - {max(0, (amp-0.5)*5):.2f}")
print(f"     = {b2_raw:.2f}  clamp → {new_b2_score:.2f} 分")

print(f"\n  数值轴（0 ────────────────────────────── 100）")
print(f"       0%    0.5%           {amp}%            20%")
print(f"  ──┼────┼────┼─────────────┼────────────┼────┼──  min")
bar_len = min(40, int(new_b2_score / 100 * 40))
print(f"  100{'█'*bar_len}{'░'*(40-bar_len)}0")
print(f"  ├{'─'*bar_len}← B2={new_b2_score:.1f}分")

contrib_old_b2 = old_b2_score * 0.08
contrib_new_b2 = new_b2_score * 0.08
print(f"\n  贡献分：旧 {old_b2_score}×8% = {contrib_old_b2:.2f}    新 {new_b2_score:.2f}×8% = {contrib_new_b2:.2f}")
print(f"  {'↓ 下降' if contrib_new_b2 < contrib_old_b2 else '↑ 上升'} {contrib_new_b2 - contrib_old_b2:.2f} 分")

# ─────────────────────────────────────────────────────────────────────────────
# B3 / B4 / C系（不变）
# ─────────────────────────────────────────────────────────────────────────────
section("B3 均价偏离  [权重 12%]（不变）")
print(f"  涨停后均价 = {vwap:.2f}  涨停价 = {limit_up:.2f}")
print(f"  均价偏离 = {rel_vwap:.2f}%")
b3_score = sub['B3_vwap']
print(f"  B3得分  = {b3_score:.2f}")
print(f"  贡献分  = {b3_score:.2f}×12% = {b3_score*0.12:.2f}")

section("B4 分时平滑度  [权重 7%]（不变）")
print(f"  R²拟合度 + 最大回撤综合评分")
print(f"  平滑度 = {smooth:.1f}  最大回撤 = {max_dd:.2f}%")
b4_score = sub['B4_smooth']
print(f"  B4得分  = {b4_score:.2f}")
print(f"  贡献分  = {b4_score:.2f}×7% = {b4_score*0.07:.2f}")

section("C1 封板量比  [权重 12%]（不变）")
print(f"  封板期间成交量 / 触板后总成交量 × 100")
print(f"  C1 = {c1:.2f}% → 触板后全程封板，无开板")
c1_score = sub['C1_seal']
print(f"  C1得分  = {c1_score:.2f}")
print(f"  贡献分  = {c1_score:.2f}×12% = {c1_score*0.12:.2f}")

section("C2 触板量比  [权重 10%]（不变）")
print(f"  触板那一分钟成交量 / 触板前每分钟均量")
print(f"  C2 = {c2:.1f} 倍")
c2_score = sub['C2_ratio']
print(f"  C2得分  = {c2_score:.2f}")
print(f"  贡献分  = {c2_score:.2f}×10% = {c2_score*0.10:.2f}")

section("C3 后量占比  [权重 8%]（不变）")
print(f"  触板后成交量 / 全天成交量 × 100")
print(f"  C3 = {c3:.1f}%")
c3_score = sub['C3_post_vol']
print(f"  C3得分  = {c3_score:.2f}")
print(f"  贡献分  = {c3_score:.2f}×8% = {c3_score*0.08:.2f}")

# ─────────────────────────────────────────────────────────────────────────────
# 总分对比
# ─────────────────────────────────────────────────────────────────────────────
section("总分对比")
old_total = (sub['A1_first_hit']*0.10 + sub['A2_seal_dur']*0.08 +
             sub['A3_zhaban']*0.10 + sub['A4_max_open']*0.07 +
             sub['B1_open_chg']*0.08 + sub['B2_amp']*0.08 +
             sub['B3_vwap']*0.12 + sub['B4_smooth']*0.07 +
             sub['C1_seal']*0.12 + sub['C2_ratio']*0.10 +
             sub['C3_post_vol']*0.08)

new_total = (new_a1_score*0.10 + new_a2_score*0.08 +
             a3_score*0.10 + a4_score*0.07 +
             new_b1_score*0.08 + new_b2_score*0.08 +
             b3_score*0.12 + b4_score*0.07 +
             c1_score*0.12 + c2_score*0.10 +
             c3_score*0.08)

print(f"""
  ┌──────────────────────────────────────────────────────────────────┐
  │                    旧公式总分    新公式总分    差值              │
  ├──────────────────────────────────────────────────────────────────┤""")
items = [
    ('A1 首封时间', sub['A1_first_hit'], new_a1_score, 0.10),
    ('A2 封板时长', sub['A2_seal_dur'], new_a2_score, 0.08),
    ('A3 炸板次数', sub['A3_zhaban'], a3_score, 0.10),
    ('A4 最大开板', sub['A4_max_open'], a4_score, 0.07),
    ('B1 开盘涨幅', sub['B1_open_chg'], new_b1_score, 0.08),
    ('B2 振幅',     sub['B2_amp'], new_b2_score, 0.08),
    ('B3 均价偏离', sub['B3_vwap'], b3_score, 0.12),
    ('B4 分时平滑', sub['B4_smooth'], b4_score, 0.07),
    ('C1 封板量比', sub['C1_seal'], c1_score, 0.12),
    ('C2 触板量比', sub['C2_ratio'], c2_score, 0.10),
    ('C3 后量占比', sub['C3_post_vol'], c3_score, 0.08),
]
for name, old_s, new_s, w in items:
    diff = new_s - old_s
    arrow = '↑' if diff > 0 else '↓' if diff < 0 else '→'
    old_c = old_s * w
    new_c = new_s * w
    diff_c = new_c - old_c
    print(f"  │ {name:<12} 原始得分={old_s:>6.1f}  新公式={new_s:>6.1f}  {arrow}{abs(diff):.1f}   "
          f"贡献 {old_c:>5.2f}→{new_c:>5.2f}  {diff_c:>+5.2f} │")
print(f"  ├──────────────────────────────────────────────────────────────────┤")
print(f"  │ {'总分':<12}  旧 {old_total:>5.1f}              新 {new_total:>5.1f}          {'↓'+str(round(old_total-new_total,1)):>4}       │")
print(f"  └──────────────────────────────────────────────────────────────────┘")

print(f"""
  关键变化说明：
  A1：从80分→37.8分  ↓42.2  首封91分钟按241分摊比例降低
  A2：从100分→62.2分 ↓37.8  封板150分钟但只占全天62%
  B1：从100分→34.0分  ↓66.0  从"在0~5%区间得满分"改为"+3.4%对应34分"
  B2：从100分→66.1分  ↓33.9  从"5~15%区间满分"改为"振幅越大扣分越多"
""")
