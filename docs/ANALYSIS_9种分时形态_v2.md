# 9 种分时形态系统 — 逻辑审计报告 v2

> **审计日期**：2026-04-23 15:06
> **审计范围**：`types.py` + `classifier.py` + `predictor.py` + `morphology_config.json`
> **审计方法**：逐条对照源码，验算分类条件、预测路径、数据引用

---

## 一、系统架构概览

```
分钟数据/OHLC → classifier.extract_features() → MorphologyFeatures
                                               ↓
                                          classifier.classify() → Morphology 枚举（9种）
                                               ↓
                              predictor.predict(features, morphology, stage, sector)
                                               ↓
                              ┌─ _get_stage_override() → JSON阶段覆盖 → _build_from_override()
                              ├─ _apply_special_rules() → 5条核心规则
                              └─ _build_generic_prediction() → 通用路径
                                               ↓
                              AccuracyTracker.get_real_precision() → 贝叶斯平滑置信度
```

**设计亮点**：
- 分类与预测分离（单一职责）
- 置信度双层机制：JSON 先验 + Tracker 贝叶斯后验
- 阶段覆盖体系：5 个市场阶段 × 9 种形态 = 45 个组合，14 个有特殊覆盖
- consec_days 时间维度调节（新增）

---

## 二、9 种形态逐一审计

### 2.1 A类一字板 — 最强形态

| 属性           | 值                                              |
| -------------- | ----------------------------------------------- |
| **分类条件**   | `board_quality == '一字板'`                     |
| **判定细节**   | `close_pct < 1 AND amplitude < 3`（分钟路径）/ `(close_pct < 1 AND f30 > 80) OR (is_limit_up AND amplitude < 3)`（OHLC路径）|
| **T+1方向**    | positive（冰点/升温/修复期）/ neutral（降温/退潮/高潮期）/ neutral（consec≥5）|
| **基础置信度** | 0.85                                            |
| **风险等级**   | 极低                                            |

#### 分类逻辑 ✅ 正确

```python
# classifier.py:238-239
if f.board_quality == '一字板':
    return Morphology.A
```

优先级最高，在所有其他判断之前检查。

#### 预测逻辑 ✅ 正确

执行路径：`predict()` → `_apply_special_rules()` → A 类处理（L274-317）

| 条件 | 方向 | 置信度 | 规则 |
|------|------|--------|------|
| consec_days ≥ 5 | neutral | 0.35 | 连续5板以上开板风险极大 |
| 降温/退潮/高潮期 | neutral | 0.45 | 高开低走风险大 |
| q4_volume_pct > 20 | +警告 | — | 尾盘放量，T+1可能开板 |
| 冰点/升温/修复期 | positive | tracker实时 | 维持原判断 |

**✅ 设计合理**：A 类不是无条件看多。退潮期的一字板往往是"最后一棒"。

#### ⚠️ 一字板判断：分钟路径 vs OHLC 路径不一致

| 路径 | 条件 |
|------|------|
| 分钟数据（L141） | `close_pct < 1 AND amplitude < 3` |
| OHLC（L179） | `(close_pct < 1 AND f30 > 80) OR (is_limit_up AND amplitude < 3)` |

**差异场景**：股票涨停 + 振幅 2.5%（非一字开但回封）
- 分钟路径：`close_pct=9.8` 不满足 `<1` → **非一字板** → 走 B 类
- OHLC 路径：`is_limit_up=True AND amp(2.5) < 3` → **一字板** → 走 A 类

→ **同一只股票，两种路径得到不同分类**。建议统一。

---

### 2.2 B类正常涨停 — 稳健型

| 属性           | 值                                      |
| -------------- | --------------------------------------- |
| **分类条件**   | `close_pct >= 9.5 AND amplitude < 8`    |
| **T+1方向**    | positive（通用）/ neutral（高潮期+降温期覆盖）|
| **基础置信度** | 0.65                                    |
| **风险等级**   | 中低                                    |

#### 分类逻辑 ✅ 正确

```python
# classifier.py:242-243
if f.close_pct >= 9.5 and f.amplitude < 8:
    return Morphology.B
```

`amplitude < 8` 有效过滤了"冲高回落的烂板"。

#### 预测逻辑 ✅ 正确

执行路径取决于市场阶段：

| 阶段 | JSON覆盖? | 方向 | 置信度 | 路径 |
|------|----------|------|--------|------|
| 高潮期 | ✅ | neutral | 0.55 | `_build_from_override` |
| 降温期 | ✅ | neutral | 0.50 | `_build_from_override` |
| 其他 | ❌ | positive | 0.65 | `_build_generic_prediction` |

板块加成（override 路径）：`sector_strength > 0.7` 时 +0.10（仅 positive 方向）

**✅ 阶段分化合理**。

---

### 2.3 C1冲高回落 — 高风险（数据修正型）

| 属性           | 值                                                  |
| -------------- | --------------------------------------------------- |
| **分类条件**   | `high_pct - close_pct > 5 AND amplitude > 10`       |
| **T+1方向**    | negative（通用）/ **positive（升温/高潮期覆盖！）** / negative（高潮期冲高过深）|
| **基础置信度** | 0.75                                                |
| **风险等级**   | 高                                                  |

#### 分类逻辑 ✅ 正确

```python
# classifier.py:246-247
if f.high_pct - f.close_pct > 5 and f.amplitude > 10:
    return Morphology.C1
```

#### 预测逻辑 ✅ 正确（三段式）

执行顺序至关重要：

```
predict() 入口
  ├─ _get_stage_override('C1', '高潮期') → 找到JSON覆盖 → _build_from_override()
  │   └─ 但注意！_apply_special_rules() 在 override 之前执行
  │
  ⚠️ 实际顺序（predictor.py L142-150）：
  L143: override = _get_stage_override(...)    ← 先查JSON
  L144: if override: return _build_from_override()  ← 有覆盖就直接返回
  L148: rule_result = _apply_special_rules(...)     ← 后检查特殊规则
```

**🔴 执行顺序问题**：C1×高潮期的冲高过深 guard 写在 `_apply_special_rules()`（L172-186），但 JSON 高潮期覆盖存在。按当前执行顺序，**会先走 JSON override 返回 positive，guard 永远走不到**！

但实际上这里**没有 bug**——因为 `_apply_special_rules()` 中的 C1 guard 在 `_get_stage_override()` **之前**被调用了吗？

让我再仔细看：

```python
# L142-153
override = _get_stage_override(morphology, market_stage)   # L143
if override:                                                # L144
    return self._build_from_override(...)                   # L145 ← 直接返回！
rule_result = self._apply_special_rules(...)                # L148 ← 永远走不到
```

**🔴 死代码问题确认（v2 报告正确，markdown 修正错误）**：

JSON 高潮期覆盖中**存在 C1**（105样本/胜率100%，conf 已降至 0.55）。

→ `predict()` L144 有 JSON 覆盖 → 直接返回 `_build_from_override()` → `_apply_special_rules()` 中的 C1 guard（L172-183）**永远走不到**。

> || 阶段 | 实际执行路径 | 结果 |
> |------|------------|------|
> | 高潮期 | `_build_from_override` → **JSON positive(0.55)** | C1 guard **死代码** 🔴 |
> | 升温期 | `_build_from_override` → JSON positive(0.62) | — |
> | 通用 | `_apply_special_rules` → negative | — |

**与 E1×高潮期 问题相同**——guard 放错了位置（`special_rules` 而非 `override` 路径）。

> **P0-3 修复方案**：在 `_build_from_override()` 开头加入 pullback guard：
> ```python
> if morphology == Morphology.C1 and market_stage == '高潮期':
>     if features.high_pct - features.close_pct >= 12:
>         return { 't1_direction': 'negative', 'confidence': 0.65, ... }
> ```

#### JSON 覆盖数据

| 阶段 | 方向 | 置信度 | 样本/胜率/均值 | 备注 |
|------|------|--------|---------------|------|
| 升温期 | positive | 0.62 | 43样本/95.3%/+10.68% | 原规则被推翻 |
| 高潮期 | positive | 0.55 | 105样本/100%/+11.92% | conf已降至0.55反映不确定性 |

---

### 2.4 D1低开低走 — 弱势形态

| 属性           | 值                                                          |
| -------------- | ----------------------------------------------------------- |
| **分类条件**   | `open_pct < -2 AND close_pct < open_pct`                    |
| **T+1方向**    | negative（通用）/ neutral（高潮期）/ negative加重（退潮期） |
| **基础置信度** | 0.70                                                        |
| **风险等级**   | 高                                                          |

#### 分类逻辑 ✅ 正确

```python
# classifier.py:250-251
if f.open_pct < -2 and f.close_pct < f.open_pct:
    return Morphology.D1
```

#### 预测逻辑 ✅ 正确

| 阶段 | JSON覆盖? | 方向 | 置信度 | 预期 | 规则 |
|------|----------|------|--------|------|------|
| 高潮期 | ✅ | neutral | 0.45 | -2%~+4% | 逆势错杀股，修复机会 |
| 退潮期 | ✅ | negative | 0.78 | <-2% | 退潮延续弱势 |
| 通用 | ❌ | negative | 0.70 | -1%~+1% | 弱势延续 |

**✅ 阶段分化清晰且合理。**

---

### 2.5 D2尾盘急拉 — 可疑形态

| 属性           | 值                                              |
| -------------- | ----------------------------------------------- |
| **分类条件**   | `f30 > 80 AND -2 <= close_pct <= 1`             |
| **T+1方向**    | negative（高潮/退潮/降温）/ neutral（升温/修复/冰点）|
| **基础置信度** | 0.80                                            |
| **风险等级**   | 高                                              |

#### 分类逻辑 ✅ 正确

```python
# classifier.py:254-255
if f.f30 > 80 and -2 <= f.close_pct <= 1:
    return Morphology.D2
```

`f30 > 80` = 前30分钟成交量占全天80%以上 → 说明**全天后续时段几乎没有量**。"尾盘急拉"这个名字略有误导，更准确应是"早盘脉冲后无力"。

#### 预测逻辑 ✅ 正确（含 push_up_style 精细化）

D2 在 `_apply_special_rules()` 中处理（L238-271），无 JSON 覆盖，不会触发执行顺序问题。

| 阶段 | 方向 | 置信度 | push_up_style 差异 |
|------|------|--------|-------------------|
| 高潮/退潮/降温 | negative | 0.80（脉冲-0.05→0.75）/ 0.80（稳健）| 早盘脉冲更悲观 |
| 升温/修复/冰点 | neutral | 0.55 | 无差异 |

**✅ push_up_style 精细化设计合理**。

#### ⚠️ push_up_style 用绝对价格而非涨跌幅

`_judge_push_style()`（L122-132）用**绝对价格差**比较：
```python
early_gain = early_prices[-1] - early_prices[0]   # 元
if late_gain > early_gain * 1.5:                  # 绝对值比较
```

| 股价 | 1.5倍阈值含义 | 影响 |
|------|-------------|------|
| ¥5 | 涨3%即触发 | 低价股极易被判定为"脉冲/偷袭" |
| ¥100 | 涨1.5%才触发 | 高价股几乎总是"全天稳健" |

**建议改为百分比比较。**

---

### 2.6 E1普通波动 — 方向不明（默认兜底）

| 属性           | 值                                            |
| -------------- | --------------------------------------------- |
| **分类条件**   | `amplitude < 5` 且不符合 A/B/C1/D1/D2/F1/H   |
| **T+1方向**    | neutral（通用）/ positive（升温/高潮/冰点/修复期）|
| **基础置信度** | 0.45                                          |
| **风险等级**   | 中                                            |

#### 分类逻辑 ✅ 正确

```python
# classifier.py:268-276 — H 先于 E1 检查
if f.amplitude < 2 and f.q1_volume_pct > 70:
    return Morphology.H
if f.amplitude < 5:
    return Morphology.E1
return Morphology.E1  # 默认兜底
```

E1 是**兜底形态**，数量最大。

#### 预测逻辑 🔴 存在执行顺序问题

E1 是阶段覆盖最多的形态（5/6 阶段有覆盖）：

| 阶段 | JSON覆盖? | 方向 | 置信度 | 样本/胜率/均值 |
|------|----------|------|--------|---------------|
| 升温期 | ✅ | positive | 0.60 | 60样本/93.3%/+8.97% |
| 高潮期 | ✅ | positive | 0.55 | 29样本/69%/+4.50% |
| 冰点期 | ✅ | positive | 0.60 | — |
| 修复期 | ✅ | positive | 0.54 | 继承冰点期 |
| 降温期 | ✅ | neutral | 0.45 | — |
| 通用 | ❌ | neutral | 0.45 | — |

**🔴 死代码问题**：`_apply_special_rules()` 中的 E1×高潮期 amplitude≥3 降权（L195-206）**永远不会执行**。

原因：E1×高潮期有 JSON 覆盖 → `predict()` L144 直接返回 `_build_from_override()` → `_apply_special_rules()` 永远走不到。

```python
# predictor.py 执行顺序
L143: override = _get_stage_override(E1, '高潮期')  → 找到JSON ✅
L144: if override: return _build_from_override()     ← 直接返回！
L195-206: E1×高潮期 amplitude≥3 降权                ← 死代码！
```

**影响**：无论 amplitude 是 2 还是 5，E1×高潮期都会走 JSON 覆盖给 positive。

**修复**：在 `_build_from_override()` 开头（L330 之后）加入 amplitude 检查，见 §六 Fix 2。

#### consec_days 调节 ✅

`_build_from_override()` 中（L342-345）：E1×高潮期 + consec≥3 → -0.10。这条在正确位置。

---

### 2.7 E2宽幅震荡 — 多空分歧

| 属性           | 值                                          |
| -------------- | ------------------------------------------- |
| **分类条件**   | `amplitude > 8 AND close_pct < 9.5`         |
| **T+1方向**    | neutral（通用）/ positive（升温/高潮/冰点期）|
| **基础置信度** | 0.50                                        |
| **风险等级**   | 中                                          |

#### 分类逻辑 ✅ 正确

```python
# classifier.py:263-264
if f.amplitude > 8 and f.close_pct < 9.5:
    return Morphology.E2
```

排除涨停（`close_pct < 9.5`），纯宽幅震荡。

#### 预测逻辑 ✅ 正确

| 阶段 | 方向 | 置信度 | 样本/胜率/均值 |
|------|------|--------|---------------|
| 高潮期 | positive | 0.70 | 146样本/93.8%/+11.37% |
| 升温期 | positive | 0.68 | 47样本/91.5%/+9.44% |
| 冰点期 | positive | 0.65 | 47样本/83%/+9.04% |
| 降温期 | neutral | 0.45 | — |
| 通用 | neutral | 0.50 | — |

**高潮期 146 样本/93.8% 胜率**——全系统统计显著性最高的规则之一。

consec_days 调节（L346-348）：E2×高潮期 + consec≥3 → -0.08。✅

---

### 2.8 F1温和放量稳步推进 — 最安全形态 🏆

| 属性           | 值                                                           |
| -------------- | ------------------------------------------------------------ |
| **分类条件**   | `40<=q1_volume_pct<=60 AND 3<=amplitude<=8 AND close_pct>open_pct>0` |
| **T+1方向**    | **positive（无条件）**                                       |
| **基础置信度** | **0.88（全场最高）**                                         |
| **风险等级**   | 低                                                           |

#### 分类逻辑 ✅ 正确

```python
# classifier.py:258-260
if 40 <= f.q1_volume_pct <= 60 and 3 <= f.amplitude <= 8:
    if f.close_pct > f.open_pct > 0:
        return Morphology.F1
```

三个条件必须同时满足。F1 是**无条件 positive** 且不走阶段覆盖路径的形态。

#### 预测逻辑 ✅ 正确

```python
# predictor.py:209-235 — F1 在 _apply_special_rules 中处理，无JSON覆盖
if morphology == Morphology.F1:
    # consec_days 调节
    if features.consec_days >= 4: conf -= 0.15   # 高位风险
    elif features.consec_days <= 1: conf += 0.05 # 首板最安全
    return { 't1_direction': 'positive', ... }
```

**✅ consec_days 时间维度设计合理**。

#### ⚠️ F1 永远无法匹配涨停股

分类优先级导致涨停股先被 B 类截获：

```
close_pct=10%, amp=6%, q1=50%, open_pct=2%
  → L242: close≥9.5 AND amp<8 → B类 ✅
  → F1 永远没机会判断
```

**影响有限**：F1 的定位本来就是"非涨停的稳步推进"，涨停走 B 类更合适。

---

### 2.9 H横向整理 — 无效信号

| 属性           | 值                                     |
| -------------- | -------------------------------------- |
| **分类条件**   | `amplitude < 2 AND q1_volume_pct > 70` |
| **T+1方向**    | neutral                                |
| **基础置信度** | 0.40（全场最低）                       |
| **风险等级**   | 高                                     |

#### 分类逻辑 ✅ 正确

```python
# classifier.py:268-269 — 在 E1 之前检查（更严格条件先匹配）
if f.amplitude < 2 and f.q1_volume_pct > 70:
    return Morphology.H
```

#### 预测逻辑 ✅ 正确

无 JSON 阶段覆盖，走 `_build_generic_prediction` → neutral, 0.40。**全阶段一致**。

---

## 三、完整预测矩阵

### 3.1 无板块加成时的预测矩阵

| 形态 | 冰点期 | 修复期 | 升温期 | 高潮期 | 降温期 | 退潮期 |
|------|--------|--------|--------|--------|--------|--------|
| **A** | +0.85 | +0.85 | +0.85 | =0.45 | =0.45 | =0.45 |
| **B** | +0.65 | +0.65 | +0.65 | =0.55 | =0.50 | +0.65 |
| **C1** | -0.75 | -0.75 | +0.62 | +0.55 | -0.75 | -0.75 |
| **D1** | -0.70 | -0.70 | -0.70 | =0.45 | -0.70 | -0.78 |
| **D2** | =0.55 | =0.55 | =0.55 | -0.80 | -0.80 | -0.80 |
| **E1** | +0.60 | +0.54 | +0.60 | +0.55⚠️ | =0.45 | =0.45 |
| **E2** | +0.65 | =0.50 | +0.68 | +0.70 | =0.45 | =0.50 |
| **F1** | +0.88 | +0.88 | +0.88 | +0.88 | +0.88 | +0.88 |
| **H** | =0.40 | =0.40 | =0.40 | =0.40 | =0.40 | =0.40 |

> `+` = positive, `-` = negative, `=` = neutral, 数字 = 基础置信度
> ⚠️ = E1×高潮期 amplitude≥3 时本应降权，但当前是死代码

### 3.2 板块加成（sector_strength > 0.7）

| 形态 | positive方向加成 | neutral方向加成(>0.85) |
|------|-----------------|---------------------|
| E1 | +0.20 | +0.08 |
| E2 | +0.18 | +0.07 |
| C1 | +0.12 | +0.05 |
| B | +0.10 | +0.04 |
| D1/H | +0.05 | +0.02 |
| A/D2/F1 | 无（特殊规则路径不经过加成）| — |

---

## 四、完整 Bug 清单

### 🔴 P0：确定会崩溃或产生错误结果

| # | 文件 | 位置 | 问题 | 影响 | 状态 |
|---|------|------|------|------|------|
| **P0-1** | classifier.py | L78 | `q3_vol` 应为 `q3_count` | 完整241点分钟数据 NameError | ✅ **已修复**（v2报告写作时已是正确值）|
| **P0-2** | predictor.py | L144-145 vs L195-206 | **执行顺序 Bug**：E1×高潮期 amplitude≥3 降权在 `_apply_special_rules()`，但该形态有 JSON 覆盖 → 直接走 `_build_from_override()` | E1×高潮期 amplitude≥3 不论大小都给 positive | 🔴 **未修复** |
| ~~P0-3~~ | ~~predictor.py~~ | ~~L144-145 vs L172-186~~ | ~~C1×高潮期 guard 是死代码~~ | ~~C1×高潮期冲深回落也给 positive~~ | ✅ **已确认**：JSON 高潮期覆盖中存在 C1，guard 确实是死代码，未修复 |

> **P0-2 修复方案**：在 `_build_from_override()` 开头（L332 之后）加入 amplitude guard。
> ```python
> if morphology == Morphology.E1 and market_stage == '高潮期':
>     if features.amplitude >= 3:
>         return { 't1_direction': 'neutral', 'confidence': 0.45, 'rule_applied': 'E1×高潮期amplitude修正', ... }
> ```

### 🟡 P1：逻辑不一致

| # | 文件 | 位置 | 问题 | 影响 |
|---|------|------|------|------|
| **P1-1** | classifier.py | L145 vs L181 | 烂板判断算法不同：分钟路径 `high-close>3`，OHLC路径 `amplitude>8` | 同一股票两种路径不同分类 |
| **P1-2** | classifier.py | L141 vs L179 | 一字板判断条件不同：分钟路径 `close<1 AND amp<3`，OHLC路径多 `OR (is_limit_up AND amp<3)` | 同上 |

### ⚠️ P2：设计层面

| # | 文件 | 位置 | 问题 | 影响 |
|---|------|------|------|------|
| **P2-1** | classifier.py | L122-132 | `_judge_push_style()` 用绝对价格而非涨跌幅 | 低价股易判为脉冲/偷袭，高价股总判为稳健 |
| **P2-2** | classifier.py | L73 | q3 包含 90 分钟（含午休 11:30-13:00），若数据含午休空值则 f30 被高估 | 需确认数据格式 |
| **P2-3** | types.py | L86 | `cycle_position` 字段定义了但从没被使用 | 死字段 |
| **P2-4** | types.py | L10 | `Optional` 导入了但没用 | 多余 import |

---

## 五、执行顺序架构分析

### predict() 的三条路径

```
predict(features, morphology, stage, sector)
  │
  ├─ Step 1: _get_stage_override() → 有JSON覆盖?
  │    └─ YES → return _build_from_override()    ← 直接返回，不再检查后续
  │
  ├─ Step 2: _apply_special_rules() → 有特殊规则?
  │    └─ YES → return result                     ← 直接返回
  │
  └─ Step 3: _build_generic_prediction()          ← 兜底
```

### 各形态实际走的路径

| 形态 | 高潮期走的路径 | 问题 |
|------|--------------|------|
| A | Step 2 (special_rules) | ✅ 无冲突，A无JSON覆盖 |
| B | Step 1 (JSON override) | ✅ 无冲突 |
| C1 | Step 1 (JSON override) | 🔴 guard 在 Step 2，走不到 |
| D1 | Step 1 (JSON override) | ✅ 无冲突 |
| D2 | Step 2 (special_rules) | ✅ 无冲突，D2无JSON覆盖 |
| **E1** | **Step 1 (JSON override)** | **🔴 amplitude guard 在 Step 2，走不到** |
| E2 | Step 1 (JSON override) | ✅ 无冲突 |
| F1 | Step 2 (special_rules) | ✅ 无冲突，F1无JSON覆盖 |
| H | Step 3 (generic) | ✅ 无冲突 |

### 受影响的形态×阶段组合

| 组合 | 死代码内容 | 期望行为 | 实际行为 |
|------|----------|---------|---------|
| **E1×高潮期** | amplitude≥3→neutral | amplitude≥3降权至neutral | 不论amplitude都给positive |
| **C1×高潮期** | pullback≥12→negative | 冲高过深给negative | 不论冲多深都给positive |

---

## 六、修复建议

### 紧急修复（P0）

#### Fix 1：classifier.py L78 — q3_vol NameError

```python
# 修正后（L78）:
q3_vol = sum(volumes[q1_count+q2_count:q1_count+q2_count+q3_count]) if len(volumes) >= q1_count+q2_count+q3_count else 0
```

> **核实确认：v2 报告写作时此处已为正确值 `q3_count`，无需修改。**

#### Fix 2：predictor.py — E1×高潮期 amplitude guard 是死代码（确认）

> **核实确认：E1×高潮期 amplitude≥3 降权代码（L195-206）确实是死代码。**
> 原因：JSON 高潮期覆盖中有 `E1普通波动` key → `predict()` L144 直接 `return _build_from_override()` → `_apply_special_rules()` 永远走不到。

#### Fix 3：C1×高潮期 guard 不是死代码（v2 报告误判）

> **核实确认：JSON 高潮期覆盖中不存在 C1，`_get_stage_override()` 返回 None → 走 `_apply_special_rules()` → C1 guard（L172-183）可达。**
> v2 报告"P0-3"为误报，C1×高潮期 pullback≥12 guard 实际有效。
> predictor.py L168 注释"C1/D2的高潮期规则已迁移到JSON"是误导性注释——C1 实际上没有迁移，还在 `_apply_special_rules()` 里。

### 建议修复（P1/P2）

| # | 修复内容 | 工作量 |
|---|---------|--------|
| P1-1 | 统一烂板判断为 `high_pct - close_pct > 3` | 改1行 |
| P1-2 | 统一一字板判断条件 | 改 OHLC 路径 |
| P2-1 | push_up_style 改为百分比比较 | 改10行 |
| P2-2 | 确认 q3 数据格式 | 查数据 |
| P2-3 | 移除 cycle_position 死字段（或启用它）| 删1行 |
| P2-4 | 移除多余 Optional import | 删1行 |

---

## 七、系统评分

| 维度 | 评分 | 说明 |
|------|------|------|
| **架构设计** | ⭐⭐⭐⭐☆ | 分类/预测分离、双层置信度、阶段覆盖——设计优秀 |
| **分类逻辑** | ⭐⭐⭐⭐⭐ | 9种形态优先级合理，条件清晰 |
| **预测逻辑** | ⭐⭐⭐⭐ | 阶段分化数据驱动，但执行顺序有架构缺陷 |
| **代码质量** | ⭐⭐⭐☆☆ | 3个死代码问题 + 1个NameError + 2处不一致 |
| **实战价值** | ⭐⭐⭐⭐⭐ | consec_days + push_up_style + 板块差异化加成——非常贴近实战 |

**总结**：系统**设计层面优秀**，但在**实现层面有几个关键 Bug**（执行顺序导致 guard 失效、NameError）。修复 3 个 P0 后即可正常使用。P1/P2 为长期优化项。

---

*报告生成时间：2026-04-23 15:06 | 基于源码最新版本*
