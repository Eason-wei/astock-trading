"""
test_pain_effect_analyzer.py
==============================
测试目标：
  1. 数据源获取 — 真实从MongoDB拉取 fupan_data，逐字段检查
  2. 逻辑判断验证 — 用真实数据逐一验证每个维度的计算过程
  3. 边界条件 — 字段缺失、零值、极端值
  4. 一票否决触发 — 用合成极端数据验证5个否决条件
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from project.data.datasource import DataSource
from decision.pain_effect_analyzer import (
    run, print_report,
    _check_veto, _score_long_ban, _score_ladder,
    _score_seal_quality, _score_market_breadth,
    FIELD_DEFS,
)


def _inject_open_num(fupan: dict) -> dict:
    """确保 open_num 存在（测试用：直接取 MongoDB 字段，无需注入）"""
    d = dict(fupan)
    # open_num 直接来自 MongoDB fupan_data，无需推算
    return d


# ─────────────────────────────────────────────────────────
# 测试1：逐字段检查 MongoDB → fupan_data 的字段映射
# ─────────────────────────────────────────────────────────
def test_field_mapping():
    print("\n" + "="*60)
    print("【测试1】字段映射检查 — 从MongoDB拉取真实数据")
    print("="*60)

    ds = DataSource()
    dates = ["2026-04-21", "2026-04-20", "2026-04-17"]

    all_fields_found = {}
    for date in dates:
        fupan = ds.get_fupan(date)
        if not fupan:
            print(f"  ⚠️ {date} 无数据，跳过")
            continue

        print(f"\n  📅 {date} 原始字段（共{len(fupan)}个）：")
        for k, v in fupan.items():
            if k == "_id":
                continue
            def_hint = FIELD_DEFS.get(k, "— 未在pain模块定义 —")
            marker = " ✅" if k in FIELD_DEFS else " ⚠️"
            print(f"    {marker} {k} = {v!r:35s}  ← {def_hint}")
            all_fields_found[k] = v

    ds.close()

    print(f"\n  📊 pain_effect_analyzer.py 定义的字段（共{len(FIELD_DEFS)}个）：")
    for k, hint in FIELD_DEFS.items():
        if k.startswith("#"):
            continue
        if k in all_fields_found:
            print(f"    ✅ {k}")
        else:
            print(f"    ❌ {k}  ← MongoDB中未找到此字段！")

    mongo_only = [k for k in all_fields_found if k not in FIELD_DEFS and k != "_id"
                  and k not in ("zhuxian", "qingxu", "status")]
    if mongo_only:
        print(f"\n  ⚠️ MongoDB有但pain模块未使用的字段（共{len(mongo_only)}个）：")
        for k in mongo_only:
            print(f"    ⚠️  {k}")


# ─────────────────────────────────────────────────────────
# 测试2：逻辑判断验证 — 用真实数据手工核算
# ─────────────────────────────────────────────────────────
def test_logic_with_real_data():
    print("\n" + "="*60)
    print("【测试2】逻辑判断验证 — 逐维度手工核算（真实MongoDB数据）")
    print("="*60)

    ds = DataSource()
    dates = ["2026-04-21", "2026-04-20", "2026-04-17"]

    for date in dates:
        fupan_raw = ds.get_fupan(date)
        if not fupan_raw:
            print(f"  ⚠️ {date} 无数据")
            continue

        fupan = _inject_open_num(fupan_raw)

        print(f"\n  📅 {date}")
        print(f"     open_num={fupan.get('open_num')}  top_num={fupan.get('top_num')}  "
              f"ban1={fupan.get('ban1')}")
        print(f"     bottom_num={fupan.get('bottom_num')}  damian={fupan.get('damian')}  "
              f"top_rate={fupan.get('top_rate')}  yesterday_top_rate={fupan.get('yesterday_top_rate')}")
        print(f"     long_ban={fupan.get('long_ban')}  continue_top_num={fupan.get('continue_top_num')}  "
              f"highopen_rate={fupan.get('highopen_rate')}")
        print(f"     up_num={fupan.get('up_num')}  down_num={fupan.get('down_num')}  "
              f"diff_amount={fupan.get('diff_amount')}")
        print(f"     ban1={fupan.get('ban1')}  ban2={fupan.get('ban2')}  ban3={fupan.get('ban3')}  "
              f"ban4={fupan.get('ban4')}  ban5={fupan.get('ban5')}  ban6={fupan.get('ban6')}  ban7={fupan.get('ban7')}")

        override, veto_reasons = _check_veto(fupan)
        d1 = _score_long_ban(fupan)
        d2 = _score_ladder(fupan)
        d3 = _score_seal_quality(fupan)
        d4 = _score_market_breadth(fupan)

        if override:
            final = override
        else:
            w1, w2, w3, w4 = 0.25, 0.25, 0.35, 0.15
            final = round(d1["score"]*w1 + d2["score"]*w2 + d3["score"]*w3 + d4["score"]*w4, 1)

        print(f"\n     ── 各维度得分 ──")
        print(f"     龙头高位股={d1['score']} | 连板生态={d2['score']} | "
              f"涨停质量={d3['score']} | 市场整体={d4['score']}")
        print(f"     综合评分={final}")

        if d1.get("signals"):
            for s in d1["signals"]: print(f"       龙头信号: {s}")
        if d1.get("warnings"):
            for w in d1["warnings"]: print(f"       龙头警告: {w}")

        brk = d2.get("detail", {})
        print(f"       晋级率={brk.get('ban1_to_ban2_rate')}%  "
              f"断层={brk.get('gap_count')}处  "
              f"最大连续断层={brk.get('max_consecutive_gap')}")
        if d2.get("warnings"):
            for w in d2["warnings"]: print(f"       连板警告: {w}")

        if d3.get("warnings"):
            for w in d3["warnings"]: print(f"       涨停质量警告: {w}")

        if d4.get("warnings"):
            for w in d4["warnings"]: print(f"       市场整体警告: {w}")

        if override:
            print(f"     ⚠️ 一票否决触发! score→{override}  原因: {veto_reasons}")

        print(f"     → {print_report(run(fupan))}")

    ds.close()


# ─────────────────────────────────────────────────────────
# 测试3：字段缺失容错测试
# ─────────────────────────────────────────────────────────
def test_missing_fields():
    print("\n" + "="*60)
    print("【测试3】字段缺失容错测试")
    print("="*60)

    ds = DataSource()
    base = ds.get_fupan("2026-04-21")
    base = _inject_open_num(base)
    ds.close()

    critical_fields = ["open_num", "bottom_num", "top_rate", "up_num", "down_num",
                       "long_ban", "ban1", "ban2", "yesterday_top_rate",
                       "highopen_rate", "continue_top_num", "damian"]

    for field in critical_fields:
        test_data = {k: v for k, v in base.items() if k != field}
        try:
            result = run(test_data)
            print(f"    ✅ 去掉 {field:<28} → score={result['score']}")
        except Exception as e:
            print(f"    ❌ 去掉 {field:<28} → 报错: {e}")


# ─────────────────────────────────────────────────────────
# 测试4：一票否决5个条件逐一触发
# ─────────────────────────────────────────────────────────
def test_veto_conditions():
    print("\n" + "="*60)
    print("【测试4】一票否决5个条件逐一触发验证")
    print("="*60)

    base = {
        "date": "2026-04-21",
        "open_num": 10,                          # 炸板家数（直接来自MongoDB）
        "top_rate": 75, "long_ban": 4,
        "bottom_num": 5, "damian": 2,
        "yesterday_top_rate": 2.0, "highopen_rate": 60,
        "continue_top_num": 8,
        "up_num": 2000, "down_num": 2500,
        "amount": 20000, "diff_amount": 0,
        "ban1": 50, "ban2": 10, "ban3": 2, "ban4": 1, "ban5": 0, "ban6": 0, "ban7": 0,
    }

    veto_tests = [
        {
            "name": "① 龙头高位炸板（炸板≥10 + 封板率≤75% + 最高板≥5）",
            "override": 25.0,
            "patch": {"open_num": 13, "top_rate": 75, "long_ban": 5},
            # open_num=13≥10, top_rate=75%≤75%, long_ban=5≥5 → 触发否决
        },
        {
            "name": "② 昨日涨停指数跌幅>3%",
            "override": 30.0,
            "patch": {"yesterday_top_rate": -4.0},
        },
        {
            "name": "③ 涨跌比<0.3",
            "override": 20.0,
            "patch": {"up_num": 300, "down_num": 2000},
        },
        {
            "name": "④ 跌停≥15 + 封板率<50%",
            "override": 20.0,
            "patch": {"bottom_num": 15, "top_rate": 45},
        },
        {
            "name": "⑤ 封板率<40% + 涨跌比<0.5",
            "override": 30.0,
            "patch": {"top_rate": 35, "up_num": 800, "down_num": 2500},
        },
    ]

    for vt in veto_tests:
        data = {**base, **vt["patch"]}
        override, reasons = _check_veto(data)
        result = run(data)

        score_ok = abs(result["score"] - vt["override"]) <= 1.0
        veto_ok = result["veto_triggered"] and override == vt["override"]
        status = "✅" if (score_ok and veto_ok) else "❌"
        print(f"  {status} {vt['name']}")
        print(f"       期望override={vt['override']} 实际override={override}  score={result['score']}")
        print(f"       原因: {reasons}")


# ─────────────────────────────────────────────────────────
# 测试5：zdb字符串解析问题检测
# ─────────────────────────────────────────────────────────
def test_zdb_parsing():
    print("\n" + "="*60)
    print("【测试5】zdb字段解析问题")
    print("="*60)

    ds = DataSource()
    for date in ["2026-04-21", "2026-04-20", "2026-04-17"]:
        fupan = ds.get_fupan(date)
        zdb = fupan.get("zdb", "0:0")
        print(f"  {date}: zdb={zdb!r}  (type={type(zdb).__name__})")
        # 分析zdb格式
        if isinstance(zdb, str) and ":" in zdb:
            parts = zdb.split(":")
            if len(parts) == 2:
                print(f"         解析: '{parts[0].strip()}' : '{parts[1].strip()}'")
    ds.close()


# ─────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_field_mapping()
    test_logic_with_real_data()
    test_missing_fields()
    test_veto_conditions()
    test_zdb_parsing()

    print("\n" + "="*60)
    print("测试完成")
    print("="*60)
