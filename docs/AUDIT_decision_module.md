# decision/ 模块完整代码审计报告

> 审计日期：2026-04-23  
> 审计范围：decision/ 全部 10 个 Python 文件 + 1 个 JSON 配置  
> 源码版本：最新提交（含 C1×高潮期 guard / push_up_style 差异化 / 板块加成等修改）

---

## 一、模块架构总览

```
decision/
├── __init__.py              (37行)   入口：导出 6 个核心类
├── types.py                 (87行)   类型层：Morphology 枚举 + MorphologyFeatures dataclass
├── classifier.py            (277行)  分类器：特征提取 + classify
├── config/
│   └── morphology_config.json (191行) 规则引擎：9形态×6阶段预测矩阵
├── predictor.py             (488行)  预测引擎：T+1 方向/置信度/板块加成
├── morphology_matrix.py     (143行)  Facade：向后兼容门面
├── position_rules.py        (92行)   仓位系统：6阶段仓位配置
├── three_questions.py       (162行)  三问过滤：空间板+主线+亏钱效应
├── risk_controller.py       (79行)   风控：RR比+止损止盈+系统风险
├── accuracy_tracker.py      (320行)  追踪器：双轨贝叶斯准确率
├── pain_effect_analyzer.py  (656行)  亏钱效应：四维度评分+一票否决
└── accuracy_stats_recent.json (运行时生成)
```

### 数据流

```
minute_data/OHLC
    │
    ▼
┌─────────────┐   MorphologyFeatures   ┌──────────────┐
│  classifier  │ ─────────────────────▶ │   predictor   │
│  (提取+分类) │                        │  (T+1预测)    │
└─────────────┘                        └──────┬───────┘
       │                                      │
  Morphology枚举                         prediction dict
       │                                      │
       ▼                                      ▼
┌──────────────────────────────────────────────────┐
│                   MorphologyMatrix               │
│  (Facade: 组合 classifier + predictor)           │
└──────────────────────────────────────────────────┘
       │
       ▼
┌─────────────┐  ┌──────────────────┐  ┌────────────────┐
│ position    │  │ three_questions  │  │ risk_controller│
│ (仓位计算)  │  │ (三问过滤)       │  │ (风控止损)     │
└─────────────┘  └──────────────────┘  └────────────────┘
                       │
                       ▼
              ┌────────────────────┐
              │ pain_effect       │
              │ (亏钱效应分析)     │
              └────────────────────┘
                       │
                       ▼
              ┌────────────────────┐
              │ accuracy_tracker   │
              │ (实时准确率追踪)    │
              └────────────────────┘
```

---

## 二、逐文件深度分析

### 2.1 `types.py` — 类型定义层 (87行)

**职责**：纯类型定义，零业务逻辑。

#### Morphology 枚举 (9种)

| Code | 名称 | 含义 |
|------|------|------|
| A | 一字板 | 无量锁仓，最强 |
| B | 正常涨停 | 换手充分，稳健 |
| C1 | 冲高回落 | 高风险 |
| D1 | 低开低走 | 弱势 |
| D2 | 尾盘急拉 | f30>80% + q4在-2%~+1% |
| E1 | 普通波动 | 方向不明 |
| E2 | 宽幅震荡 | 方向不明 |
| F1 | 温和放量 | 最安全，3/3胜率 |
| H | 横向整理 | 无效信号 |

#### MorphologyFeatures dataclass

**核心字段**：
- 价格维度：`open_pct`, `close_pct`, `high_pct`, `low_pct`, `amplitude`
- 量能维度：`q1_volume_pct`, `q2_volume_pct`, `q3_volume_pct`, `q4_volume_pct`, `f30`
- 辅助字段：`push_up_style`, `board_quality`, `sector_leader`
- 时间维度（外部传入）：`consec_days`, `cycle_position`

**审计结论**：
- ✅ 职责清晰，纯类型定义
- ✅ `from_string()` 的 mapping 覆盖了常见简写
- ⚠️ `cycle_position` 字段定义了但 **predictor.py 未使用**（dead field）
- ⚠️ `sector_leader` 字段定义了但 **predictor.py 未使用**（dead field）

---

### 2.2 `classifier.py` — 形态分类器 (277行)

**职责**：特征提取 + 分类，不含预测逻辑。

#### 2.2.1 `extract_features()` — 分钟数据路径 (L32-111)

**输入**：241点分钟数据 `[{price, volume, time, base_price}]`

**处理流程**：
1. base_price 三级 fallback：字段 > change_pct反推 > 首价
2. 四个季度成交量分段：
   - Q1: 0-30 (9:30-10:00)
   - Q2: 30-90 (10:00-11:00)
   - Q3: 90-180 (11:00-13:00)
   - Q4: 180+ (13:00-14:57)
3. 拉升方式判断 `_judge_push_style()`
4. 板质量判断 `_judge_board_quality()`

**审计发现**：

| # | 级别 | 行号 | 问题 | 说明 |
|---|------|------|------|------|
| 🔴 | P0 | L78 | `q3_vol` 自引用 **已修复** | 之前是 `q3_count+q3_vol`，现在正确为 `q3_count`。**确认已修复。** |
| 🟡 | P1 | L113-132 | `push_up_style` 用绝对价格差 | `early_gain = early_prices[-1] - early_prices[0]` 是绝对价格差。10元股涨2元 = +20%，100元股涨2元 = +2%。应该用涨跌幅比率。低价股偏差大。 |
| 🟡 | P1 | L134-147 | 烂板阈值与 OHLC 路径不一致 | 分钟路径：`high_pct - close_pct > 3` 判烂板；OHLC路径：`amplitude > 8` 判烂板。同一只股票可能因数据来源不同得到不同 board_quality。 |
| ⚠️ | P2 | L179 | OHLC 路径一字板判断过宽 | `(close_pct < 1 and f30 > 80) or (is_limit_up and amplitude < 3)` — 第二个条件 `is_limit_up and amplitude < 3` 可能误判。如 close=9.8%, high=10.2%, low=8%, amplitude=2.2% → 被判为一字板，但实际是烂板。 |

#### 2.2.2 `extract_from_ohlc()` — OHLC 简化路径 (L153-216)

**输入**：OHLC + base_price + 可选 q1_vol_pct/q4_vol_pct

**与分钟路径的差异**：

| 特征 | 分钟路径 | OHLC路径 |
|------|---------|---------|
| push_up_style | 4种（脉冲/午盘/尾盘/稳健）| 仅2种（脉冲/稳健），**无法区分尾盘偷袭** |
| board_quality | 一字板：close_pct<1 & amp<3 | 一字板：(close_pct<1 & f30>80) or (涨停 & amp<3) |
| 烂板 | high_pct-close_pct>3 | amplitude>8 |
| 成交量分段 | 实际统计 | 比例估算 (Q1:传入, Q2:40%, Q3:35%, Q4:25%) |

**审计发现**：

| # | 级别 | 行号 | 问题 | 说明 |
|---|------|------|------|------|
| 🟡 | P1 | L196-201 | `push_up_style` 退化严重 | OHLC 路径下无法产生 '尾盘偷袭' 和 '午盘拉升'，D2 规则的 push_up_style 精细化完全失效。 |
| ⚠️ | P2 | L179 | 一字板误判风险 | 见上文 P2 说明 |

#### 2.2.3 `classify()` — 分类逻辑 (L222-277)

**分类优先级链**：

```
A(一字板) → B(涨停amp<8) → C1(high-close>5 & amp>10) → D1(open<-2 & close<open) 
→ D2(f30>80 & -2≤close≤1) → F1(Q1 40-60 & amp 3-8 & 涨) → E2(amp>8 & 非涨停) 
→ H(amp<2 & Q1>70) → E1(amp<5) → E1(默认)
```

**审计发现**：

| # | 级别 | 行号 | 问题 | 说明 |
|---|------|------|------|------|
| ⚠️ | P2 | L254 | D2 的 close_pct 范围过窄 | `-2 <= close_pct <= 1` 只覆盖很小的收盘区间。如果 f30>80 但 close_pct=1.5%，则不满足 D2，会落进 E2（如果 amp>8）。这种边界 case 可能导致分类不稳定。 |
| ℹ️ | P3 | L267-269 | H 判断注释说"在 E1 之前"但实际顺序 | H 在 E2 之后判断。如果 amp<2 但 Q1<=70，既不满足 H 也不满足 E2，但 E1 需要 amp<5 → 会落入 E1。逻辑上没问题，注释可能引起混淆。 |

---

### 2.3 `morphology_config.json` — 规则引擎 (191行)

**结构**：9 个基础形态配置 + 6 个阶段的 stage_overrides

#### 完整预测矩阵 (9×6)

| 形态 | 冰点期 | 退潮期 | 升温期 | 高潮期 | 降温期 | 修复期 |
|------|--------|--------|--------|--------|--------|--------|
| **A** | ✅+0.85 | ⚠️neutral/降权 | ✅+0.85 | ⚠️neutral/降权 | ⚠️neutral/降权 | ✅+0.85 |
| **B** | +0.65 | +0.65 | +0.65 | ⚠️neutral 0.55 | ⚠️neutral 0.50 | +0.65 |
| **C1** | neg 0.75 | neg 0.75 | ✅+0.62 | ✅+0.55(幸存者) | neg 0.75 | neg 0.75 |
| **D1** | neg 0.70 | ⚠️neg 0.78 | neg 0.70 | ⚠️neutral 0.45 | neg 0.70 | neg 0.70 |
| **D2** | neg 0.80 | ⚠️neg 0.80 | neg 0.80 | ⚠️neg 0.80 | ⚠️neg 0.80 | ⚠️neutral 0.55 |
| **E1** | ✅+0.60 | neutral 0.45 | ✅+0.60 | ✅+0.55 | neutral 0.45 | ✅+0.54 |
| **E2** | ✅+0.65 | neutral 0.50 | ✅+0.68 | ✅+0.70 | neutral 0.45 | neutral 0.50 |
| **F1** | ✅+0.88 | ✅+0.88 | ✅+0.88 | ✅+0.88 | ✅+0.88 | ✅+0.88 |
| **H** | neutral 0.40 | neutral 0.40 | neutral 0.40 | neutral 0.40 | neutral 0.40 | neutral 0.40 |

> ✅ = positive / neg = negative / ⚠️ = 特殊处理（见 predictor.py 代码）
> 数字为 t1_confidence（JSON 静态值，实际会被 tracker 动态覆盖）

**覆盖统计**：
- JSON override 覆盖了 14/54 种组合（26%）
- 40 种走通用路径（`_build_generic_prediction`）
- A/D2/F1/H 的 24 种组合完全由 predictor.py special_rules 控制

**审计发现**：

| # | 级别 | 位置 | 问题 | 说明 |
|---|------|------|------|------|
| ✅ | — | 全局 | JSON 配置结构合理 | 规则与代码分离，便于调参 |
| ⚠️ | P2 | 高潮期-C1 | 幸存者偏差注释但无硬编码 guard | JSON 注释了"amplitude≥12则降权至negative"，但这个 guard 在 predictor.py 中。如果 JSON override 先执行（实际是），guard 变死代码。**见 P0-2。** |
| ⚠️ | P2 | 升温期-C1 | conf=0.62 偏高 | "43样本/胜率95.3%/avg=+10.68%" — 样本量小，贝叶斯先验会向下拉。但 JSON 静态 0.62 可能仍偏高。 |

---

### 2.4 `predictor.py` — T+1 预测引擎 (488行)

**职责**：综合形态+阶段+板块强度 → T+1 预测结论

#### 2.4.1 核心执行流 `predict()` (L114-153)

```
predict(features, morphology, market_stage, sector_strength)
    │
    ├─ 1. _get_stage_override() → JSON 配置有覆盖？
    │      YES → _build_from_override()  ← 直接返回
    │      NO  ↓
    ├─ 2. _apply_special_rules() → 匹配特殊规则？
    │      YES → 返回 dict
    │      NO  ↓
    └─ 3. _build_generic_prediction() ← 兜底
```

#### 🔴 P0-2：执行顺序缺陷（关键发现）

**问题**：JSON override（步骤1）优先于 special_rules（步骤2），导致以下 guard **成为死代码**：

| 死代码 | 位置 | 条件 | 说明 |
|--------|------|------|------|
| C1×高潮期 pullback≥12 guard | L172-186 | morphology==C1 & stage==高潮期 | **但 JSON 高潮期-C1 有 override** → 永远走步骤1，步骤2 永远不走 |
| E1×高潮期 amplitude≥3 降权 | L194-206 | morphology==E1 & stage==高潮期 | **但 JSON 高潮期-E1 有 override** → 同上 |

**影响**：
- C1×高潮期冲高超12%的股票，本应降权至 negative，但现在走 JSON override → 返回 positive (conf=0.55)
- E1×高潮期 amplitude≥3 的股票，本应降权至 neutral，但现在走 JSON override → 返回 positive (conf=0.55)

**修复方案（推荐方案A）**：

在 `_build_from_override()` 开头加入 guard 检查：

```python
def _build_from_override(self, features, morphology, market_stage, override, sector_strength):
    # === Guard：JSON override 之前的拦截规则 ===
    if morphology == Morphology.C1 and market_stage == '高潮期':
        pullback = features.high_pct - features.close_pct
        if pullback >= 12:
            return {  # ... negative 预测
            }
    if morphology == Morphology.E1 and market_stage == '高潮期':
        if features.amplitude >= 3:
            return {  # ... neutral 预测
            }
    # ... 原有逻辑
```

#### 2.4.2 `_apply_special_rules()` (L159-319)

**5条特殊规则**：

| # | 规则 | 条件 | 结果 | 状态 |
|---|------|------|------|------|
| 1 | C1×高潮期 | (已迁移到JSON override) | — | 已注释，不执行 |
| 1.5 | E1×高潮期 amp≥3 | E1 + 高潮期 + amp≥3 | neutral 0.45 | 🔴 **死代码** |
| guard | C1×高潮期 pullback≥12 | C1 + 高潮期 + pullback≥12 | negative 0.65 | 🔴 **死代码** |
| 2 | F1 最安全 | F1 (无条件) | positive | ✅ 正常（F1 无 JSON override） |
| 3 | D2 规则 | D2 + 特定阶段 | negative/neutral | ✅ 正常（D2 无 JSON override） |
| 4 | A 类规则 | A + consec_days/阶段 | 调节 | ✅ 正常（A 无 JSON override） |

#### 2.4.3 `_build_from_override()` (L321-385)

**板块加成机制**：

```python
boost_map = {
    E1: 0.20,  # 最弱形态，最需要板块支撑
    E2: 0.18,
    B:  0.10,
    C1: 0.12,
    D1: 0.05,
    H:  0.05,
}
# 条件：sector_strength > 0.7
# positive 方向：直接加 boost
# neutral 方向：sector_strength > 0.85 时加 boost×0.4
```

**审计发现**：

| # | 级别 | 行号 | 问题 | 说明 |
|---|------|------|------|------|
| ⚠️ | P2 | L358-365 | boost_map 无 F1/A/D2 | F1/A/D2 在 special_rules 中直接返回，不走 override 路径，所以不需要。逻辑正确但不够显式。 |
| ✅ | — | L334-338 | Cold Start 警告 | 样本<5时提示，设计合理 |
| ✅ | — | L342-349 | consec_days 调节 | E1/E2×高潮期连续≥3天降权，符合逻辑 |

#### 2.4.4 `_build_generic_prediction()` (L391-447)

**审计发现**：

| # | 级别 | 行号 | 问题 | 说明 |
|---|------|------|------|------|
| ⚠️ | P2 | L414-421 | boost_map 重复定义 | 与 `_build_from_override` 中完全相同的 boost_map，应提取为类常量 |
| ✅ | — | L407-411 | Cold Start 警告 | 与 override 路径一致 |

#### 2.4.5 置信度双层机制 (L99-112)

```
_get_confidence(morphology, stage, json_conf)
    ├─ tracker 有足够数据(min_samples=3) → 返回 blended 值
    └─ tracker 数据不足 → fallback 到 JSON 静态值
```

**审计结论**：✅ 设计优秀。JSON 先验 + tracker 后验贝叶斯的双层机制是系统的核心亮点。

---

### 2.5 `morphology_matrix.py` — Facade 门面 (143行)

**职责**：保持向后兼容 API，委托给 classifier + predictor。

**审计发现**：

| # | 级别 | 问题 | 说明 |
|---|------|------|------|
| ✅ | — | Facade 模式标准 | 委托清晰，API 兼容 |
| ✅ | — | `predict_with_stage()` 别名 | 保持旧代码兼容 |
| ⚠️ | P3 | config 加载冗余 | `__init__` 中的 config 加载与 `predictor._load_config()` 重复，但影响不大 |

---

### 2.6 `position_rules.py` — 仓位规则 (92行)

#### 6阶段仓位配置

| 阶段 | base | min | max | 乘数上限 |
|------|------|-----|-----|---------|
| 冰点期 | 20% | 10% | 30% | 1.5×5板=30% |
| 退潮期 | 15% | 10% | 30% | 1.5×5板=22.5% |
| 升温期 | 40% | 30% | 50% | 1.5×5板=60%→50% |
| 高潮期 | 40% | 30% | 50% | 1.5×5板=60%→50% |
| 降温期 | 20% | 10% | 30% | 1.5×5板=30% |
| 修复期 | 20% | 10% | 30% | 1.5×5板=30% |

#### 乘数体系

```python
# 连板乘数
lianban >= 5: ×1.5
lianban >= 3: ×1.3
lianban == 2: ×1.15

# 板块乘数
sector >= 0.8: ×1.2
sector >= 0.6: ×1.1
is_main_line: ×1.1 (叠加)

# 情绪乘数
emotion < 20: ×0.9
emotion > 90: ×0.95
```

**审计发现**：

| # | 级别 | 行号 | 问题 | 说明 |
|---|------|------|------|------|
| ⚠️ | P2 | L58-60 | 连板乘数方向存疑 | 连板天数越多乘数越大 = 仓位越重。但高位连板（≥5板）风险极大。这与其他模块的"连板高位回避"逻辑矛盾。建议：`lianban >= 5` 应降仓而非加仓。 |
| ⚠️ | P2 | L77-79 | `should_enter()` 逻辑 | 退潮期/降温期禁止开仓，但 emotion<20 的极端冰点允许。问题是：退潮期 emotion<20 = 市场恐慌中的退潮，此时"允许试探"是否合理？ |
| ✅ | — | L46-49 | 未知阶段 warning | P2-A修复已到位，logging.warning 有助于排查 |

---

### 2.7 `three_questions.py` — 三问定乾坤 (162行)

**输入**：space_board / main_line / pain_score / top_rate / dadian_count + 时间维度趋势

**权重**：Q1 空间板 40% + Q2 主线 35% + Q3 亏钱效应 25%

**时间维度惩罚**（建议5新增）：

| 趋势 | 惩罚 | 说明 |
|------|------|------|
| ladder_trend='decelerating' | -15 | 梯队高度下降 |
| main_line_trend in (rotating, dying) | -10 | 主线衰退 |
| pain_trend='worsening' | -10 | 亏钱效应扩散 |
| **最大惩罚** | **-35** | |

**判决阈值**：

| 分数 | 判决 | 条件 |
|------|------|------|
| ≥70 | heavy (重仓) | Q1 passed AND Q2 passed |
| ≥50 | light (轻仓) | — |
| <50 | forbidden (禁止) | — |

**审计发现**：

| # | 级别 | 行号 | 问题 | 说明 |
|---|------|------|------|------|
| ⚠️ | P2 | L58-63 | heavy 需要 Q1+Q2 同时 pass | 如果 Q1=90(Q1 pass) + Q2=90(Q2 pass) + Q3=20(pain=20, not pass)，overall = 90×0.4 + 90×0.35 + 20×0.25 = 36+31.5+5 = 72.5 → heavy。但 pain=20 说明亏钱效应严重，仍然允许重仓？建议：heavy 还需要 Q3 pass。 |
| ✅ | — | L82-84 | Q1 加速趋势加分 | `ladder_trend='accelerating'` → max(score, 80)，合理 |
| ✅ | — | L152-157 | Q3 亏钱趋势调整 | worsening→40, improving→70，方向正确 |
| ✅ | — | L76-93 | Q1 空间板 | status/degree 分级合理 |

---

### 2.8 `risk_controller.py` — 风险控制 (79行)

**常量**：

```python
HARD_STOP = 0.05      # 硬止损 5%
SOFT_STOP = 0.03      # 软止损 3%
PROFIT_TARGET = 0.25  # 止盈 25%
MIN_RR = 3.0          # 最小风险收益比 3:1
MIN_EXPECTANCY = 0.01 # 最小期望值
MIN_TURNOVER = 8000   # 最小成交额（亿）
```

**审计发现**：

| # | 级别 | 行号 | 问题 | 说明 |
|---|------|------|------|------|
| ⚠️ | P2 | L73-74 | emotion_score 阈值 90 | P2-②修复后从 >100 改为 >90。但 emotion_score 的范围来自 VIP 复盘网的 degree_market（0-100）。>90 = 高潮区警戒 → 仓位上限 20%。合理。 |
| ⚠️ | P2 | L66 | rise_pct < 25 触发风险 | 但 `rise_pct` 参数含义不明确 — 是上涨家数占比？还是指数涨幅？如果是个股涨跌幅，<25% 可能太宽松。 |
| ✅ | — | L27-38 | RR 计算 | 公式正确，expectancy 双路径（已知胜率/未知胜率）设计合理 |
| ✅ | — | L40-61 | 止损止盈 | 三级止损（硬/软/追踪）+ 固定止盈，实用 |

---

### 2.9 `accuracy_tracker.py` — 准确率追踪器 (320行)

#### 双轨机制（V5新增）

```
overall_smoothed = Bayesian(correct + 6, total + 10)     # 先验60%
recent_smoothed  = Bayesian(correct_recent + 1.8, len_recent + 3)  # 先验60%×0.3权重
blended = overall × 0.4 + recent × 0.6                   # 近期权重更高
```

**审计发现**：

| # | 级别 | 行号 | 问题 | 说明 |
|---|------|------|------|------|
| ✅ | — | L246 | 近期窗口先验缩放 | `PRIOR_CORRECT * 0.3` — 近期窗口先验力度是全量的30%，避免少量近期样本过度影响。设计合理。 |
| ✅ | — | L56-104 | _recent 持久化 | P1-1修复已到位，进程重启不丢近期数据 |
| ⚠️ | P2 | L144-163 | `record_batch()` 不更新 _recent | 批量导入历史数据时只更新 `_stats` 不更新 `_recent`。导致 bootstrap 后 `recent` 队列为空，blended = overall × 0.4 + overall × 0.6 = overall（即 blended 退化为 overall）。**bootstrap 后双轨机制无效。** |
| ⚠️ | P2 | L246 | 近期窗口需要 ≥5 才算 | `if recent_deque and len(recent_deque) >= 5` — 如果近期只有 1-4 条，recent = overall，blended 退化为 overall。新形态前5条记录双轨无效。 |
| ✅ | — | L117-124 | KeyError 防护 | P1-1补丁，防止 _recent 和 _stats 不同步 |

---

### 2.10 `pain_effect_analyzer.py` — 亏钱效应分析 (656行)

#### 一票否决（5条 + 1条警示）

| # | 条件 | 分数上限 | 说明 |
|---|------|---------|------|
| ① | zhaban≥15 + top_rate≤70% + long_ban≥6 | ≤25 | 极端龙头炸板 |
| ①b | zhaban≥10 + top_rate≤75% + long_ban≥5 + degree≥55 | 降权10分 | 高温市场炸板警示（非否决） |
| ② | yesterday_top_rate < -3% | ≤30 | 最强资金亏损 |
| ③ | 涨跌比 < 0.3 | ≤20 | 市场全面恐慌 |
| ④ | 跌停≥15 + top_rate<50% | ≤20 | 最强资金被闷杀 |
| ⑤ | top_rate<40% + 涨跌比<0.5 | ≤30 | 炸板潮+跌多涨少 |

#### 四维度评分

| 维度 | 权重 | 基础分 | 核心指标 |
|------|------|--------|---------|
| 龙头高位股 | 25% | 70 | long_ban, continue_top_num, damian, zhaban |
| 连板生态 | 25% | 70 | ban1→ban2晋级率, 断层检测, 梯队空虚 |
| 涨停质量 | **35%** | 60 | top_rate, yesterday_top_rate, highopen_rate, zhaban |
| 市场整体 | 15% | 60 | 涨跌比, diff_amount |

**审计发现**：

| # | 级别 | 行号 | 问题 | 说明 |
|---|------|------|------|------|
| ✅ | — | L461-471 | ①b 警示与否决分离 | 设计精巧：①b 不触发 hard veto，而是降权10分，最终在 run() 中单独扣除 |
| ⚠️ | P2 | L247-257 | 断层检测边界 | `for height in range(7, 1, -1)` 只检查 7→2。如果 highest_ban=8+（当前最高板≥8），ban8/ban9 不在检查范围内。但 ban7 字段实际是"七板及以上"，所以无问题。 |
| ⚠️ | P2 | L100-103 | ①号否决阈值 | zhaban≥15 + top_rate≤70% + long_ban≥6 — 三个条件同时满足的概率极低。实际市场中 zhaban=15+ 且 long_ban=6+ 的情况很少。可能导致此否决几乎不触发。 |
| ✅ | — | L532-536 | 档位微调 | "亏钱效应显现"档位下，damian=0 + top_rate≥80 时给出更积极的建议。精细化。 |
| ✅ | — | L546-561 | trend 计算 | 取 history 中最近一天的分数做差，逻辑正确 |

---

## 三、跨模块问题汇总

### 🔴 P0 级（必须修复）

| # | 问题 | 模块 | 影响 | 修复方案 |
|---|------|------|------|---------|
| P0-2 | **执行顺序缺陷**：JSON override 优先于 special_rules | predictor.py | C1×高潮期 pullback≥12 guard 和 E1×高潮期 amp≥3 降权成为**死代码** | 在 `_build_from_override()` 开头加 guard 检查（约5行代码） |

### 🟡 P1 级（建议修复）

| # | 问题 | 模块 | 影响 | 修复方案 |
|---|------|------|------|---------|
| P1-1 | push_up_style 用绝对价格差 | classifier.py L113-132 | 低价股（如3元股）拉升方式误判 | 改为涨跌幅比率：`(prices[-1] - prices[0]) / prices[0] * 100` |
| P1-2 | 分钟路径 vs OHLC 路径 board_quality 不一致 | classifier.py | 同一股票不同数据来源得到不同分类 | 统一判断条件，或明确文档说明两条路径的差异 |
| P1-3 | OHLC 路径 push_up_style 退化（只有2种） | classifier.py L196-201 | D2 规则的 push_up_style 精细化完全失效 | 需要额外参数或更智能的估算逻辑 |
| P1-4 | record_batch() 不更新 _recent | accuracy_tracker.py L144-163 | bootstrap 后双轨机制退化为单轨 | 在 record_batch 中也 append 到 _recent |
| P1-5 | 连板乘数方向矛盾 | position_rules.py L58-60 | 高位连板加仓，与"连板高位回避"逻辑矛盾 | ≥5板应降仓(×0.7)，而非加仓(×1.5) |

### ⚠️ P2 级（可以优化）

| # | 问题 | 模块 | 说明 |
|---|------|------|------|
| P2-1 | boost_map 重复定义 | predictor.py | 提取为类常量 `_SECTOR_BOOST_MAP` |
| P2-2 | ①号否决条件过严 | pain_effect_analyzer.py | 三条件同时满足概率极低 |
| P2-3 | rise_pct 含义不明 | risk_controller.py L66 | 需确认是上涨家数占比还是指数涨幅 |
| P2-4 | heavy 判决不需 Q3 pass | three_questions.py L58 | pain 严重时仍可重仓 |
| P2-5 | dead field: cycle_position / sector_leader | types.py | 定义了但无模块使用 |
| P2-6 | D2 close_pct 范围过窄 | classifier.py L254 | 边界 case 可能不稳定 |

---

## 四、运行时行为验证

### predict() 完整决策树

```
predict(E1, 高潮期, amp=5, sector=0.8)
    ├─ JSON override 存在(E1×高潮期) → _build_from_override()
    │   ├─ direction = positive (from JSON)
    │   ├─ confidence = get_real_precision() (from tracker)
    │   ├─ consec_days >= 3? → conf -= 0.10
    │   ├─ sector > 0.7? → conf += 0.20 (E1 boost)
    │   └─ return positive, conf
    │
    └─ ⚠️ E1×高潮期 amp≥3 降权 → 永远不执行（死代码！）

predict(C1, 高潮期, high=15, close=2, sector=0.5)
    ├─ JSON override 存在(C1×高潮期) → _build_from_override()
    │   ├─ direction = positive (from JSON, 幸存者偏差)
    │   ├─ confidence = 0.55 (JSON fallback or tracker)
    │   └─ return positive, 0.55
    │
    └─ ⚠️ C1×高潮期 pullback=13≥12 guard → 永远不执行（死代码！）

predict(F1, 任何阶段, consec=4)
    ├─ JSON override 不存在 → _apply_special_rules()
    ├─ 匹配 F1 规则
    │   ├─ consec >= 4 → conf -= 0.15
    │   └─ return positive, conf (正确执行)
```

---

## 五、系统整体评价

| 维度 | 评分 | 说明 |
|------|------|------|
| **架构设计** | ⭐⭐⭐⭐☆ | Facade+分类器+预测器分离清晰，JSON规则引擎解耦良好 |
| **分类逻辑** | ⭐⭐⭐⭐⭐ | 9种形态优先级链严谨，边界条件处理到位 |
| **预测逻辑** | ⭐⭐⭐⭐ | 双层置信度机制优秀，但 P0-2 执行顺序缺陷影响2个 guard |
| **代码质量** | ⭐⭐⭐⭐☆ | P0-1 已修复，重复代码(P2-1)、dead field(P2-5)等小问题 |
| **实战价值** | ⭐⭐⭐⭐⭐ | 四维度亏钱效应、三问过滤、时间维度趋势 — 直接可用 |
| **可维护性** | ⭐⭐⭐⭐ | JSON 配置+代码分离，但跨模块一致性（分钟/OHLC路径）需加强 |

---

## 六、修复优先级建议

1. **立即修复**：P0-2 执行顺序缺陷（5行代码）
2. **本迭代修复**：P1-1~P1-3 classifier 一致性问题
3. **下迭代修复**：P1-4 accuracy_tracker bootstrap + P1-5 连板乘数
4. **择机优化**：P2 级别问题

---

*审计完成。共发现 1 个 P0、5 个 P1、6 个 P2 级问题。*
