# Step5 Stock Filter · 全面分析报告 v2

> 分析日期：2026-04-28
> 分析文件：`project/steps/step5_stock_filter.py`（912 行）
> 分析维度：运行流畅度 / 逻辑正确性 / 判断合理性 / 架构设计
> 前置审计：[AUDIT_step5_zhangting_strength.md](./AUDIT_step5_zhangting_strength.md)（首轮因子审计）、[ANALYSIS_step5_full.md](./ANALYSIS_step5_full.md)（首轮全量分析）
> 本报告基于最新代码重新审计，所有前次发现均已核对是否修复

---

## 一、版本变化总览（vs 首轮审计版）

| 变化项 | 旧版 | 新版 | 状态 |
|--------|------|------|------|
| `_compute_zhangting_strength()` 是否被 `run()` 调用 | ❌ 死代码 | ✅ L314 调用 | 已修复 |
| A3 炸板计数 | ❌ 永远=0 | ✅ L569-570 `zhaban_cnt += 1` | 已修复 |
| B3 均价偏离 | ❌ 永远=0 | ✅ L594 统计全部触板后成交 | 已修复 |
| A2 封板时长分母 | ❌ 固定 210 | ✅ L822 `available = n - first_hit` | 已修复 |
| C 量能结构 | C1/C2/C3 数学不独立 | ✅ L771-781 重构为三独立因子 | 已修复 |
| B4 平滑度 | ❌ R² + max_dd 区分度崩塌 | ✅ V2 三方案分场景组合 | 已修复 |
| `301` 前缀覆盖 | ❌ 缺失 | ✅ L31 `pure.startswith('301')` | 新增 |
| jiuyang list 为空时的 longtou fallback | ❌ 直接跳过 | ✅ L185-189 降级用 longtou | 新增 |
| `_compute_zhangting_strength` 结果写入 candidate | ❌ 未写入 | ✅ L333-341 `strength`/`B4_smoothness`/`_zts` | 新增 |
| `prev_close` 传递给 `_compute_zhangting_strength` | ❌ 无 | ✅ L313-314 从分钟数据 base_price 读取 | 新增 |
| 数据字段防御性取值 | ❌ `m['price']` / `m['volume']` | ❌ 仍然用 `m['price']` / `m['volume']` | **未修复** |

---

## 二、模块结构与数据流

```
step4_lianban_health ──→ lianban dict ──┐
                                       ├──→ step5_stock_filter.run() ──→ result dict ──→ step6_t1_prediction
jiuyangongshe.analysis ──→ jiuyang list ─┤
                                       │
MySQL stock_mins_data ──→ mysql_data ───┘
```

```
step5_stock_filter.py (912 行)
├── 涨跌停价计算（L19-88）
│   ├── _get_limit_ratio()              内部，涨跌停比例判断
│   ├── calculate_limit_price()         公开，涨停价
│   └── calculate_price_limits()        公开，涨跌停价
│
├── run() 主流程（L91-410）
│   ├── 阶段一：找 tier1 板块（L168-226）
│   │   ├── jiuyang_name_map O(1) 查表
│   │   ├── lianban_code_map 构建
│   │   ├── topnum 动态计算（P0-⑥修复）
│   │   └── longtou fallback（list 为空时降级）
│   ├── 阶段二：收集候选股（L228-274）
│   │   ├── tier1 成分股去重
│   │   ├── longtou fallback（L242-252）
│   │   └── 龙头标记 ban≥4（L277-284）
│   ├── 阶段三：筛选+形态检查+涨停强度（L286-342）
│   │   ├── 龙头排除
│   │   ├── MySQL 无数据排除
│   │   ├── _check_minute_pattern() 形态分类
│   │   └── _compute_zhangting_strength() 涨停强度 ⭐新增
│   └── 阶段四：综合评分排序+HARD_CAP 截断（L347-409）
│
├── _check_minute_pattern()（L413-464）
├── _q4_vol_pct()（L467-472）
│
└── _compute_zhangting_strength()（L482-911）⭐ 重大改版
    ├── A组：时间结构（A1~A4）
    ├── B组：价格形态（B1~B4 V2 三方案组合）
    ├── C组：量能结构（C1~C3 数学独立重设计）
    └── 综合评分（11因子加权 100%）
```

---

## 三、运行流畅度分析

### 3.1 时间复杂度

| 段落 | 操作 | 复杂度 | 评价 |
|------|------|--------|------|
| 建映射 | `lianban_code_map` + `jiuyang_name_map` | O(L + J) | ✅ 一次遍历 |
| 找 tier1 | jiuyang 板块 × 成分股查表 | O(J × K) | ✅ K=板块平均成分股 |
| 收集候选 | tier1 × 成分股 | O(T × K) | ✅ |
| 形态检查 | 每只候选 241 条分钟遍历 | O(C × 241) | ✅ C=候选股数 |
| **涨停强度** | 每只候选 **7 次** 分钟遍历 | O(C × 241 × 7) | ⚠️ 见下文 |
| 排序 | sort | O(C log C) | ✅ |

**涨停强度的 7 次遍历分解**：

| # | 遍历 | 行号 | 可合并？ |
|---|------|------|---------|
| 1 | A1 首次触板 | L540-544 | 可合并 |
| 2 | A2 封板总时长 | L548-551 | 可合并 |
| 3 | A3+A4 炸板+最长开板 | L559-573 | 已合并 ✅ |
| 4 | B3 触板后均价 | L594-596 | 可合并 |
| 5 | B4 方案①②③ 各自遍历 pre_prices/pre_volumes | L640-725 | 部分合并 |
| 6 | 最大回撤 | L719-725 | 可合并 |
| 7 | C1/C2/C3 | L784-800 | 各自 sum() |

**实际影响**：241 条数据 × 7 = 1687 次操作/股，Python 循环约 10μs/次 ≈ 17ms/股。300 只候选 ≈ 5 秒。**可接受但不够优雅。**

**优化建议**：A1+A2 可在 A3/A4 的同一循环中完成（只需加 2 个变量），B3+C1/C3 可合并（都是 range(first_hit, n) 的遍历）。

### 3.2 空间复杂度

| 对象 | 大小 | 评价 |
|------|------|------|
| prices/volumes/amounts | 各 241 元素 | ✅ |
| lianban_code_map | ~200-500 条 | ✅ |
| all_candidates | ~100-300 条，每条含 `_zts` 子 dict | ✅ 但 `_zts` 嵌套较深 |
| candidates_out | 同上 | ✅ |

**`_zts` 嵌套**：每个 candidate 内含完整 `_compute_zhangting_strength()` 返回 dict（含 `_sub_scores` 子 dict），约 20 个字段。300 只候选 × 20 字段 × 8 bytes ≈ 48KB。无压力。

### 3.3 健壮性 / 容错

| 检查项 | 位置 | 结论 |
|--------|------|------|
| jiuyang 为空 | L103-105 | ✅ |
| jiuyang list 为空 → longtou fallback | L185-189, L242-252 | ✅ 新增 |
| MySQL 无数据 | L300-303 | ✅ |
| 分钟数据不足 60 条 | L414, L511 | ✅ |
| base_price 为 0 | L428-430, L519-520 | ✅ |
| prev_close ≤ 0 | L59-60 | ✅ |
| 多处除零 | `or 1` / `if > 0` / `max(..., 1)` | ✅ |
| 代码格式兼容（6 种） | L161 | ✅ |
| ban_tag 解析失败 | L283 try-except | ✅ |

---

## 四、🔴 运行时崩溃风险

### 🔴 Crash-1：`_check_minute_pattern` 和 `_compute_zhangting_strength` 中 `m['price']` / `m['volume']` 无防御性取值

**位置**：L417-418、L515-516

```python
# L417-418
prices  = [float(m['price']) for m in mins]    # ← KeyError if 'price' missing
volumes = [int(m['volume']) for m in mins]      # ← KeyError if 'volume' missing

# L515-516 (同一问题)
prices  = [float(m['price']) for m in mins]
volumes = [int(m['volume']) for m in mins]
```

注意 L517 `amounts = [float(m.get('amount', 0)) for m in mins]` 已经用了 `.get()`，但 price/volume 没有。

**触发条件**：MySQL 分钟数据中某些分钟缺失 `price` 或 `volume` 字段（数据采集异常时可能发生）。

**影响**：`KeyError` → 整个 step5 崩溃，当天全部候选股丢失。

**修复**：

```python
prices  = [float(m.get('price', 0)) for m in mins]
volumes = [int(m.get('volume', 0)) for m in mins]
```

**严重度**：🔴 高。上游数据质量不可控，这是最可能导致生产环境崩溃的问题。

---

### 🟡 Crash-2：`find_mysql_key` 清洗逻辑对某些代码格式不完整

**位置**：L161

```python
clean = code.replace('sz', '').replace('sh', '').replace('SZ', '').replace('SH', '').replace('.SZ','').replace('.SH','').strip()
```

当前覆盖 6 种格式：`sz`/`sh`/`SZ`/`SH`/`.SZ`/`.SH`

未覆盖的已知格式（来自不同数据源）：

| 未覆盖格式 | 示例 | 清洗结果 | 问题 |
|-----------|------|---------|------|
| `Sz` / `Sh` | `Sz000720` | `z000720` 或 `000720`（取决于 replace 顺序）| `sz` 替换不影响 `Sz` |
| `bj` 前缀 | `bj830799` | `bj830799` | 北交所代码不识别 |

不过 L161 的 `replace('sz')` 会匹配到 `Sz` 中的 `z` 吗？不会——`str.replace()` 是精确匹配子串，`'Sz'.replace('sz', '')` 不变。

实际上 `'Sz000720'.replace('sz', '')` = `'Sz000720'`（Python 区分大小写），然后 `.replace('SZ', '')` 也不变。最终 clean = `'Sz000720'`，匹配不到 MySQL 的 `000720.SZ`。

**影响**：低。当前数据源（jiuyang）观察到的格式是 `sz000720`（全小写），未出现过 `Sz` 混合大小写。但作为防御性编程，建议改用正则：

```python
import re
clean = re.sub(r'^(sz|sh|SZ|SH)\.?|\.(SZ|SH)$', '', code).strip()
```

---

## 五、逻辑正确性分析

### 5.1 `run()` 主流程

#### ✅ 阶段一：找 tier1 板块（L168-226）

**改进**：新增 longtou fallback（L185-189）。当 jiuyang 板块的 `list` 为空但 `longtou` 存在时，用 longtou 单只撑起候选池。

| 检查点 | 结论 |
|--------|------|
| 板块名 `.strip()` | ✅ L173 |
| GARBAGE_PLATES 过滤 | ✅ L178 |
| 成分股代码清洗 | ✅ L195 |
| topnum 动态计算 | ✅ L194-202 |
| 龙头选择 `max(by ban_height)` | ✅ L207 |
| 排序 topnum 降序 | ✅ L224 |

**🟡 注意**：L193 `if stocks and stocks[0].get('code')` 作为 `in_lb_codes` 计算的前置条件。当走了 longtou fallback（L185-189）时，`stocks` 只包含 1 只龙头股的 code，而龙头股**必然**在 lianban 中（因为 topnum 是从 lianban 统计的），所以 `in_lb_codes` 长度 = 1。而 L202 `len(in_lb_codes) < 5` 会把这种板块过滤掉。

**这意味着 longtou fallback 实际上永远不会通过 topnum≥5 的阈值。** longtou fallback 的唯一作用是避免 `stocks` 为空时 `for s in stocks` 报错，但对 tier1 筛选没有实际贡献。这是一个**逻辑死路径**——不会报错，但也不会产生 tier1 结果。

> **判断**：这不是 bug，是设计局限。如果 jiuyang 板块的 list 确实为空（数据缺失），它不应该成为 tier1，因为连 5 只连板股都没有。longtou fallback 在阶段二（L242-252）更有意义——阶段二不要求 topnum，只要有 tier1 就收其成分股。

#### ✅ 阶段二：收集候选股（L228-274）

**改进**：新增 longtou fallback（L242-252），当 `plate_doc.list` 为空时用 longtou 单只。

| 检查点 | 结论 |
|--------|------|
| jiuyang_name_map O(1) 查表 | ✅ L236 |
| 去重 `raw in all_candidates` | ✅ L256 |
| reason 截断 80 字符 | ✅ L267 |
| longtou fallback | ✅ L242-252 |

**🟡 多板块共振问题（首轮提出，仍未改）**：同一只股在多个 tier1 板块时只保留第一个。`plate_weight` 只取第一个板块的 topnum。例如一只票同时在 topnum=12 和 topnum=6 的板块里，它只拿到 6 的权重。

**影响**：中。对于"多板块共振"这种强信号，当前设计会低估其强度。

**建议**：`plate_weight = max(已有值, 新值)` 而非直接覆盖。

#### 🟡 阶段三：龙头标记（L277-284）

```python
for code, info in all_candidates.items():
    if info['is_lianban']:
        try:
            bh = int(info['ban_tag'].replace('ban', '').replace('b', ''))
            if bh >= 4:
                info['is_longtou'] = True
        except (ValueError, AttributeError):
            pass
```

**问题**：`ban_tag` 的解析用 `replace('ban','').replace('b','')`，但模块内已有 `_ban_height_tag()`（L134-140）用正则做更健壮的解析。两处逻辑不一致。

实际场景：如果 tag = `"3板"`（中文），`replace('ban','')` 不会匹配 → ValueError → try-except 兜底 → **不标记为龙头**。但 `_ban_height_tag("3板")` 能正确返回 3。虽然 3 < 4 不会标记为龙头，但如果 tag = `"四板"` 或 `"ban四"`，行为会不一致。

**影响**：低（被 try-except 兜住，不会崩溃），但逻辑不一致。建议统一用 `_ban_height_tag`：

```python
bh = _ban_height_tag(info['ban_tag'])
if bh >= 4:
    info['is_longtou'] = True
```

#### ✅ 阶段三新增：涨停强度计算（L305-341）⭐

```python
if not exclude and mysql_key:
    mins = mysql_stocks[mysql_key]
    form = _check_minute_pattern(mins)
    prev_close = float(mins[0].get('base_price', 0)) if mins else 0.0
    zts = _compute_zhangting_strength(mins, code, prev_close)
```

**这修复了上一版最大的架构问题**——`_compute_zhangting_strength()` 从死代码变成了活跃代码。

| 检查点 | 结论 |
|--------|------|
| 只对非排除股计算 | ✅ `if not exclude` |
| prev_close 从分钟数据读取 | ✅ L313 |
| 结果写入 candidate dict | ✅ L333-341 |
| `_zts` 完整因子数据保留 | ✅ L341 供 step6 使用 |
| `is_on_limit` 字段取自 zts | ✅ L338 |

**🟡 小问题**：L313 `prev_close = float(mins[0].get('base_price', 0)) if mins else 0.0`。如果 `base_price` 为 0 或 None（数据异常），`prev_close = 0.0`，传入 `_compute_zhangting_strength` 后：

- L527 `calculate_limit_price(0, code)` → L59-60 返回 `0.0`
- L532 `is_on_limit` 检查 `abs(prices[i] - 0) < 0.005` → 只要价格接近 0 就"涨停"

**极端情况**：如果 base_price=0 且首分钟价格也是 0（停牌），`first_hit=0` → 开盘即封板 → smoothness=100 → 虚假高分。

**建议**：L313 加 `prev_close = max(prev_close, 0.01)` 或在 `_compute_zhangting_strength` 入口处加保护。

#### ✅ 阶段四：综合评分排序（L347-409）

评分公式无变化，逻辑正确。L383 始终排序后 L385 再截断。✅

### 5.2 `_check_minute_pattern()`（L413-464）

与上一版完全相同，首轮分析中的问题仍然存在：

| 问题 | 状态 | 影响 |
|------|------|------|
| 形态分类互斥（早盘拉升+尾盘砸盘只返回前者） | 未改 | 低（标记不排除，只影响排序） |
| q4 对数据长度敏感（<240 条时含义失真） | 未改 | 低（L462 `q4_vol_pct` 已有 `len >= 240` 保护） |
| amp=2.99 vs amp=3.01 导致形态跳变 | 未改 | 低 |

### 5.3 `_compute_zhangting_strength()`（L482-911）

#### 5.3.1 A 组：时间结构（A1~A4）

| 因子 | 状态 | 评价 |
|------|------|------|
| A1 首次触板 | ✅ | 正确 |
| A2 封板时长 | ✅ 已修 | 分母 = `n - first_hit`，正确 |
| A3 炸板次数 | ✅ 已修 | L569-570 正确递增 |
| A4 最长开板 | ✅ | 与 A3 共用扫描，正确 |

#### 5.3.2 B 组：价格形态（B1~B4）

| 因子 | 状态 | 评价 |
|------|------|------|
| B1 开盘涨幅 | ✅ | 正确 |
| B2 振幅 | ✅ | 正确 |
| B3 均价偏离 | ✅ 已修 | 统计全部触板后成交，有区分度 |
| B4 平滑度 V2 | ✅ 重写 | 见下方详细分析 |

#### B4 V2 详细分析

**架构**：三方案分场景组合——

| 场景 | 条件 | 方案①加速斜率 | 方案②量能递增 | 方案③尾盘集中度 |
|------|------|-------------|-------------|---------------|
| early | first_hit ≤ 60 | 60% | 40% | — |
| mid | 60 < fh ≤ 180 | 40% | 40% | 20%（mid 评分） |
| squeeze | fh > 180 | — | 35% | 65%（squeeze 评分） |

**方案① 加速斜率比（L637-687）**：

前后两半段分别做线性回归，归一化后比较斜率变化。

| 场景 | 评分 | 合理性 |
|------|------|--------|
| 前后均横盘 | 50 分 | ✅ 中性 |
| 前横盘后有方向 | 60 分 | ✅ |
| 前跌后涨 | 100 分 | ✅ V 型反转=强信号 |
| 前后均涨，ratio=2.0 | 100 分 | ✅ 加速=强 |
| 前后均涨，ratio=1.0 | 33 分 | ⚠️ 匀速偏低，风格选择 |
| 前后均涨，ratio=0.5 | 0 分 | ✅ 减速=弱 |
| 前涨后跌/横 | 100-ratio×60 | ✅ 减速扣分 |

**方案② 量能递增比（L689-695）**：

```python
b4_vol_score = min(b4_vol_ratio / 1.2 * 100, 100)
```

阈值 1.2 合理——后半段量比前半段多 20% 以上才满分。✅

**方案③ 尾盘集中度（L697-716）**：

两套评分：mid 用 `b4_squeeze_score`（低 ratio=健康），squeeze 用 `b4_squeeze_score_for_squeeze`（高 ratio=squeeze 特征）。✅

**权重归一化检查**：

| 场景 | scheme2_valid | scheme3_valid | w1 | w2 | w3 | 总和 |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| early, 两者 valid | ✅ | — | 0.60 | 0.40 | — | **1.0** ✅ |
| early, 两者 invalid | ❌ | — | 1.0 | 0 | — | **1.0** ✅ |
| mid, 全 valid | ✅ | ✅ | 0.40 | 0.40 | 0.20 | **1.0** ✅ |
| mid, w2 invalid | ❌ | ✅ | 0.60 | 0 | 0.20 | **0.80** ⚠️ |
| mid, w3 invalid | ✅ | ❌ | 0.60 | 0.40 | 0 | **1.0** ✅ |
| mid, 全 invalid | ❌ | ❌ | 1.0 | 0 | 0 | **1.0** ✅ |
| squeeze, 全 valid | ✅ | ✅ | — | 0.35 | 0.65 | **1.0** ✅ |
| squeeze, w2 invalid | ❌ | ✅ | — | 0 | 0.65 | **0.65** ⚠️ |
| squeeze, w3 invalid | ✅ | ❌ | — | 0.35 | 0 | **0.35** ⚠️ |
| squeeze, 全 invalid | ❌ | ❌ | — | 0 | 0 | 兜底 40 分 ✅ |

**⚠️ mid 场景 w2 invalid + w3 valid 时，权重总和 0.80**

具体场景：`first_hit=120`，`n_pre=120`，`half=60`（≥5 → scheme2_valid=True）。但如果 `pre_volumes` 数据异常导致 `vol1=0`，`b4_vol_score=0`，但 `scheme2_valid` 仍然为 True（只检查 `half >= 5`，不检查数据有效性）。所以这个情况在正常数据下不会触发。

**⚠️ squeeze 场景 w2 invalid + w3 valid 时，权重总和 0.65**

`first_hit > 180` 意味着 `n_pre > 180`，`half > 90`（scheme2_valid 必为 True），`tail = 30`（scheme3_valid 必为 True）。所以在正常数据下**永远不会触发**。

**结论**：权重不归一的情况在正常数据下不会触发，但作为防御性编程建议加归一化保护（一行代码）。

#### 5.3.3 C 组：量能结构（C1~C3）⭐ 重设计

**新版三因子（L771-800）**：

```python
C1 = seal_vol / vol_sum           # 封板量/全天量（封板多=封得实）
C2 = hit_vol / pre_avg            # 触板瞬间量比（爆发力）
C3 = pre_vol / vol_sum            # 触板前量/全天量（建仓充分度）
```

**数学独立性验证**：

设 `post_vol = sum(volumes[first_hit:])`，`pre_vol = sum(volumes[:first_hit])`，`seal_vol = 封板分钟量`。

- C1 = seal_vol / vol_sum
- C2 = hit_vol / (pre_vol / first_hit) = hit_vol × first_hit / pre_vol
- C3 = pre_vol / vol_sum

三个变量分别从"封板期"/"触板瞬间"/"触板前期"三个独立时间段取值，且使用了不同的归一化基准（vol_sum / pre_avg / vol_sum）。

**C1 和 C3 的关系**：C1 + C3 ≤ 1（因为 seal_vol + pre_vol ≤ vol_sum，但 seal_vol 可能 < post_vol）。两者不共线（一只票可以封板量高但触板前量低 = 板上吸筹型；也可以触板前量高但封板量低 = 提前建仓型）。

**结论**：✅ 新三因子数学独立，无冗余。相比旧版（C1 = C3/C2 的派生量）是重大改进。

**评分逻辑**：

| 因子 | 评分公式 | 满分条件 | 评价 |
|------|---------|---------|------|
| C1 | `c1_seal_pct / 50 × 100` | >50% | ✅ 合理 |
| C2 | `limit_ratio / 5 × 100` | >5 倍 | ✅ 合理 |
| C3 | `(c3_pre_touch_pct - 30) / 30 × 100` | >60% | ⚠️ 见下文 |

**C3 评分问题（L865-866）**：

```python
c3_score = max(0, min(100, (c3_pre_touch_pct - 30) / 30 * 100))
```

| c3_pre_touch_pct | c3_score | 场景 |
|-------------------|----------|------|
| 30% | 0 分 | 触板前只占 30% |
| 45% | 50 分 | 中等 |
| 60% | 100 分 | 建仓充分 |
| 90% | 100 分 | cap |

问题：c3_pre_touch_pct < 30% 时得 0 分。但一只早盘秒封板的票，触板前只有 1 分钟，pre_vol/total_vol 可能只有 1-2%，得 0 分。**秒封板应该是强信号，但 C3 给了 0 分。**

不过这只被 C3 自身的 8% 权重影响：`0 × 0.08 = 0`，损失 8 分。而 A1（封板时间）会给秒封板 100 分（权重 10%），所以综合来看影响不大。

**判断**：可接受。C3 衡量的是"主力提前建仓充分度"，秒封板确实没有提前建仓过程，0 分在逻辑上说得通。

---

## 六、判断合理性分析

### 6.1 核心业务判断

| 判断 | 代码体现 | 评价 |
|------|---------|------|
| 以 jiuyang 为板块标准 | L113-114 | ✅ |
| topnum≥5 为 tier1 | L202 | ⚠️ 固定阈值 |
| 龙头 ban≥4 排除 | L281 | ✅ |
| 一字板/尾盘砸盘标记不排除 | L316-317 | ✅ |
| HARD_CAP=60 | L287 | ⚠️ 固定值 |
| 涨停强度结果写入 candidate | L333-341 | ✅ 新增 |
| C 量能因子数学独立 | L771-800 | ✅ 重设计 |
| B4 分场景组合 | L728-757 | ✅ 重设计 |
| jiuyang list 为空时 longtou fallback | L185-189, L242-252 | ✅ 新增 |

### 6.2 `run()` 综合评分与 `_compute_zhangting_strength()` 综合评分的关系

现在有**两套独立的评分系统**：

| 评分 | 用途 | 使用者 | 因子 |
|------|------|--------|------|
| `_composite_score` | 候选股排序 | `run()` L360-378 | ban_height×2 + plate_weight×0.5 + FORM_SCORES + is_lianban + f30 |
| `score` | 涨停强度评估 | `_compute_zhangting_strength()` L868-870 | 11 因子加权 |

`_composite_score` 的特点是：简单、快速、不需要计算涨停强度。它在 `run()` 的排序阶段使用，而此时涨停强度已经被计算了（L314）。

**🟡 问题**：`_composite_score`（L360-378）没有利用已计算好的 `strength`（涨停强度分）。涨停强度是一个更全面的评估（11 因子），但排序时完全没用到它。

**建议**：在 `_composite_score` 中加入 strength 的权重：

```python
score += c.get('strength', 0) * 0.03  # 涨停强度微调
```

这样涨停强度高的票会排在前面，且不影响现有排序结构。

### 6.3 低吸策略一致性（延续首轮分析）

| 低吸核心要求 | step5 实现 | 一致性 |
|-------------|-----------|--------|
| 找到有启动信号的个股 | `is_lianban=True` 加 1.5 分 | ⚠️ 已涨停的票不适合低吸 |
| 排除追高风险 | 龙头 ban≥4 排除 | ✅ |
| 评估涨停质量 | `_compute_zhangting_strength` | ✅ 新增 |
| 评估回调机会 | 无 | ❌ 缺失 |
| 评估支撑位 | 无 | ❌ 缺失 |

**新增 `_compute_zhangting_strength` 后**，step5 现在能为已涨停的票提供涨停质量评估。但"低吸"需要的是**未涨停但属于强板块的跟风股**的评估——这些票 `first_hit=-1`，`_compute_zhangting_strength` 返回空 dict（L511-512），完全没有评分。

---

## 七、代码质量分析

### 7.1 优点

| 维度 | 评价 |
|------|------|
| 函数职责清晰 | ✅ 4 个主要函数各司其职 |
| 修复历史可追溯 | ✅ P0-①~P0-⑥、P1-1、P2-④ 等注释 |
| 防御性编码 | ✅ 大量 `or 1`、try-except 兜底 |
| 无外部依赖 | ✅ 只用标准库 |
| O(n²) 优化 | ✅ dict map 替代嵌套遍历 |
| DRY 重构 | ✅ `_get_limit_ratio` 抽取公共逻辑 |
| C 量能因子数学独立 | ✅ 新版三因子设计 |
| B4 分场景架构 | ✅ early/mid/squeeze 三策略 |
| 诊断字段丰富 | ✅ `_sub_scores`、`B4_scheme`、`B4_accel_ratio` 等 |
| longtou fallback | ✅ jiuyang list 为空时降级 |

### 7.2 代码异味

| 问题 | 位置 | 严重度 |
|------|------|--------|
| 5 个内部函数定义在 `run()` 内 | L134-166 | 低（每次调用重建闭包） |
| `import re` 在函数内部 | L136 | 低（Python 缓存但风格不规范） |
| `_parse_ban_height` 与 `_ban_height_tag` 重复 | L150-152 | 低 |
| 取值风格不统一 `.get()` vs `[]` | L417 vs L517 | 中 |
| `FORM_SCORES` 定义在 `run()` 内 | L351-358 | 低 |
| 魔法数字 | L202 `5`, L281 `4`, L287 `60` | 中 |
| `is_on_limit` 返回值未写入 `result` dict | L530 | 低（但在 candidate dict 中 L338 有引用，zts 中无此字段） |

### 7.3 `is_on_limit` 字段引用问题

L338：

```python
'is_on_limit': zts.get('is_on_limit', False),
```

但 `_compute_zhangting_strength()` 的返回 dict（L872-911）中**没有 `is_on_limit` 字段**。它只是一个内部函数（L530-532），不写入返回值。

结果：`zts.get('is_on_limit', False)` 永远返回 `False`。

**影响**：如果 step6 使用 `candidate['is_on_limit']` 判断某只票当天是否触及涨停板，会得到错误结果（全部 False）。

**修复**：在 `_compute_zhangting_strength` 返回 dict 中加入：

```python
'is_on_limit': first_hit >= 0,  # 是否触板（简化判断）
```

---

## 八、问题汇总

### 🔴 P0 — 必须修复

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| P0-1 | `m['price']`/`m['volume']` 无防御性取值 | L417-418, L515-516 | 数据缺失时 KeyError 崩溃 |
| P0-2 | `is_on_limit` 字段在 zts 返回值中不存在 | L338 引用 → zts 无此字段 | step6 读取时永远 False |

### 🟡 P1 — 应该修复

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| P1-1 | 龙头标记用脆弱的 `replace` 而非已有的 `_ban_height_tag` | L280 | 格式不匹配时漏标 |
| P1-2 | `_composite_score` 未利用已计算的 `strength` | L360-378 | 排序未使用涨停强度信息 |
| P1-3 | `topnum≥5` 固定阈值 | L202 | 情绪低迷时 candidates 为空 |
| P1-4 | 多板块共振信号丢失 | L256 | 只保留第一个板块归属 |
| P1-5 | `prev_close=0` 时涨停强度计算虚高 | L313 → L527 | base_price=0 → 虚假涨停 |
| P1-6 | longtou fallback 在阶段一无法通过 topnum 阈值 | L185-189 vs L202 | 逻辑死路径（不会报错但无实际效果） |

### ⚠️ P2 — 建议优化

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| P2-1 | 5 个内部函数应提到模块级 | L134-166 | 每次调用 `run()` 重建闭包 |
| P2-2 | `import re` 应移到文件顶部 | L136 | 规范 |
| P2-3 | `_parse_ban_height` 与 `_ban_height_tag` 重复 | L150-152 | DRY |
| P2-4 | 取值风格不统一 | L417 vs L517 | 一致性 |
| P2-5 | `FORM_SCORES` 应为模块常量 | L351-358 | 微优化 |
| P2-6 | A1+A2 可合并到 A3/A4 的同一循环中 | L540-551 | 性能（7 次遍历→5 次） |
| P2-7 | squeeze 场景权重不归一保护 | L754-755 | 正常数据不触发 |
| P2-8 | 缺少单元测试 | — | 回归风险 |

---

## 九、与前次审计对比

| 维度 | 首轮评分 | 本轮评分 | 变化 |
|------|---------|---------|------|
| 运行流畅度 | 4/5 | 4/5 | 持平（新增涨停强度计算增加耗时但可接受） |
| 逻辑正确性 | 3.5/5 | **4/5** | ↑ A3/B3/A2 死因子修复，C 量能独立重设计 |
| 判断合理性 | 3.5/5 | **4/5** | ↑ B4 三方案分场景，涨停强度写入 candidate |
| 架构设计 | 3/5 | **4/5** | ↑ `_compute_zhangting_strength` 不再是死代码 |
| 代码质量 | 4/5 | **4/5** | 持平 |
| 可维护性 | 3.5/5 | **3.5/5** | 持平（仍缺测试，新增诊断字段有助调试） |

**综合评价**：从首轮的 3.7/5 提升到 **4.1/5**。核心改进是：
1. `_compute_zhangting_strength` 从死代码变成活跃代码
2. C 量能因子从数学不独立变为三独立因子
3. B4 从 R²+max_dd 区分度崩塌变为三方案分场景组合
4. longtou fallback 增强了数据缺失时的鲁棒性

剩余 2 个 P0 问题（字段防御性取值、`is_on_limit` 字段缺失）修复后，可达到 **4.5/5**。

---

## 十、修复优先级建议

```
第一优先级（防崩溃）：
  1. P0-1: m.get('price', 0) / m.get('volume', 0) 替代 m['price'] / m['volume']
  2. P0-2: _compute_zhangting_strength 返回 dict 加入 'is_on_limit': first_hit >= 0

第二优先级（逻辑完善）：
  3. P1-1: L280 改用 _ban_height_tag(tag)
  4. P1-5: L313 加 prev_close = max(prev_close, 0.01)

第三优先级（增强排序）：
  5. P1-2: _composite_score 加入 strength 微调

第四优先级（代码质量）：
  6. P2-1: 内部函数提到模块级
  7. P2-3: 删除 _parse_ban_height 冗余
  8. P2-7: squeeze 场景权重归一化保护
```

---

*分析完成。共发现 2 个 P0 级、6 个 P1 级、8 个 P2 级问题。相比首轮审计，P0 从 3 个减少到 2 个，且严重度从"逻辑错误+架构缺陷"降低到"防御性编码+字段缺失"。*
