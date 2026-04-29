# Step6 T+1 预判矩阵 — 深度分析报告

> 分析对象：`/Users/eason/.hermes/trading_study/project/steps/step6_t1_prediction.py`（546行）
> 分析范围：运行流畅度 / 逻辑正确性 / 判断合理性 / 数据源 / 架构设计
> 依赖模块：`decision/` 下的 MorphologyMatrix、PositionRules、ThreeQuestions、RiskController、pain_effect_analyzer

---

## 总评分：4.2 / 5

| 维度 | 评分 | 说明 |
|------|------|------|
| 运行流畅度 | 4.5/5 | 结构清晰，优雅降级，无明显崩溃风险 |
| 逻辑正确性 | 3.8/5 | 存在 2 个 P0 字段不匹配 + 若干边界问题 |
| 判断合理性 | 4.3/5 | 三问定乾坤 + pain_effect 四维度设计专业 |
| 数据源可靠性 | 3.5/5 | fupan/ds 可选参数兜底，但趋势推断数据不充分 |
| 架构设计 | 4.8/5 | Facade + 委托模式清晰，依赖解耦，扩展性好 |

---

## 一、架构设计（4.8/5 — 优秀）

### 1.1 整体数据流

```
step1(市场概况) ──┐
step2(主线分析) ──┤
step3(情绪周期) ──┼──→ step6.run() ──→ T+1预判矩阵
step4(连板健康) ──┤     │
step5(候选股)   ──┘     ├─→ MorphologyMatrix（形态分类+T+1预测）
                        ├─→ ThreeQuestions（三问定乾坤）
                        ├─→ PositionRules（仓位规则）
                        ├─→ pain_effect_analyzer（亏钱效应）
                        └─→ RiskController（⚠️ 已导入但未使用）
```

### 1.2 设计亮点

**✅ Facade 模式**
`MorphologyMatrix` 作为 Facade 委托给 `MorphologyClassifier` + `T1Predictor`，step6 只依赖一个门面接口，内部可独立演化。

**✅ 优雅降级**
所有外部数据（fupan/ds/minute_pattern）都有 None 兜底：
- L106：`if fupan:` → 否则 pain_trend='stable'
- L108：`if ds:` → 否则 history={}
- L336：`if mp and isinstance(mp, dict)` → 否则走字符串形态回退
- L248-270：`_get_prev_trade_date` 最多回查30天，返回 None 而非崩溃

**✅ 职责分离清晰**
```
_predict_emotion()       → 整体情绪预判（5阶段）
_predict_stocks()        → 个股形态预判（MorphologyMatrix）
_build_position_plan()   → 仓位计算（PositionRules）
_build_action_items()    → 操作清单生成
_build_matrix()          → 预判矩阵表格
```

每个函数职责单一，便于测试和维护。

---

## 二、问题清单

### 🔴 P0 — 必须修复

#### P0-① RiskController 导入但从未使用（死代码）

**位置**：L17（导入），L76（实例化 `rc = RiskController()`）

**问题**：`RiskController` 被导入并实例化，但其三个核心方法（`calculate_rr`、`should_stop_loss`、`check_system_risk`）在 step6 中从未被调用。这是资源浪费，也意味着风报比计算和止损逻辑没有接入 T+1 预判流程。

**影响**：step6 的 `can_enter` 判断只依赖 direction + confidence，缺少：
- 风报比（RR ≥ 3:1）硬门槛
- 止损价位计算
- 系统风险综合检查

**建议**：在 `_predict_stocks()` 中接入 `rc.calculate_rr()` 或在 `_build_position_plan()` 中接入 `rc.check_system_risk()`，否则删除导入和实例化。

---

#### P0-② `_infer_trends()` 中 qingxu 取自 step3 但 ladder 取自 step4，可能跨日期

**位置**：L21-59

**问题**：`_infer_trends(step3, step4, step2)` 中：
- `qingxu = step3.get('qingxu', '')` — 来自 step3（基于当日 fupan_data）
- `ladder = step4.get('ladder', {})` — 来自 step4（基于当日 lianban_data）

这两者理论上应该是同一天的数据，但 `ladder_trend` 的判断逻辑（L29-39）中用了 `health_score` 来自 step4，而 `main_line_trend` 的判断（L41-54）用了 `qingxu` 来自 step3。**这里不存在跨日期问题**，但存在语义混淆：`_infer_trends` 被命名为"推断趋势"，实际上只是用当日静态快照做了一个粗略的即时判断，不是真正的跨日趋势推断。

**实际风险**：L338 的 `ladder_trend='decelerating'` 会触发三问 -15 分惩罚，但这个"减速"判断是基于单日快照（有没有 ban5/ban6 + health_score），不能反映真正的跨日趋势变化。例如某天刚好没有 5 板以上但梯队很健康（health=80），会被错误判为 stable 而非 accelerating。

**建议**：
1. 将函数名改为 `_infer_instant_state()` 更准确
2. 或者接入 `pain_effect_analyzer` 的 history 趋势数据，做真正的跨日判断

---

### 🟡 P1 — 建议修复

#### P1-① `_predict_stocks()` 中 conf_floor 基于 `stock_preds`（已处理列表）而非 `candidates`（全量列表）

**位置**：L376

```python
total_candidates = len(stock_preds)  # ← 这里用的是 stock_preds（已经 append 过的）
```

**问题**：`stock_preds` 是在循环中逐个 append 的列表。当处理第 N 只股票时，`stock_preds` 只有 N-1 个元素。这意味着：
- 第 1 只股票：total=0，conf_floor=0.65
- 第 50 只股票：total=49，conf_floor=0.65
- 第 61 只股票：total=60，conf_floor=0.70
- 第 81 只股票：total=80，conf_floor=0.75

**同样的候选股**，排在前面的和排在后面的使用了不同的置信度门槛。这是一个 **顺序依赖 bug**。

**修复**：
```python
total_candidates = len(candidates)  # 用全量候选数
```

---

#### P1-② `_predict_stocks()` 中 sector_strength 硬编码 0.8/0.4

**位置**：L321

```python
sector_strength = 0.8 if tier1 else 0.4
```

**问题**：
- `tier1` 是一个列表（step2 返回的 `result['tier1']`，如 `['光模块', '算力']`）
- 判断 `bool(tier1)` 在 tier1 非空时为 True → sector_strength=0.8
- 但 step2 的 tier1 门槛是 `lianban_num >= 5`（L119），可能存在"有 tier1 但强度很弱"的情况
- 0.8 vs 0.4 是二元跳变，没有过渡区间

**建议**：使用 tier1 的实际连板数做连续映射：
```python
tier1_detail = step2.get('tier1_detail', [])
if tier1_detail:
    max_num = max(d.get('lianban_num', 0) for d in tier1_detail)
    sector_strength = min(1.0, 0.4 + max_num * 0.08)  # 5→0.8, 8→1.0
else:
    sector_strength = 0.4
```

---

#### P1-③ `_predict_emotion()` 中"高潮期"判断与 PositionRules 逻辑重复

**位置**：L279-288

```python
elif qingxu == '高潮期':
    zhaban = step4.get('ladder', {}).get('zhaban', {}).get('cnt', 0)
    if zhaban >= 10:
        pred = "高潮退潮信号，高位股分批离场"
```

**问题**：step4 的 ladder 结构中，`zhaban` 层的 key 是 `'zhaban'`，step4 构建的 tier_map（L59-65）格式为 `{'name', 'tag', 'cnt', 'rate', 'stocks'}`。这里 `step4.get('ladder', {}).get('zhaban', {}).get('cnt', 0)` 需要确认 MongoDB lianban_data 中 `lianban_list` 是否真的有 tag='zhaban' 的 tier。

根据 step2 L195 的注释：`zhaban（炸板家数）来自 fupan_data.open_num`，而 step4 的 `lianban_list` 来自 MongoDB `lianban_data`，其中确实有 `zhaban` tier（炸板股列表）。

**但这里有个潜在问题**：如果某天的 lianban_data 中没有 zhaban tier（数据缺失），这个条件永远不会触发，导致高潮期总是返回"延续但注意撤退"而非"退潮信号"。

**建议**：添加防御性取值，同时参考 fupan_data.open_num 作为备用：
```python
zhaban = step4.get('ladder', {}).get('zhaban', {}).get('cnt', 0)
# 备用：如果 step4 无 zhaban tier，从 step1 的 fupan_data 取
if zhaban == 0 and step1:
    zhaban = step1.get('_raw_fupan', {}).get('open_num', 0)  # 需要透传原始数据
```

---

#### P1-④ pain_effect_analyzer 中 fupan 字段 `long_ban` 在 MongoDB 中可能不存在

**位置**：pain_effect_analyzer.py L88, L154

```python
long_ban = int(fupan.get("long_ban", 0))
```

**问题**：根据 MEMORY.md 记录，MongoDB `fupan_data` 集合中包含 `long_code`（最高板股票代码），但不确定是否有 `long_ban`（最高板天数数字）字段。如果 MongoDB 中该字段不存在，`fupan.get("long_ban", 0)` 会返回 0，导致否决条件 ①（L101：`long_ban >= 6`）永远不会触发。

**建议**：从 step4 的 `long_code` 或 `ladder` 推算 `long_ban`：
```python
# 在 step6 调用 pain_run 之前补充 long_ban
if 'long_ban' not in fupan:
    fupan = dict(fupan)  # 不修改原始数据
    fupan['long_ban'] = _get_top_lianban_days(step4)  # 复用已有函数
```

---

#### P1-⑤ `_get_prev_trade_date()` 中 30 天限制可能不够

**位置**：L264

```python
for _ in range(30):  # 最多回查30天
```

**问题**：春节假期最长可达 13 天（2026 年 2/16-2/28），如果加上春节前后的周末，连续非交易日可能超过 15 天。30 天上限足够覆盖，但如果逢特殊情况（如疫情停市），可能不够。

**风险**：极低。30 天足够覆盖中国 A 股历史上最长的连续休市。

---

### 🟢 P2 — 建议优化

#### P2-① `_build_action_items()` 中操作清单最多展示 3 只候选 + 2 只警告

**位置**：L495, L506

```python
for p in enter_candidates[:3]:   # 最多3只
    ...
for p in danger[:2]:            # 最多2只
```

**建议**：使 `[:3]` 和 `[:2]` 可配置化，或根据仓位计划动态调整展示数量（仓位 50% → 展示更多候选）。

---

#### P2-② 三问中 Q2 `passed` 条件过严

**位置**：three_questions.py L123

```python
passed = status == "明确" and base_score >= 50
```

**问题**：Q2 需要 `status == "明确"` 才能通过，但 step6 L97 中：

```python
main_line_status = '明确' if main_line_data else '无'
```

`main_line_data = step2.get('zhuxian', [])`，而 step2 的输出中主线数据在 `result['zhuxian']`（不是 `zhuxian` 而是来自 fupan_data），同时 step2 的 tier1 列表才是 step6 应该用来判断主线的数据。

**实际检查**：step6 L96 `main_line_data = step2.get('zhuxian', [])` — step2 返回的 `result` 中没有 `zhuxian` 字段！step2 返回的是 `tier1`、`tier2`、`tier3`、`suggestion`、`strength_eval`、`lianban_list` 等。`zhuxian` 是 step1 返回的字段（L72：`'zhuxian': fupan.get('zhuxian', [])`）。

**这意味着**：step6 应该用 `step1.get('zhuxian', [])` 而不是 `step2.get('zhuxian', [])` 来获取主线数据！

**影响**：当前 `main_line_data` 始终为空列表 → `main_line_status = '无'` → Q2 必然得 20 分 → 三问大概率不通过 → 仓位强制压到 10%。

**严重程度**：这实际上是 **P0** 级别。三问定乾坤的核心 Q2（主线判断，35% 权重）因为字段来源错误而永远给出极低分。

**修复**：
```python
# L96: 改为从 step1 取 zhuxian
main_line_data = step1.get('zhuxian', [])
```

> **经进一步确认**：step2 的 result 确实没有 `zhuxian` 字段（step2 处理的是 lianban_data 的 tier 分组），但 step1 的 result 有 `zhuxian: fupan.get('zhuxian', [])`。这是一个明确的字段取错来源。

**→ 将此问题升级为 🔴 P0-③**

---

#### P2-③ `_predict_stocks()` 未使用 step5 的 `strength` 分数

**位置**：整个 `_predict_stocks()` 函数

**问题**：step5 的每个 candidate 有 `strength`（涨停强度 0-100）和 `B4_smoothness`（平滑度）等评分字段，step6 在做 T+1 预判时完全忽略了这些已计算的强度指标，只用了 `minute_pattern`（形态）和 `ban_tag`（连板高度）。

**影响**：step5 花了 912 行代码计算的强度因子体系（A/B/C 三组 11 因子），在 step6 中没有参与 T+1 预判置信度的计算。这意味着 step5 的打分结果只用于候选股排序（step5 内部的 `_composite_score`），但不影响 step6 的 T+1 判断。

**建议**：将 step5 的 `strength` 作为 T1Predictor 的输入之一，或用作 confidence 的调节因子。

---

#### P2-④ `_build_matrix()` 中表格列宽可能溢出

**位置**：L533-545

```python
lines.append(f"│ {p['item']:<15} │ {p['prediction']:<28} │ 置信:{p['confidence']} │")
```

**问题**：prediction 最大 28 字符，但 `_predict_emotion` 的 `pred` 字符串如"高潮退潮信号，高位股分批离场"有 14 个中文字符（28 字节），在终端中显示可能因为中英文混排导致对不齐。

---

## 三、数据源分析

### 3.1 上游数据字段映射（step1-5 → step6）

| step6 读取 | 来源 | 字段名 | 存在性 |
|-----------|------|--------|--------|
| `step1.get('date')` | step1 | `date` | ✅ |
| `step1.get('bottom_num', 0)` | step1 | `bottom_num` | ✅ |
| `step2.get('tier1', [])` | step2 | `tier1` | ✅ `List[str]` |
| **`step2.get('zhuxian', [])`** | step2 | **`zhuxian`** | ❌ **不存在！应从 step1 取** |
| `step3.get('qingxu')` | step3 | `qingxu` | ✅ |
| `step3.get('degree_market')` | step3 | `degree_market` | ✅ |
| `step3.get('top_rate')` | step3 | `top_rate` | ✅ |
| `step4.get('health_score', 50)` | step4 | `health_score` | ✅ |
| `step4.get('ladder', {})` | step4 | `ladder` | ✅ `Dict[tag, {name,tag,cnt,rate,stocks}]` |
| `step5.get('candidates', [])` | step5 | `candidates` | ✅ |

### 3.2 pain_effect_analyzer 字段依赖（fupan_data MongoDB）

| 字段 | 用途 | 风险 |
|------|------|------|
| `open_num` | 炸板家数 | ✅ fupan_data 必有 |
| `top_rate` | 封板率 | ✅ fupan_data 必有 |
| `long_ban` | 最高连板高度 | ⚠️ **可能不存在**，默认0导致否决失效 |
| `continue_top_num` | 连板中继数 | ⚠️ 可能不存在 |
| `damian` | 大面家数 | ⚠️ 可能不存在 |
| `yesterday_top_rate` | 昨日溢价 | ✅ |
| `highopen_rate` | 高开率 | ✅ |
| `up_num / down_num` | 涨跌家数 | ✅ |
| `amount / diff_amount` | 成交额 | ✅ |
| `ban1-ban7` | 连板家数 | ⚠️ 可能不存在（在新版 ladder 结构中） |

### 3.3 DataSource 依赖

| 方法 | MongoDB 集合 | 用途 |
|------|-------------|------|
| `ds.get_pain_scores()` | `pain_effect_scores` | 跨日趋势 history |
| `ds.save_pain_score()` | `pain_effect_scores` | 保存当日评分 |
| `ds.get_fupan(date)` | `vip_fupanwang.fupan_data` | 获取原始情绪数据 |

---

## 四、判断合理性评估

### 4.1 三问定乾坤（ThreeQuestions）— 4.3/5

**权重分配**：Q1 空间板 40% + Q2 主线 35% + Q3 亏钱效应 25%

**合理之处**：
- Q1 空间板最高权重 40%，梯队健康是短线生存的基础
- Q3 亏钱效应用 pain_effect_analyzer 的四维度评分，比旧的粗糙推断专业很多
- 时间维度趋势惩罚（-15/-10/-10）是好的风控设计

**问题**：
- Q2 的 `passed` 要求 `status == "明确"`，在当前数据源取错的情况下形同虚设
- Q3 的 `pain_trend='improving'` 会将分数拉到 70 以上（L156），但如果市场从 20 分恢复到 40 分（diff=+20），trend 是"上升"但绝对分仍然很低，不应给高分

### 4.2 情绪预判（_predict_emotion）— 4.0/5

**合理之处**：
- 5 阶段分别处理，逻辑清晰
- 高潮期区分"炸板≥10 退潮信号"和"无明显退潮"两种子场景

**问题**：
- 退潮期直接给"延续退潮，空仓观望"，置信度"高"——但退潮第一天和第五天应区别对待
- 冰点期"可能延续冰点或快速转修复"太模糊，没有给出具体判断标准

### 4.3 个股形态预判（_predict_stocks）— 4.2/5

**合理之处**：
- MorphologyMatrix 的 Facade 设计，形态分类 + T+1 预测一体化
- 有分钟数据和无分钟数据两条路径，优雅降级
- 6 板以上直接禁止进场（L391），硬门槛合理

**问题**：
- conf_floor 顺序依赖（P1-①）
- `can_enter` 只看 direction + confidence，没考虑 strength/量能/板块位置

### 4.4 仓位计划（_build_position_plan）— 4.5/5

**合理之处**：
- PositionRules 的 6 阶段仓位配置清晰
- 三问不通过 → 强制压到 10%
- 无合格候选 → 仓位归零

**问题**：
- `sector_strength=0.7` 硬编码（L437），与个股层面 L321 的 0.8/0.4 不一致
- 连板乘数 `lianban_multiplier` 在高位时加仓（1.5x），但高位应更谨慎而非更激进

### 4.5 亏钱效应分析（pain_effect_analyzer）— 4.5/5

**合理之处**：
- 四维度加权（涨停质量 35% 权重最高）符合逻辑
- 五条一票否决条件覆盖了极端风险场景
- ①b 高温市场警示是精细设计（区分否决和降权）

**问题**：
- `long_ban` 字段可能缺失（P1-④）
- trend 计算只看最近一天差值（diff>5 或 <-5），忽略更长期的移动平均

---

## 五、修复优先级总结

| 编号 | 级别 | 问题 | 影响 |
|------|------|------|------|
| **P0-③** | 🔴 | `step2.get('zhuxian')` 应为 `step1.get('zhuxian')` | Q2 永远低分，三问大概率不通过，仓位被压到 10% |
| **P0-①** | 🔴 | RiskController 导入但未使用 | 风报比/止损未接入预判流程 |
| **P0-②** | 🟡→🔴 | `_infer_trends()` 实为即时判断非趋势推断 | decelerating 误判可能导致三问 -15 分 |
| **P1-④** | 🟡 | fupan `long_ban` 字段可能不存在 | 一票否决条件 ① 失效 |
| **P1-①** | 🟡 | conf_floor 顺序依赖 bug | 同批次候选使用不同门槛 |
| **P1-②** | 🟡 | sector_strength 二元跳变 | 精细度不足 |
| **P1-③** | 🟡 | 高潮期 zhaban 判断依赖 step4 数据可用性 | 数据缺失时退潮信号丢失 |
| **P2-③** | 🟢 | step5 strength 未参与 T+1 预判 | 信息浪费 |
| **P2-①** | 🟢 | 操作清单展示数量硬编码 | 用户体验 |
| **P2-④** | 🟢 | 矩阵表格中英文混排对齐 | 显示美观 |

---

## 六、修复后预期评分

| 修复项 | 评分提升 |
|--------|---------|
| 修复 P0-③（zhuxian 来源） | +0.3 → 4.5 |
| 修复 P0-①（接入 RiskController） | +0.2 → 4.7 |
| 修复 P1-① + P1-④ | +0.1 → 4.8 |

**全部修复后预期：4.8/5**

---

## 七、对比 step5 分析的问题延续

| step5 问题 | step6 影响 | 状态 |
|-----------|-----------|------|
| `is_on_limit` 字段缺失（step5 的 `_compute_zhangting_strength` 无此字段） | step6 不读取此字段，无直接影响 | ✅ 不影响 |
| `m['price']`/`m['volume']` 无防御性取值 | step6 不直接读取分钟数据，通过 MorphologyMatrix 间接使用 | ⚠️ 间接影响 |
| step5 `strength` 评分未被 step6 使用 | 信息浪费 | P2-③ |
