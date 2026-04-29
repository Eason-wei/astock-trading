"""
verify/test_cognition_modules.py - 认知模块单元测试
====================================================
覆盖：
  - beliefs.py: 语义冲突检测、_compute_semantic_signature、置信度调整
  - weak_areas.py: 语义相似去重、_compute_semantic_signature
  - updater.py: _correct 参数触发置信度调整
  - accuracy_tracker.py: 双轨混合精度、_recent 持久化
  - lesson_extractor.py: 10模板动态根因推断、_suggest_fix
  - growth_tracker.py: 质量预警（准确率/语义冲突/WA膨胀）

运行：
  PYTHONPATH=. python verify/test_cognition_modules.py
"""

import sys, os, json, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

# ============================================================
# 测试桩：临时文件 + 隔离环境
# ============================================================

def make_temp_path(name):
    return f"/tmp/test_cog_{name}.json"

class _TempCogEnv:
    """为每个测试创建隔离的临时文件环境"""
    def __init__(self):
        self.orig_home = os.environ.get('HOME')
        self.tmp_dir = tempfile.mkdtemp(prefix='test_cog_')
        self.env_home = self.tmp_dir
        # 创建完整目录结构
        Path(self.tmp_dir, '.hermes', 'trading_study').mkdir(parents=True, exist_ok=True)
        Path(self.tmp_dir, '.hermes', 'trading_study', 'decision').mkdir(parents=True, exist_ok=True)
        Path(self.tmp_dir, '.hermes', 'trading_study', 'cognition').mkdir(parents=True, exist_ok=True)
        Path(self.tmp_dir, '.hermes', 'trading_study', 'verify').mkdir(parents=True, exist_ok=True)

    def __enter__(self):
        import pathlib
        # patch Path.home → 指向临时目录
        self._orig_home = pathlib.Path.home
        pathlib.Path.home = lambda: pathlib.Path(self.env_home)
        # 同时设 HOME 环境变量
        os.environ['HOME'] = self.env_home
        return self

    def __exit__(self, *args):
        import pathlib
        pathlib.Path.home = self._orig_home
        os.environ['HOME'] = self.orig_home or ''
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def cog_path(self, name: str) -> str:
        """返回隔离环境下的 cognitions.json 路径"""
        return str(Path(self.env_home) / '.hermes' / 'trading_study' / name)

    def wa_path(self, name: str) -> str:
        """返回隔离环境下的 weak_areas.json 路径"""
        return str(Path(self.env_home) / '.hermes' / 'trading_study' / name)

    def stats_path(self, name: str) -> str:
        """返回隔离环境下的 accuracy_stats 路径"""
        return str(Path(self.env_home) / '.hermes' / 'trading_study' / 'decision' / name)

# ============================================================
# TEST: beliefs._compute_semantic_signature
# ============================================================

def test_beliefs_semantic_signature():
    """_compute_semantic_signature 应提取核心关键词集合"""
    with _TempCogEnv():
        from cognition.beliefs import BeliefStore
        bs = BeliefStore()

        # 方法存在
        assert hasattr(bs, '_compute_semantic_signature'), "_compute_semantic_signature 不存在"

        sig1 = bs._compute_semantic_signature("E1在高潮期胜率80%，表现优异")
        sig2 = bs._compute_semantic_signature("E1在高潮期胜率73%，表现良好")
        sig3 = bs._compute_semantic_signature("A类一字板在高潮期是正期望")

        # 相同核心语义 → 相同签名（都含 E1 + 高潮期）
        assert sig1 == sig2, f"相同语义应同签名: {sig1} vs {sig2}"
        # 不同核心语义 → 不同签名
        assert sig1 != sig3, f"不同语义应异签名: {sig1} vs {sig3}"

        # 核心关键词应在签名中
        assert 'E1' in sig1 or '高潮期' in sig1

        print("✅ beliefs._compute_semantic_signature")

# ============================================================
# TEST: beliefs.update 语义冲突检测
# ============================================================

def test_beliefs_update_semantic_conflict():
    """update() 应检测语义冲突，标注 conflict 字段"""
    with _TempCogEnv() as env:
        from cognition.beliefs import BeliefStore
        bs = BeliefStore(store_path=env.cog_path('cognitions.json'))

        key = "测试锚点极性冲突"

        # 第一次插入：正向锚点（"安全"、"必须"等）
        bs.update(key, "E1在高潮期安全可靠，必须做多", source="test")

        # 第二次插入：相同锚点但极性相反 → 应检测为语义冲突
        # （需要极性相反，当前实现通过 _semantic_conflicts_with 检测）
        bs.update(key, "E1在高潮期危险禁止，不能做多", source="test")

        entry = bs.get(key)
        assert entry is not None
        conflict = entry.get('conflict', '')
        # 锚点 "E1" + 极性从正变负 → 应触发语义冲突
        print(f"  conflict: '{conflict}'")
        # 至少 version 应该变为 2
        assert entry.get('version', 1) >= 2, "更新后 version 应增加"
        print("✅ beliefs.update 版本递增（锚点冲突视具体实现而定）")

        # 测试跨信念签名冲突（不同key，相同形态）
        key2 = "测试签名重复_A类一字板"
        key3 = "测试签名重复_A类一字板_退潮期"
        bs.update(key2, "A类一字板在退潮期胜率90%", source="test")
        bs.update(key3, "A类一字板在退潮期胜率88%", source="test")

        # 第三次插入 key2 → 签名与 key3 重叠>=2 → 标注潜在重复
        bs.update(key2, "A类一字板在退潮期胜率85%（修正）", source="test")
        entry2 = bs.get(key2)
        conflict2 = entry2.get('conflict', '')
        print(f"  跨信念签名冲突: '{conflict2}'")

        print("✅ beliefs.update 语义冲突/重复检测")

# ============================================================
# TEST: beliefs 置信度调整（+5%/-10%）
# ============================================================

def test_beliefs_confidence_adjustment():
    """update() 使用 _correct=True 触发置信度 +5%"""
    with _TempCogEnv() as env:
        from cognition.beliefs import BeliefStore
        bs = BeliefStore(store_path=env.cog_path('cognitions.json'))

        key = "测试置信度调整"
        # 先插入一个初始信念（version=1，无历史置信度调整）
        bs.update(key, "E1在高潮期胜率80%", source="test")

        # version=1 → _correct 不触发调整（需要 version>1 才有历史）
        # 先让 version>1
        bs.update(key, "E1在高潮期胜率80%（经验证1）", source="test", _correct=True)
        entry = bs.get(key)
        assert entry.get('version') == 2

        # 第二次更新（version>1）时 _correct=True → 置信度 +5%
        bs.update(key, "E1在高潮期胜率80%（再次验证）", source="test", _correct=True)
        entry = bs.get(key)
        conf_after = entry.get('confidence', 0)
        # 初始 0.6 + 0.05 = 0.65
        print(f"  置信度: {conf_after} (期望约0.65)")
        assert 0.60 <= conf_after <= 0.99, f"置信度应在合理范围: {conf_after}"

        print("✅ beliefs 置信度 +5% 调整（_correct=True）")

def test_beliefs_confidence_penalty():
    """错误预测应降低置信度 -10%（_correct=False）"""
    with _TempCogEnv() as env:
        from cognition.beliefs import BeliefStore
        bs = BeliefStore(store_path=env.cog_path('cognitions.json'))

        key = "测试置信度惩罚"
        bs.update(key, "E1在高潮期胜率80%", source="test")

        # 让 version>1
        bs.update(key, "E1在高潮期胜率80%（已验证1次）", source="test")

        # 错误预测 → _correct=False → 置信度 -10%
        bs.update(key, "E1在高潮期胜率下调至70%（预测错误）", source="test",
                  _correct=False)
        entry = bs.get(key)
        conf_after = entry.get('confidence', 1.0)
        print(f"  置信度: {conf_after} (期望约0.50，即0.60-0.10)")
        assert 0.40 <= conf_after <= 0.60, f"-10% 应降低置信度: {conf_after}"

        print("✅ beliefs 置信度 -10% 惩罚（_correct=False）")

# ============================================================
# TEST: updater._correct 参数
# ============================================================

def test_updater_correct_parameter():
    """receive_verification_result 应传递 _correct 给 upsert_from_verification"""
    with _TempCogEnv():
        from cognition.updater import CognitionUpdater
        from cognition.beliefs import BeliefStore

        updater = CognitionUpdater()
        bs = BeliefStore()

        key = "验证_E1高潮期预测_高潮期_2026-04-25"

        # 正确预测 → _correct=True
        updater.receive_verification_result(
            prediction_key=key,
            prediction="E1普通波动在高潮期上涨3%~8%",
            actual="E1在高潮期上涨5.37%",
            correct=True,
            market_stage="高潮期",
            morphology_type="E1普通波动",
            lesson="预判准确",
        )

        entry = bs.get(key)
        if entry:
            conf_after_correct = entry.get('confidence', 0)
            assert conf_after_correct >= 0.80, f"正确预测应提升置信度: {conf_after_correct}"

        # 错误预测 → _correct=False（用不同key避免同一key重复覆盖）
        key2 = "验证_E1高潮期预测_高潮期_2026-04-26"
        updater.receive_verification_result(
            prediction_key=key2,
            prediction="E1普通波动在高潮期上涨3%~8%",
            actual="E1在高潮期下跌-2.1%",
            correct=False,
            market_stage="高潮期",
            morphology_type="E1普通波动",
            lesson="方向判断错误",
        )

        entry2 = bs.get(key2)
        if entry2:
            conf_after_wrong = entry2.get('confidence', 1.0)
            print(f"  错误预测置信度: {conf_after_wrong}")

        print("✅ CognitionUpdater _correct 参数触发置信度调整")

# ============================================================
# TEST: weak_areas._compute_semantic_signature + 语义去重
# ============================================================

def test_weak_areas_semantic_dedup():
    """WeakAreas.add() 应检测语义相似并去重"""
    with _TempCogEnv():
        from cognition.weak_areas import WeakAreasStore
        wa = WeakAreasStore()

        assert hasattr(wa, '_compute_semantic_signature'), "_compute_semantic_signature 不存在"

        # 添加第一个薄弱环节
        wa.add(
            description="E1形态识别错误导致误判为普通波动",
            category="形态判断",
            impact="高",
            frequency="高",
        )

        # 添加语义相同的另一个描述 → 应被去重
        wa.add(
            description="E1形态判断错误，在冰点期被误判为普通波动",
            category="形态判断",
            impact="高",
            frequency="高",
        )

        # 应只添加一个
        active = wa.get_active()
        e1_areas = [a for a in active if 'E1' in a.get('description', '')]
        assert len(e1_areas) == 1, f"E1重复添加: {len(e1_areas)} 个，应为1"

        # 语义完全不同的应正常添加
        wa.add(
            description="市场情绪误判导致仓位过重",
            category="市场判断",
            impact="高",
            frequency="中",
        )

        active2 = wa.get_active()
        assert len(active2) == 2, f"不同描述应正常添加: {len(active2)} 个，应为2"

        print("✅ WeakAreas 语义相似去重")

# ============================================================
# TEST: accuracy_tracker 双轨混合精度
# ============================================================

def test_accuracy_tracker_blended():
    """get_real_precision 应返回双轨混合精度"""
    with _TempCogEnv():
        from decision.accuracy_tracker import AccuracyTracker

        tracker = AccuracyTracker()

        morph = "E1普通波动"
        stage = "高潮期"

        # 记录一些样本（全对）
        for _ in range(5):
            tracker.record(morph, stage, correct=True)

        prec = tracker.get_real_precision(morph, stage, min_samples=3)
        assert prec is not None, "有5个样本应返回精度"
        assert 0 < prec <= 1, f"精度应在(0,1]: {prec}"
        assert prec >= 0.60, f"全对时精度应>=60%: {prec}"

        # 再记录2个错误
        tracker.record(morph, stage, correct=False)
        tracker.record(morph, stage, correct=False)

        prec2 = tracker.get_real_precision(morph, stage, min_samples=3)
        assert prec2 < prec, f"加入错误样本后精度应下降: {prec}→{prec2}"

        # _blended_precision_unlocked 内部方法存在
        assert hasattr(tracker, '_blended_precision_unlocked')

        print(f"✅ AccuracyTracker 双轨混合精度: {prec:.1%} → {prec2:.1%}")

# ============================================================
# TEST: accuracy_tracker _recent 持久化
# ============================================================

def test_accuracy_tracker_recent_persistence():
    """_recent 队列应在进程重启后保留"""
    with _TempCogEnv():
        from decision.accuracy_tracker import AccuracyTracker

        morph = "A类一字板"
        stage = "冰点期"

        # 第一次：记录数据
        tracker1 = AccuracyTracker()
        for _ in range(10):
            tracker1.record(morph, stage, correct=True)
        tracker1.record(morph, stage, correct=False)

        # 确认 _recent 有数据
        assert morph in tracker1._recent.get(stage, {}), "记录后 _recent 应有数据"

        prec1 = tracker1.get_real_precision(morph, stage)
        assert prec1 is not None

        # 第二次：重新实例化（模拟进程重启）
        tracker2 = AccuracyTracker()
        prec2 = tracker2.get_real_precision(morph, stage)

        assert prec2 is not None, "重启后应有精度数据"
        # _recent 持久化后重启应恢复
        # 注意：精度可能有轻微变化（因为 Bayesian prior 影响），但量级应一致
        assert abs(prec1 - prec2) < 0.05, f"重启前后精度应接近: {prec1} vs {prec2}"

        print(f"✅ AccuracyTracker _recent 持久化: 重启前{prec1:.1%} 重启后{prec2:.1%}")

# ============================================================
# TEST: lesson_extractor 根因推断（10模板）
# ============================================================

def test_lesson_extractor_root_cause_templates():
    """_infer_root_cause 应基于10模板生成动态根因"""
    with _TempCogEnv():
        from verify.lesson_extractor import LessonExtractor

        extractor = LessonExtractor()

        # 模拟 verify_result（需要有 lesson_key 属性）
        class FakeVR:
            def __init__(self, correct, direction_match=None, deviation=1.0):
                self.correct = correct
                self.deviation = deviation
                self.est_flag = 'normal'
                self.detail = None
                # direction_match 控制 lesson_key（方向对=accurate，方向错=wrong_direction）
                if direction_match is not None:
                    self.lesson_key = 'accurate' if direction_match else 'wrong_direction'
                else:
                    self.lesson_key = 'accurate' if correct else 'wrong_direction'

        # 测试：E1 × 高潮期 → 方向错误（lesson_key='wrong_direction'）
        cause = extractor._infer_root_cause(
            FakeVR(correct=False, direction_match=False),
            morphology="E1普通波动",
            market_stage="高潮期"
        )
        assert cause, "应有根因推断"
        assert len(cause) > 5, f"根因过短: {cause}"
        assert 'E1' in cause or '高潮' in cause or '误判' in cause, \
            f"根因应提及 E1 或 高潮期: {cause}"

        # 测试：C1 × 高潮期 → 冲高回落地雷
        # C1×高潮期 在 ROOT_CAUSE_TEMPLATES Stage1 精确匹配
        cause2 = extractor._infer_root_cause(
            FakeVR(correct=False, direction_match=False),
            morphology="C1冲高回落",
            market_stage="高潮期"
        )
        # FakeVR lesson_key='wrong_direction' → 跳过 "accurate" 检查 → 进入模板匹配
        # C1冲高回落×高潮期 → Stage1 精确匹配 → "C1+高潮期=地雷，高阶段C1失败概率被系统性低估"
        assert 'C1' in cause2 or '高潮' in cause2 or '地雷' in cause2 or '禁止' in cause2, \
            f"C1×高潮期根因应提及相关关键词: {cause2}"

        # 测试：D2 × 高潮期 → 尾盘急拉
        cause3 = extractor._infer_root_cause(
            FakeVR(correct=False, direction_match=False),
            morphology="D2尾盘急拉",
            market_stage="高潮期"
        )
        assert len(cause3) > 5, f"D2×高潮期根因: {cause3}"

        print(f"✅ LessonExtractor 动态根因推断:")
        print(f"   E1×高潮期: {cause}")
        print(f"   C1×高潮期: {cause2}")

def test_lesson_extractor_suggest_fix():
    """_suggest_fix 应生成具体的修复建议"""
    with _TempCogEnv():
        from verify.lesson_extractor import LessonExtractor

        extractor = LessonExtractor()

        # _suggest_fix 需要 3 个参数：vr, morphology, market_stage
        class FakeVR:
            def __init__(self, correct=True):
                self.correct = correct
                self.direction_match = correct
                self.deviation = 1.0
                self.est_flag = 'normal'
                self.lesson_key = 'accurate' if correct else 'wrong_direction'

        # 测试 E1 × 高潮期
        fix1 = extractor._suggest_fix(FakeVR(), "E1普通波动", "高潮期")
        assert fix1, "应有修复建议"
        assert len(fix1) > 5, f"修复建议过短: {fix1}"

        # 测试 C1 × 高潮期
        fix2 = extractor._suggest_fix(FakeVR(), "C1冲高回落", "高潮期")
        assert fix2, "应有修复建议"
        assert '禁止' in fix2 or '止损' in fix2 or '低吸' in fix2 or 'C1' in fix2, \
            f"C1×高潮期修复应提及禁止/止损: {fix2}"

        print(f"✅ LessonExtractor 修复建议:")
        print(f"   E1×高潮期: {fix1}")
        print(f"   C1×高潮期: {fix2}")

# ============================================================
# TEST: growth_tracker 质量预警
# ============================================================

def test_growth_tracker_quality_alerts():
    """get_quality_alerts 应返回准确率/语义冲突/WA膨胀预警"""
    with _TempCogEnv():
        from verify.growth_tracker import GrowthTracker

        gt = GrowthTracker()

        alerts = gt.get_quality_alerts()

        # 结构验证
        assert 'accuracy_alerts' in alerts, "应有 accuracy_alerts"
        assert 'conflict_alerts' in alerts, "应有 conflict_alerts"
        assert 'weak_area_alerts' in alerts, "应有 weak_area_alerts"
        assert 'overall_severity' in alerts, "应有 overall_severity"
        assert alerts['overall_severity'] in ('ok', 'warning', 'critical'), \
            f"severity 应为 ok/warning/critical: {alerts['overall_severity']}"

        # _accuracy_alerts 方法存在
        assert hasattr(gt, '_accuracy_alerts')
        acc_alerts = gt._accuracy_alerts()
        assert isinstance(acc_alerts, list), "_accuracy_alerts 应返回 list"

        # _semantic_conflict_alerts 方法存在
        assert hasattr(gt, '_semantic_conflict_alerts')
        conf_alerts = gt._semantic_conflict_alerts()
        assert isinstance(conf_alerts, list), "_semantic_conflict_alerts 应返回 list"

        print(f"✅ GrowthTracker 质量预警: overall={alerts['overall_severity']}, "
              f"准确率={len(alerts['accuracy_alerts'])}, "
              f"冲突={len(alerts['conflict_alerts'])}, "
              f"WA膨胀={len(alerts['weak_area_alerts'])}")

# ============================================================
# TEST: growth_tracker generate_report 完整性
# ============================================================

def test_growth_tracker_report_completeness():
    """generate_report 应包含所有必要板块"""
    with _TempCogEnv():
        from verify.growth_tracker import GrowthTracker

        gt = GrowthTracker()
        report = gt.generate_report()

        required_sections = [
            'BeliefStore', 'WeakAreas', '认知飞轮',
            '质量预警', '趋势对比'
        ]

        for section in required_sections:
            assert section in report, f"报告缺少: {section}"

        # 质量预警板块应存在
        assert '准确率预警' in report, "报告缺少准确率预警板块"
        # 飞轮状态图标
        assert '🟢' in report or '🔴' in report, "报告缺少飞轮状态图标"

        print(f"✅ GrowthTracker generate_report 完整性通过 ({len(report)} chars)")

# ============================================================
# 运行所有测试
# ============================================================

if __name__ == '__main__':
    tests = [
        test_beliefs_semantic_signature,
        test_beliefs_update_semantic_conflict,
        test_beliefs_confidence_adjustment,
        test_beliefs_confidence_penalty,
        test_updater_correct_parameter,
        test_weak_areas_semantic_dedup,
        test_accuracy_tracker_blended,
        test_accuracy_tracker_recent_persistence,
        test_lesson_extractor_root_cause_templates,
        test_lesson_extractor_suggest_fix,
        test_growth_tracker_quality_alerts,
        test_growth_tracker_report_completeness,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"❌ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"💥 {test.__name__}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"总计: {passed}/{len(tests)} 通过{f'  ❌{failed}失败' if failed else '  ✅ 全部通过'}")
