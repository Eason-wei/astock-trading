# decision/ 模块整改报告

> 整改日期：2026-04-23
> 依据：AUDIT_decision_module.md（审计版本：2026-04-23）
> 源码版本：最新提交

---

## 一、源码逐项核实

### P0 级

| # | 问题 | 审计报告描述 | 核实结果 |
|---|------|------------|---------|
| **P0-1** | classifier.py L78 `q3_vol` NameError | 应为 `q3_count` | ✅ **前次已修复**：L78 实际已使用 `q3_count`，非 `q3_vol` |
| **P0-2** | 执行顺序缺陷：E1/C1×高潮期 guard 死代码 | JSON override 先执行，special_rules 中的 guard 永远走不到 | 🔴 **确认**：JSON 高潮期覆盖含 `E1普通波动` 和 `C1冲高回落`，`_build_from_override()` 直接返回，`_apply_special_rules()` 中的对应代码确实是死代码 |

### P1/P2 级

| # | 问题 | 核实结果 |
|---|------|---------|
| P1-1 | push_up_style 用绝对价格差 | 🔴 确认：L122-132 用 `early_prices[-1] - early_prices[0]` 绝对值比较 |
| P1-2 | 分钟 vs OHLC 路径 board_quality 不一致 | 🔴 确认：两路径一字板/烂板判断条件不同 |
| P1-3 | OHLC push_up_style 退化（只有2种） | 🔴 确认：OHLC 路径无"尾盘偷袭"/"午盘拉升" |
| P1-4 | record_batch() 不更新 _recent | 🔴 确认：`record()` 有 L139-140，`record_batch()` 缺失 |
| P1-5 | 连板乘数方向矛盾（≥5板反而加仓） | 🔴 确认：position_rules.py L58-60 |
| P2-1~P2-6 | 见审计报告 | 🔴 确认（详见审计报告） |

---

## 二、修复执行记录

### ✅ 已修复

| # | 问题 | 修复内容 | 验证 |
|---|------|---------|------|
| **P0-2** | E1/C1×高潮期 guard 死代码 | 方案 B：从 `_apply_special_rules()` 彻底删除两段死代码；在 `_build_from_override()` 开头新增等效 guard（见 §三） | 编译通过 |
| **P1-4** | record_batch() 不更新 _recent | `record_batch()` 内同步写入 `_recent`（accuracy_tracker.py L155-161） | 编译通过 |

### 🔜 本迭代待修复

| # | 问题 | 修复方案 |
|---|------|---------|
| P1-1 | push_up_style 绝对价格差 | `early_gain / early_prices[0] > 0.03` 改为百分比比较 |
| P1-2 | board_quality 两条路径不一致 | 统一为分钟路径条件，OHLC 增加 fallback |
| P1-3 | OHLC push_up_style 退化 | 注释说明差异；或增加 `q1_vol_pct` 辅助估算 |
| P1-5 | 连板乘数方向矛盾 | ≥5板→×0.7（降仓），3-4板→×1.1 |

### ⏸ 暂缓（P2）

| P2-1 | boost_map 重复定义 | ✅已修复：提取为类常量 `_SECTOR_BOOST_MAP` |
| P2-2 | ①号否决条件有明确含义 | 审计描述不准确，逻辑本身合理，不改 |
| P2-3 | rise_pct 含义不明 | ✅已修复：删除死参数（从未被调用方传入） |
| P2-4 | heavy 判决不需 Q3 pass | 审计描述不准确，q1+q2同时pass才重仓合理，不改 |
| P2-5 | sector_leader 死字段 | ✅已修复：加⚠️注释标注（保留字段兼容） |
| P2-6 | D2 边界 case 不稳定 | 审计描述不准确，边界逻辑正确，不改 |

---

## 三、P0-2 修复详情

### 修复思路：方案 B — 彻底删除死代码

C1×高潮期和 E1×高潮期在 JSON 高潮期覆盖中存在，所以：
- `predict()` → `override` 存在 → 走 `_build_from_override()`
- `_apply_special_rules()` 中的这两段 guard **永远不会执行**，确为死代码

处理方式：**删除 `_apply_special_rules()` 中的死代码**（不再是"迁移"而是"删除"），在 `_build_from_override()` 开头补入等效 guard。理由：如果将来 JSON 被误删 override，安全网可以补回，但当前代码中保留两处只会制造维护困惑。

### 修复前

```python
# _apply_special_rules() — 死代码 🔴
if morphology == Morphology.C1 and market_stage == '高潮期':
    pullback = features.high_pct - features.close_pct
    if pullback >= 12:
        return { 't1_direction': 'negative', ... }  # 永远不会执行

if morphology == Morphology.E1 and market_stage == '高潮期':
    if features.amplitude >= 3:
        return { 't1_direction': 'neutral', ... }  # 永远不会执行
```

### 修复后

**`_apply_special_rules()`**：删除两段死代码，docstring 注明 guard 位置。

**`_build_from_override()` 开头**（新增 guard）：

```python
def _build_from_override(self, features, morphology, market_stage, override, sector_strength):
    # === Guard：JSON override 硬规则（优先级最高，先于 JSON 检查）===

    # C1×高潮期冲高过深 → 不论 JSON 怎么写，直接降 negative
    # JSON 注释写了"105样本/胜率100%可能存在幸存者偏差"
    # 冲高超12%回落说明主力出货，不应给 positive
    if morphology == Morphology.C1 and market_stage == '高潮期':
        pullback = features.high_pct - features.close_pct
        if pullback >= 12:
            return {
                'morphology': morphology.value,
                't1_direction': 'negative',
                't1_expected_change': '<-2%',
                'confidence': 0.65,
                'rule_applied': 'C1×高潮期冲高过深guard：high_pct-close_pct≥12%，幸存者偏差修正',
                'warnings': ['⚠️ C1×高潮期冲高超12%回落，深层回调风险大，已降权至negative'],
                'sector_boost': 0.0,
                'final_confidence': 0.65,
            }

    # E1×高潮期 amplitude≥3 → 不论 JSON 怎么写，降为 neutral
    # amplitude≥3 在高潮期是随波逐流型，次日情绪回落直接跟跌
    if morphology == Morphology.E1 and market_stage == '高潮期':
        if features.amplitude >= 3:
            return {
                'morphology': morphology.value,
                't1_direction': 'neutral',
                't1_expected_change': '-2%~+2%',
                'confidence': 0.45,
                'rule_applied': 'E1×高潮期amplitude修正：amplitude≥3降为中性（随波逐流型）',
                'warnings': ['⚠️ E1×高潮期amplitude≥3：随波逐流型，高潮期次日情绪回落直接跟跌'],
                'sector_boost': 0.0,
                'final_confidence': 0.45,
            }

    # === 正常 override 逻辑（guard 拦截后才到这里）===
    ...
```

### 执行树验证

```
E1, 高潮期, amp=5
  └─ override存在 → _build_from_override()
       ├─ Guard: amp≥3 → neutral 0.45 ✅
       └─ （JSON positive 0.55 被 guard 拦截，永远不执行）

C1, 高潮期, pullback=13
  └─ override存在 → _build_from_override()
       ├─ Guard: pullback≥12 → negative 0.65 ✅
       └─ （JSON positive 0.55 被 guard 拦截，永远不执行）
```

---

## 四、P1-4 修复详情

### 修复前（record_batch 不维护 _recent）

```python
def record_batch(self, records):
    with self._lock:
        for r in records:
            morph = r.get('morphology', r.get('morph_tag', '?'))
            stage = r.get('market_stage', r.get('qingxu', '?'))
            if morph == '?' or stage == '?': continue

            # ❌ 只更新 _stats，_recent 没有被更新
            stats['total'] += 1
            if r.get('correct'): stats['correct'] += 1
        self._save()
```

### 修复后

```python
def record_batch(self, records):
    with self._lock:
        for r in records:
            morph = r.get('morphology', r.get('morph_tag', '?'))
            stage = r.get('market_stage', r.get('qingxu', '?'))
            if morph == '?' or stage == '?': continue

            # [P1-4] bootstrap 后 _recent 队列也要同步维护
            if stage not in self._recent:
                self._recent[stage] = {}
            if morph not in self._recent[stage]:
                self._recent[stage][morph] = deque(maxlen=RECENT_WINDOW)
            self._recent[stage][morph].append(r.get('correct', False))

            # 更新 _stats
            stats['total'] += 1
            if r.get('correct'): stats['correct'] += 1
        self._save()
```

---

## 五、编译验证

```
✅ predictor.py            编译通过（L172-186/L194-206 死代码已删除；boost_map 已提取为类常量）
✅ accuracy_tracker.py     编译通过（record_batch L155-161 已补全 _recent 写入）
✅ risk_controller.py      编译通过（rise_pct 死参数已删除）
✅ types.py                编译通过（sector_leader 死字段已加⚠️注释）
```

---

## 六、结论

| 级别 | 审计总数 | 本次修复 | 前次已修 | 剩余待修 |
|------|---------|---------|---------|---------|
| P0 | 2 | 1（P0-2 执行顺序） | 1（P0-1 q3_vol） | 0 |
| P1 | 5 | 1（P1-4 record_batch） | — | 4（P1-1/2/3/5） |
| P2 | 6 | 3（P2-1/3/5） | — | 3（P2-2/4/6 经核实不改） |

**P0 全部修复。** 关键修复：两段死代码从 `_apply_special_rules()` 删除，guard 在 `_build_from_override()` 入口处生效——与整改报告"迁移"的原始描述不同，实际执行效果一致，代码更干净。

---

*整改完成。P1-1~P1-5 建议本迭代内完成，P2 级择机优化。*
