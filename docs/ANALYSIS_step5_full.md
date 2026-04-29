# Step5 Stock Filter · 全面分析报告

> 分析日期：2026-04-28
> 分析文件：`project/steps/step5_stock_filter.py`（729 行）
> 分析维度：运行流畅度 / 逻辑正确性 / 判断合理性 / 架构设计
> 前置审计：[AUDIT_step5_zhangting_strength.md](./AUDIT_step5_zhangting_strength.md)（已覆盖的涨停强度因子问题不重复展开）

---

## 一、模块定位与数据流

```
step4_lianban_health (连板健康度)
        │
        ▼ lianban dict
step5_stock_filter ◄── jiuyang list (韭研公社题材)
        │         ◄── mysql_data dict (分钟行情)
        ▼ result dict
step6_t1_prediction (T+1预判)
```

| 方向 | 数据 | 来源 |
|------|------|------|
| 输入 1 | `lianban` | MongoDB `vip_fupanwang.lianban_data` |
| 输入 2 | `jiuyang` | MongoDB `jiuyangongshe.analysis` |
| 输入 3 | `mysql_data` | MySQL `stock_mins_data` |
| 输出 | `result` dict | `candidates` / `excluded` / `tier1_detail` 等 |
| 消费方 | step6 | 读取 `result['candidates']` 做个股预测 |

**唯一外部接口**：`run(lianban, jiuyang, mysql_data, **kwargs) -> Dict[str, Any]`

---

## 二、模块结构概览

```
step5_stock_filter.py (729 行)
├── 涨跌停价计算（L19-88）
│   ├── _get_limit_ratio()         ← 内部，判断涨跌停比例
│   ├── calculate_limit_price()    ← 公开（但无外部调用者）
│   └── calculate_price_limits()   ← 公开（但无外部调用者）
├── run() 主流程（L91-367）
│   ├── 第一阶段：找 tier1 板块（L167-210）
│   ├── 第二阶段：收集候选股（L212-254）
│   ├── 第三阶段：筛选+形态检查（L256-299）
│   └── 第四阶段：综合评分排序+截断（L304-367）
├── _check_minute_pattern()（L370-421）
├── _q4_vol_pct()（L424-429）
└── _compute_zhangting_strength()（L439-729）
    ├── A组：时间结构（A1~A4）
    ├── B组：价格形态（B1~B4）
    ├── C组：量能结构（C1~C3）
    └── 综合评分（11因子加权）
```

---

## 三、运行流畅度分析

### 3.1 时间复杂度

| 段落 | 操作 | 复杂度 | 评价 |
|------|------|--------|------|
| 建立映射（L112-131） | `lianban_code_map` 构建 | O(L) | ✅ 一次遍历，L=连板股数 |
| 找 tier1（L170-208） | 遍历 jiuyang 板块 × 成分股查表 | O(J × K) | ✅ 查表 O(1)，K=板块平均成分股 |
| 收集候选（L216-244） | 遍历 tier1 × 成分股 | O(T × K) | ✅ T=tier1数量，K 同上 |
| 形态检查（L276-283） | 分钟数据遍历 | O(241 × C) | ✅ C=候选股数 |
| 综合评分排序（L340） | sort | O(C log C) | ✅ C≤300 |
| **涨停强度因子** | 分钟数据多次遍历 | O(241 × 6) | ⚠️ 同一只股遍历 6 次可合并 |

**总体评价**：主流程 `run()` 时间复杂度约 O(J×K + C×241)，对 A 股全市场（~5000 只）完全在可接受范围。单只股的 `_compute_zhangting_strength()` 多次遍历分钟数据，但由于只对候选股计算，不影响整体性能。

### 3.2 空间复杂度

| 对象 | 大小 | 评价 |
|------|------|------|
| `prices / volumes / amounts` | 各 241 个元素 | ✅ 极小 |
| `lianban_code_map` | ~200-500 条 | ✅ |
| `all_candidates` | ~100-300 条 | ✅ |
| 中间列表（`in_lb_codes` 等） | 临时创建后释放 | ✅ |

**结论**：内存使用无压力，全部在单机可承受范围内。

### 3.3 健壮性 / 容错

| 检查项 | 代码位置 | 结论 |
|--------|---------|------|
| jiuyang 为空 | L103-105 | ✅ 优雅返回 |
| MySQL 无数据 | L270-273 | ✅ 标记排除 |
| 分钟数据不足 60 条 | L468-469 | ✅ 返回空 dict |
| base_price 为 0 | L476-477, L386-387 | ✅ fallback 到 open_price |
| prev_close ≤ 0 | L59-60 | ✅ 返回 0.0 |
| 除零保护 | `or 1` / `if > 0` / `max(..., 1)` | ✅ 多处覆盖 |
| 代码格式兼容 | L160 `.SZ/.SH/sz/sh/SZ/SH` | ✅ 六种格式 |
| `ban_tag` 解析失败 | L249-254 try-except | ✅ 静默跳过 |

### 3.4 🔴 运行时崩溃风险

#### 🔴 Crash-1：`_check_minute_pattern` 数据字段缺失

**位置**：L374-375

```python
prices  = [float(m['price']) for m in mins]
volumes = [int(m['volume']) for m in mins]
```

如果 `mins` 中某个元素缺少 `price` 或 `volume` 字段（MongoDB 数据不完整的情况），会直接抛 `KeyError` 崩溃。

**风险等级**：中（依赖上游数据质量）

**建议**：加防御性取值：

```python
prices  = [float(m.get('price', 0)) for m in mins]
volumes = [int(m.get('volume', 0)) for m in mins]
```

#### 🔴 Crash-2：`_compute_zhangting_strength` 同理

**位置**：L472-474

```python
prices  = [float(m['price']) for m in mins]
volumes = [int(m['volume']) for m in mins]
amounts = [float(m.get('amount', 0)) for m in mins]  # ← amount 已用 get，但 price/volume 没有
```

`amount` 用了 `.get('amount', 0)`，但 `price` 和 `volume` 直接用 `m['price']`，风格不一致且存在崩溃风险。

---

## 四、逻辑正确性分析

### 4.1 `run()` 主流程

#### ✅ 第一阶段：找 tier1 板块（L167-210）

**逻辑**：遍历 jiuyang 每个板块 → 统计成分股中有多少只在 lianban 里（= topnum）→ topnum ≥ 5 且非垃圾板块 → 纳入 tier1

**正确性验证**：

| 检查点 | 结论 |
|--------|------|
| 板块名去空格 `.strip()` | ✅ L171 |
| GARBAGE_PLATES 过滤 | ✅ L176-177 |
| 成分股代码清洗 | ✅ L184-187，去 sz/sh 前缀 |
| topnum 计算 | ✅ L189 = `len(in_lb_codes)` |
| 龙头选择 | ✅ L194 `max(by ban_height)` |
| 排序 | ✅ L208 按 topnum 降序 |

**🟡 逻辑隐患**：

- **topnum=5 的阈值是否合理？** 在市场情绪低迷时（连板股 < 50 只），可能没有任何板块达到 topnum≥5，导致 candidates 为空。建议考虑动态阈值（如 topnum ≥ max(3, 连板总数 × 5%)）。

- **去重问题**：同一只股票可能出现在多个 jiuyang 板块（如"光模块"和"算力"），`all_candidates` 用 `raw in all_candidates` 做去重（L226），只会保留第一个遇到的板块归属。这在逻辑上是对的（一只票只入一次候选），但可能丢失"多板块共振"的信号。目前 `plate_weight` 只取第一个板块的 topnum，如果一只票同时在 topnum=10 和 topnum=5 的板块里，它只拿到 5 的权重。

### 4.2 候选股收集（L212-254）

| 检查点 | 结论 |
|--------|------|
| jiuyang_name_map O(1) 查表 | ✅ L220 避免了 O(n²) |
| reason 截断 80 字符 | ✅ L237 `[:80]` |
| 龙头标记 ban≥4 | ✅ L251 |

**🟡 问题**：龙头标记用的是 `ban_tag` 解析（L250 `int(tag.replace('ban', '').replace('b', ''))`），但 `_ban_height_tag`（L133-139）已经用正则做了更健壮的解析。两处逻辑不一致，`ban_tag` 解析更脆弱（如果 tag 格式是 `"三板"` 而非 `"ban3"`，这里会 ValueError）。

虽然 L253 有 try-except 兜底，但意味着这些股票会被**漏标为非龙头**（`is_longtou` 默认 False），导致它们不会被排除——但它们本应该被排除。

### 4.3 筛选逻辑（L256-299）

| 规则 | 实现 | 结论 |
|------|------|------|
| 龙头 ban≥4 排除 | L265-267 | ✅ |
| MySQL 无数据排除 | L271-273 | ✅ |
| 一字板/尾盘砸盘标记 | L282-283 | ✅ 仅标记不排除 |
| 形态检查仅在有 MySQL 数据时执行 | L278 | ✅ |

**正确**。筛选逻辑清晰，排除条件合理。

### 4.4 综合评分排序（L304-367）

#### ✅ 评分公式

```python
_score = ban_height × 2.0 + plate_weight × 0.5 + FORM_SCORES + is_lianban_bonus + f30_bonus
```

| 维度 | 权重/分数 | 合理性 |
|------|----------|--------|
| 连板高度 | ×2.0 / 板 | ✅ 高板晋级是强信号 |
| 板块强度 | ×0.5 / topnum | ✅ 板块越热，个股越受益 |
| 分钟形态 | -5.0 ~ +3.0 | ✅ 尾盘砸盘重罚，早盘脉冲奖励 |
| 在板跟风 | +1.5 | ✅ 已启动但非龙头，适合低吸 |
| 前30分钟量能 | +1.0（40-80%）| ✅ 健康放量区间 |

**🟡 权重分配问题**：

- `ban_height × 2.0` 意味着 ban10 的票比 ban3 多 14 分。但 `FORM_SCORES` 只有 -5~+3 的范围，连板高度完全主导了排序。对于**低吸策略**（step5 的核心定位），候选股大多是首板/二板的跟风股（ban_height 0-2），此时 ban_height 的区分度反而很弱（差值只有 0-4 分）。
- `plate_weight × 0.5` 的 topnum 范围通常在 5-20，贡献 2.5-10 分，对低吸目标股（ban_height 低）排序影响很大。这是合理的——板块热度是低吸的安全边际。

#### ✅ P4 修复验证（L340）

旧版只在 `len > HARD_CAP` 时排序，新版**始终排序**，保证候选顺序稳定。✅

### 4.5 `_check_minute_pattern()`（L370-421）

#### ✅ 形态分类逻辑

| 形态 | 条件 | 合理性 |
|------|------|--------|
| 一字板 | amp < 0.5% | ✅ 几乎无波动 |
| 早盘脉冲 | f30 > 85% 且 amp < 3% | ✅ 集中放量但价格不动 |
| 早盘拉升 | f30 > 70% | ✅ 早盘集中放量 |
| 尾盘拉升 | q4 > 1% | ✅ 尾盘涨 |
| 尾盘砸盘 | q4 < -1% | ✅ 尾盘跌 |
| 正常波动 | 其他 | ✅ 兜底 |

#### 🟡 判断边界问题

**问题 1：amp 阈值断层**

- `amp < 0.5` → 一字板
- `amp 0.5~5`（f30>70）→ 正常波动（不满足早盘脉冲的 amp<3）
- `amp 0.5~5`（f30>85）→ 早盘脉冲（amp<3）或正常波动（3≤amp<5）

也就是说 amp=2.99 + f30=86% → 早盘脉冲；amp=3.01 + f30=86% → 早盘拉升。1 个基点的差异导致形态完全不同。建议加过渡区间或连续化评分。

**问题 2：q4 尾盘指标的脆弱性**

```python
q4 = (prices[-1] - prices[-60]) / prices[-60] * 100
```

`prices[-60]` 假设数据恰好 241 条（9:30~15:00）。如果数据只有 60 条（比如停牌半天或数据不完整），`prices[-60]` 就是 `prices[0]`（开盘价），此时 q4 变成"收盘相对开盘的涨跌幅"，不再是"尾盘相对前1小时"的变化率。

代码 L394 有 `if len(prices) >= 60` 保护，但 len≥60 不等于 len=241。**只有 `len >= 240` 时 q4 才有物理意义**。

**问题 3：形态分类的优先级**

代码用 if-elif 链，一旦命中就不再检查后续条件。但形态之间并非互斥：

- 一只票可能同时是"早盘拉升"+"尾盘砸盘"（早盘拉高，午后回落）
- 当前代码只会把它归为"早盘拉升"，丢失尾盘风险信号

### 4.6 `_compute_zhangting_strength()`（L439-729）

> 注：因子体系的详细审计见 [AUDIT_step5_zhangting_strength.md](./AUDIT_step5_zhangting_strength.md)，此处只补充**运行流畅和逻辑判断**维度。

#### 🔴 Crash-3：C1 计算（L608-611）—— 上一轮审计的 P2 变量作用域问题已确认修复

```python
# L608-611 当前代码
post_limit_vol = sum(volumes[first_hit:]) if first_hit >= 0 else 0
post_limit_vol = max(post_limit_vol, 1)
seal_vol = sum(v for i, v in enumerate(volumes) if i >= first_hit and is_on_limit(i)) if first_hit >= 0 else 0
c1_vol_ratio = seal_vol / post_limit_vol * 100
```

✅ 变量 `available` 已移到 A2 评分处（L644），C1 不再引用它。之前的 NameError 崩溃已修复。

#### 🟡 `_compute_zhangting_strength()` 未被 `run()` 调用

这是当前代码最大的**架构问题**：

- `_compute_zhangting_strength()` 定义了完整的 11 因子涨停强度评分（L439-729，占文件 40% 的代码量）
- `run()` 只调用 `_check_minute_pattern()` 做简单形态分类（L280）
- **没有任何代码路径调用 `_compute_zhangting_strength()`**

这意味着：
1. 290 行代码是"死代码"——当前完全不生效
2. step6 拿到的 candidates 里没有涨停强度分
3. 如果 step6 依赖 `score` 字段做预测，会得到 `KeyError`

**建议**：确认 `_compute_zhangting_strength()` 是否应在 `run()` 的第三步（L276-283）中对每只候选股调用，并将结果附加到 candidate dict 中。

---

## 五、判断合理性分析

### 5.1 核心业务判断

| 判断 | 代码体现 | 评价 |
|------|---------|------|
| **以 jiuyang 为板块标准** | L112-113 `jiuyang_name_map` | ✅ 解决了板块名不一致问题（2026-04-22 重构） |
| **topnum≥5 为 tier1** | L190 | ⚠️ 固定阈值，情绪低时可能为空 |
| **龙头 ban≥4 排除** | L251 | ✅ 避免追高风险 |
| **一字板标记但不排除** | L282-283 | ✅ 一字板次日可能开板，值得跟踪 |
| **尾盘砸盘标记但不排除** | L282-283 | ⚠️ 尾盘砸盘票风险较高，是否应该降权而非仅标记？ |
| **HARD_CAP=60** | L257 | ⚠️ 为什么是 60？是否应基于 tier1 板块数量动态调整？ |
| **综合评分中形态权重** | FORM_SCORES -5~+3 | ⚠️ 形态分范围过窄，在高板票面前被 ban_height×2 压制 |

### 5.2 边界情况判断

| 场景 | 当前行为 | 合理性 |
|------|---------|--------|
| jiuyang 为空 | 返回 verdict="无数据跳过" | ✅ |
| 所有板块 topnum<5 | candidates 为空，verdict 建议观望 | ✅ |
| 某只股在多个 tier1 板块 | 只保留第一个板块 | ⚠️ 丢失共振信号 |
| 某只股无 MySQL 分钟数据 | 标记排除 | ✅ |
| 某只股有 MySQL 但分钟不足 60 条 | `_check_minute_pattern` 返回 None，form=None | ✅ 不影响入选，但缺少形态信息 |
| 退市/停牌股在 jiuyang 列表中 | 不会被排除（除非无 MySQL 数据） | ⚠️ 建议检查是否停牌（分钟数据=0 或只有开盘数据） |

### 5.3 低吸策略一致性

step5 的定位是"成分股筛选"，服务于低吸策略。从判断逻辑看：

| 低吸核心要求 | step5 实现 | 一致性 |
|-------------|-----------|--------|
| 找到有启动信号的个股 | `is_lianban=True`（已涨停）| ✅ 但已涨停的票不适合低吸，低吸应关注"跟风未涨但属于强板块"的票 |
| 排除追高风险 | 龙头 ban≥4 排除 | ✅ |
| 评估回调机会 | 无回调评估逻辑 | ❌ 缺失 |
| 评估支撑位 | 无支撑位判断 | ❌ 缺失 |
| 评估缩量信号 | `_check_minute_pattern` 有 f30 量能比 | ⚠️ 只看了一天 |

**关键矛盾**：step5 的候选股来自 tier1 板块，但筛选逻辑偏向"找在板股"（`is_lianban` 加 1.5 分）。对于低吸策略，真正需要的是"强板块中**尚未涨停**但有资金关注的跟风股"——这些票目前没有被特别加权。

---

## 六、代码质量分析

### 6.1 优点

| 维度 | 评价 |
|------|------|
| **函数职责清晰** | `run` / `_check_minute_pattern` / `_compute_zhangting_strength` / `_get_limit_ratio` 各司其职 |
| **注释充分** | 每个 P0/P1/P2 修复都有注释标注，便于追溯 |
| **防御性编码** | 多处 `or 1`、`if > 0`、try-except 兜底 |
| **无外部依赖** | 只用标准库 `math` + `typing`，零依赖风险 |
| **O(n²) 优化** | 用 dict map 替代嵌套遍历，L112-113 |
| **DRY 重构** | `_get_limit_ratio` 抽取公共逻辑（L23-38） |

### 6.2 代码异味

| 问题 | 位置 | 严重度 | 说明 |
|------|------|--------|------|
| 函数内定义函数 | L133-165 | 低 | `_ban_height_tag`、`ban_height`、`in_lianban`、`find_mysql_key` 等 5 个函数定义在 `run()` 内部，每次调用 `run()` 都会重新创建。Python 闭包有性能开销，建议提到模块级 |
| `re` 延迟导入 | L135 | 低 | `import re` 在 `_ban_height_tag` 内部，每次调用都会执行 import 语句（虽然 Python 会缓存模块，但不规范） |
| 魔法数字 | L190 `topnum < 5`、L257 `HARD_CAP = 60`、L251 `bh >= 4` | 中 | 关键业务参数硬编码，建议集中为常量或配置 |
| `_parse_ban_height` 冗余 | L149-151 | 低 | 与 L133-139 `_ban_height_tag` 功能完全相同 |
| `FORM_SCORES` 定义在循环内 | L308-315 | 低 | 每次调用 `run()` 都会重建字典，应提为模块常量 |
| 代码风格不统一 | L472 `m['price']` vs L474 `m.get('amount', 0)` | 中 | 取值方式不一致 |

### 6.3 可维护性

| 指标 | 评价 |
|------|------|
| 文件长度 | 729 行，适中偏大。建议拆分为 `filter.py` + `zhangting_strength.py` |
| 修改历史可追溯 | ✅ 每个修复都有注释标记（P0-1、P0-2、P2-④等） |
| 类型标注 | ✅ 函数签名有完整的 type hints |
| 单元测试 | ❌ 未发现测试文件 |

---

## 七、问题汇总（按优先级）

### 🔴 P0 — 必须修复（运行时错误）

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| P0-A | `_check_minute_pattern` 和 `_compute_zhangting_strength` 中 `m['price']`/`m['volume']` 无防御性取值 | L374-375, L472-473 | 数据缺失时 KeyError 崩溃 |
| P0-B | `_compute_zhangting_strength()` 是死代码，未被 `run()` 调用 | L439-729 | 290 行代码不生效，step6 可能 KeyError |
| P0-C | 龙头标记用脆弱的 `replace('ban','')` 而非已有的正则 `_ban_height_tag` | L250 | 格式不匹配时漏标龙头 |

### 🟡 P1 — 应该修复（逻辑/设计问题）

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| P1-A | `topnum≥5` 固定阈值 | L190 | 情绪低迷时 candidates 为空 |
| P1-B | 多板块共振信号丢失 | L226 | 只保留第一个板块归属 |
| P1-C | `HARD_CAP=60` 魔法数字 | L257 | 无法根据市场热度动态调整 |
| P1-D | `_check_minute_pattern` 形态互斥分类 | L396-407 | 早盘拉升+尾盘砸盘只返回前者 |
| P1-E | q4 尾盘指标对数据长度敏感 | L394 | 分钟数据不足 240 条时含义失真 |
| P1-F | 综合评分中 `ban_height×2` 主导排序 | L321 | 低吸候选（ban 0-2）区分度弱 |
| P1-G | 低吸策略缺少回调/支撑/缩量评估 | 全局 | 筛选结果偏向已涨停票 |

### ⚠️ P2 — 建议优化（代码质量）

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| P2-A | 5 个内部函数应提到模块级 | L133-165 | 每次调用 `run()` 重建闭包 |
| P2-B | `import re` 应移到文件顶部 | L135 | 代码规范 |
| P2-C | `_parse_ban_height` 与 `_ban_height_tag` 重复 | L149-151 | DRY 违反 |
| P2-D | 取值风格不统一 `.get()` vs `[]` | L472 vs L474 | 一致性 |
| P2-E | `FORM_SCORES` 应为模块常量 | L308-315 | 性能微优化 |
| P2-F | 缺少单元测试 | — | 回归风险 |

---

## 八、修复建议（按优先级排序）

### 第一优先级：修复崩溃

```
1. P0-A: m.get('price', 0) / m.get('volume', 0) 替代 m['price'] / m['volume']
2. P0-C: L250 改用 _ban_height_tag(tag) 替代手动 replace
```

### 第二优先级：确认 `_compute_zhangting_strength` 的调用路径

```
3. P0-B: 在 run() 第三步中，对 is_lianban=True 的候选股调用
        _compute_zhangting_strength()，将结果附加到 candidate dict
4. 确认 step6 是否依赖 score 字段，如果依赖，必须完成第 3 步
```

### 第三优先级：逻辑增强

```
5. P1-A: topnum 阈值动态化 = max(3, 连板总数 × 5%)
6. P1-D: 形态分类改为"多标签"而非互斥
7. P1-E: q4 增加 len(prices) >= 240 前置检查
8. P1-G: 为"未涨停但属于强板块"的跟风股增加专项加分
```

### 第四优先级：代码质量

```
9. P2-A: 内部函数提到模块级
10. P2-C: 删除 _parse_ban_height 冗余
11. P2-F: 补充核心路径的单元测试
```

---

## 九、总体评价

| 维度 | 评分 | 说明 |
|------|------|------|
| **运行流畅度** | ⭐⭐⭐⭐ 4/5 | 时间复杂度优秀，有 2 处潜在崩溃需修复 |
| **逻辑正确性** | ⭐⭐⭐☆ 3.5/5 | 主流程正确，形态分类和涨停强度有设计缺陷 |
| **判断合理性** | ⭐⭐⭐☆ 3.5/5 | 板块筛选和龙头排除合理，低吸定位偏移 |
| **架构设计** | ⭐⭐⭐ 3/5 | 函数分层清晰，但涨停强度模块是死代码 |
| **代码质量** | ⭐⭐⭐⭐ 4/5 | 注释充分、防御性好，有少量 DRY 和规范问题 |
| **可维护性** | ⭐⭐⭐☆ 3.5/5 | 修改历史可追溯，缺少测试和动态配置 |

**综合评价**：step5 是一个**功能完整、逻辑基本正确**的成分股筛选模块。2026-04-22 的重大重构解决了板块名匹配问题，P0 级 bug（A3 炸板/B3 均价/A2 分母）已在上一轮审计后修复。当前最紧迫的问题是**数据字段取值的防御性缺失**（可能导致运行时崩溃）和 **`_compute_zhangting_strength()` 未被调用**（290 行死代码）。业务逻辑上，筛选结果偏向已涨停票，与低吸策略定位存在偏差，建议在综合评分中增加"未涨停跟风股"的专项评估。

---

*分析完成。共发现 3 个 P0 级、7 个 P1 级、6 个 P2 级问题。*
