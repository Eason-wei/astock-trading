"""lesson_extractor.py - 从验证结果中提取教训，触发认知更新"""

from typing import Dict, Any, Optional
from datetime import datetime
from cognition import CognitionUpdater

class LessonExtractor:
    """从验证结果提取教训并触发认知更新"""

    # 教训模板（建议1/4修订：增加profitable/loss，修正accurate）
    LESSON_TEMPLATES = {
        "overshoot": "预判幅度偏保守，实际超出预期，可能存在市场加速行为",
        "undershoot": "预判幅度偏乐观，实际大幅低于预期，应重新评估形态有效性",
        "wrong_direction": "方向判断错误，需检查形态分类是否准确或阶段判断是否正确",
        "profitable": "盈利正确，市场确认了形态有效性",          # 建议4：盈利写盘
        "loss": "方向正确但实际亏损，可能存在买点质量问题或板块轮动",  # 建议4：方向陷阱
        "accurate": "预判准确，验证了当前认知的有效性",            # 建议4：保留但不加因果链
        "limit_down": "遭遇跌停极端行情，需加入防跌停止损规则",
    }

    # 通用根因模板（按优先级排列）
    _ROOT_CAUSE_TEMPLATES = [
        # 形态 × 阶段 规则
        ('C1冲高回落', '高潮期', 'C1+高潮期=地雷，高阶段C1失败概率被系统性低估', '立即执行：高潮期遇到C1形态禁止开仓'),
        ('D2尾盘急拉', '高潮期', 'D2+高潮期=加速见顶信号，尾盘急拉次日往往低开', '高潮期D2形态：必须等到调整确认后才考虑介入'),
        ('D2尾盘急拉', '退潮期', '退潮期D2尾盘急拉=主力最后出货，次日大概率低开', '退潮期D2=立即止损，不抄底'),
        ('C1冲高回落', '退潮期', '退潮期C1冲高回落=双杀格局，板块情绪退潮+个股形态差', '退潮期C1必须回避，不抄底'),
        ('E1普通波动', '高潮期', '高潮期E1振幅不足，板块高潮日E1往往跑输指数', '高潮期E1降低仓位，不超20%'),
        ('E2宽幅震荡', '退潮期', '退潮期E2宽幅震荡=多空分歧大，方向不明', '退潮期E2方向不明，禁止重仓'),
        # 跟风/共振 规则
        ('跟风', '高潮期', '高潮期跟风股溢价减少，龙头调整时跟风跌幅更大', '高潮期只做龙头，不做跟风'),
        ('共振', '退潮期', '共振失效：退潮期板块共振带动反而加大出货力度', '退潮期不追共振板块'),
        # 通用规则
        ('wrong_direction', None, '方向判断错误：形态分类或阶段判断可能有误', '重新校验形态分类规则和阶段判断逻辑'),
        ('undershoot', None, '幅度保守：实际走势超出预判，说明市场强于预期', '适当扩大预判幅度区间，或降低置信度'),
        ('overshoot', None, '幅度过度：实际走势弱于预判，可能存在诱多行为', '检查是否存在主力拉高出货特征'),
    ]
    _ROOT_CAUSE_KEYWORDS = {
        "形态判断错误": ["形态", "分类", "E1", "F1", "D2", "C1", "冲高回落", "尾盘急拉", "脉冲"],
        "阶段判断错误": ["高潮", "退潮", "冰点", "阶段", "情绪", "deg"],
        "跟风问题": ["跟风", "助攻", "孤身", "小弟"],
        "板块效应": ["板块", "共振", "独立", "分化"],
        "时机问题": ["开盘", "尾盘", "午盘", "时机", "次日"],
        "仓位问题": ["仓位", "重仓", "轻仓", "满仓"],
    }

    def __init__(self):
        self.updater = CognitionUpdater()
        # 建议4：盈利预测写盘计数器 {morph_stage_key: count_since_last_write}
        self._profit_counter: Dict[str, int] = {}
        self.PROFIT_WRITE_INTERVAL = 5  # 每5次盈利同形态才写一次因果链

    def _should_skip_profitable(self, morphology: str, market_stage: str, lesson_key: str) -> bool:
        """
        建议4：判断盈利预测是否应跳过写盘。

        策略：
        - lesson_key='profitable'（盈利正确）：每5次同类形态才写一次
        - lesson_key='accurate'（准确）：始终跳过写盘（不加因果链）
        - lesson_key='loss'（方向陷阱）：始终写盘
        - 其他（wrong_direction/overshoot/undershoot/limit_down）：始终写盘
        """
        # accurate 始终不加因果链
        if lesson_key == 'accurate':
            return True
        # profitable 才走频率控制
        if lesson_key != 'profitable':
            return False  # wrong_direction/overshoot/undershoot/limit_down → 每次都写

        key = f"{morphology}_{market_stage or '无阶段'}"
        count = self._profit_counter.get(key, 0) + 1
        self._profit_counter[key] = count
        if count >= self.PROFIT_WRITE_INTERVAL:
            self._profit_counter[key] = 0  # 写完后重置
            return False  # 达到间隔，允许写
        return True  # 未达间隔，跳过写盘

    def extract(
        self,
        verify_result,
        morphology: str = None,
        market_stage: str = None,
        stock_name: str = None,
        prediction: Dict[str, Any] = None,
        root_cause_hint: str = None,
        data_quality: str = None,   # D-①新增：估算数据降权标记
    ) -> Dict[str, Any]:
        """从验证结果提取教训并触发认知更新（建议4：分层降噪）

        认知分层策略：
        - 宏观层（emotion）：每日只记1条市场整体判断（step7情绪验证）
        - 板块层（sector）：每周最多3条主线切换规律
        - 个股层（stock）：每月最多10条重大偏差，其余正确预测不写盘

        写盘频率控制（建议4）：
        - profitable（盈利正确）：累计5次同类形态才写一次因果链
        - accurate（准确）：不写因果链，只更新信念
        - loss（方向陷阱）：每次都写
        - wrong_direction/overshoot/undershoot：每次都写
        """
        vr = verify_result

        # ── 建议4：分层控制 ─────────────────────────────
        # 1. 宏观层（emotion）: 正常处理
        # 2. 个股层（stock）: profitable且不是方向陷阱 → 检查写盘频率
        lesson_type = 'stock'
        if morphology and self._should_skip_profitable(morphology, market_stage, vr.lesson_key):
            # 建议4：盈利预测达到写盘频率上限时，只记录lesson但跳过写盘
            return {
                "lesson": self.LESSON_TEMPLATES.get(vr.lesson_key, "")[:60],
                "root_cause": root_cause_hint or self._infer_root_cause(vr, morphology, market_stage),
                "suggested_fix": "",
                "update_result": {'skipped': True, 'reason': 'freq_cap', 'lesson_type': lesson_type},
                "verify_result": {"correct": vr.correct, "score": vr.score, "lesson_key": vr.lesson_key},
            }

        # P1-②幂等检查：同一天同一只股票的同一形态不重复更新
        # Key 逻辑必须与 step7_verification._verify_stocks 里的 receive_verification_result 保持一致
        # prediction_key = f"T+1预测_{morphology or stock_name or '未知'}"
        # belief_key = f"验证_{prediction_key}_{today}"
        # → 统一用 morphology or stock_name or '未知'
        today = datetime.now().strftime('%Y%m%d')
        morph_or_name = morphology or stock_name or '未知'
        # P5-fix: belief_key 必须包含 stage，否则同形态不同阶段会互相覆盖
        stage_part = market_stage or '未知阶段'
        belief_key = f"验证_T+1预测_{morph_or_name}_{stage_part}_{today}"
        existing = self.updater.beliefs._cache.get(belief_key)
        if existing:
            # 已有该 key 的记录（当天已验证过），跳过写盘但返回 lesson
            lesson_text = self.LESSON_TEMPLATES.get(vr.lesson_key, "")
            if morphology:
                lesson_text = f"[{morphology}]{lesson_text}"
            if market_stage:
                lesson_text = f"[{market_stage}]{lesson_text}"
            return {
                "lesson": lesson_text,
                "root_cause": root_cause_hint or self._infer_root_cause(vr, morphology, market_stage),
                "suggested_fix": "",
                "update_result": {'skipped': True, 'reason': 'duplicate_key', 'belief_key': belief_key},
                "verify_result": {"correct": vr.correct, "score": vr.score, "lesson_key": vr.lesson_key},
            }

        # 生成教训文本
        template = self.LESSON_TEMPLATES.get(vr.lesson_key, "")
        lesson = template
        if morphology:
            lesson = f"[{morphology}]{lesson}"
        if market_stage:
            lesson = f"[{market_stage}]{lesson}"

        # 推断根本原因
        root_cause = root_cause_hint or self._infer_root_cause(vr, morphology, market_stage)

        # 建议修复（仅对失败预测生成）
        fix = self._suggest_fix(vr, morphology, market_stage) if not vr.correct else ""

        # 构造trigger/mechanism/outcome（仅对失败预测构造因果链）
        trigger = f"{morphology or '未知形态'}_{market_stage or '未知阶段'}"
        mechanism = root_cause
        outcome = f"{vr.lesson_key}: {vr.detail}"

        # D-①: 估算数据降权（confidence<1时lesson文本需加标记）
        est_flag = (data_quality == 'estimated')
        if est_flag:
            lesson += " [估算数据，请谨慎参考]"
        conf = 0.3 if est_flag else None

        # 调用认知更新器（仅对失败预测调用）
        if vr.correct:
            # 验证通过：只记录信念，不添加因果链（无需簿记因果链）
            update_result = self.updater.receive_verification_result(
                prediction_key=f"T+1预测_{morphology or stock_name or '未知'}",
                prediction=prediction.get("expected_change", "未知") if prediction else "未知",
                actual=f"{vr.deviation:.1f}%" if hasattr(vr, "deviation") else "未知",
                correct=True,
                market_stage=market_stage,
                lesson=lesson,
                morphology_type=morphology,
                confidence=conf,   # D-①
            )
        else:
            # 验证失败：完整因果链 + 薄弱环节记录
            update_result = self.updater.receive_verification_result(
                prediction_key=f"T+1预测_{morphology or stock_name or '未知'}",
                prediction=prediction.get("expected_change", "未知") if prediction else "未知",
                actual=f"{vr.deviation:.1f}%" if hasattr(vr, "deviation") else "未知",
                correct=False,
                market_stage=market_stage,
                lesson=lesson,
                morphology_type=morphology,
                trigger=trigger,
                mechanism=mechanism,
                outcome=outcome,
                root_cause=root_cause,
                suggested_fix=fix,
                confidence=conf,   # D-①
            )

        return {
            "lesson": lesson,
            "root_cause": root_cause,
            "suggested_fix": fix,
            "update_result": update_result,
            "verify_result": {
                "correct": vr.correct,
                "score": vr.score,
                "lesson_key": vr.lesson_key,
            },
        }

    def _infer_root_cause(self, vr, morphology: str, market_stage: str) -> str:
        """
        推断根本原因 [P1-2 改进版]

        匹配顺序：
          1. 精确匹配：morphology × market_stage 模板
          2. 形态泛匹配：morphology × any stage 模板
          3. lesson_key 泛匹配：wrong_direction/undershoot/overshoot 通用模板
          4. 兜底：关键词分类推断
        """
        if vr.lesson_key == "accurate":
            return "无根本问题"

        if vr.lesson_key == "limit_down":
            return "极端行情未纳入风控"

        # Stage 1: 精确匹配 morphology × market_stage
        for tmpl_morph, tmpl_stage, tmpl_cause, _ in self._ROOT_CAUSE_TEMPLATES:
            if tmpl_morph == morphology and tmpl_stage == market_stage:
                return tmpl_cause

        # Stage 2: 形态泛匹配（stage=None）
        for tmpl_morph, tmpl_stage, tmpl_cause, _ in self._ROOT_CAUSE_TEMPLATES:
            if tmpl_morph == morphology and tmpl_stage is None:
                return tmpl_cause

        # Stage 3: lesson_key 泛匹配（wrong_direction/undershoot/overshoot）
        for tmpl_morph, tmpl_stage, tmpl_cause, _ in self._ROOT_CAUSE_TEMPLATES:
            if tmpl_morph == vr.lesson_key and tmpl_stage is None:
                return tmpl_cause

        # Stage 4: 兜底——从现有关键词分类推断
        for category, keywords in self._ROOT_CAUSE_KEYWORDS.items():
            if any(kw in (morphology or '') or kw in (vr.detail or '') for kw in keywords):
                return f"{category}：{(morphology or vr.detail or '未知形态')[:20]}"
        return "多因素叠加，需进一步分析"

    def _suggest_fix(self, vr, morphology: str, market_stage: str) -> str:
        """
        建议修复方法 [P1-2 改进版]

        匹配顺序：同 _infer_root_cause
        """
        # Stage 1: 精确匹配 morphology × market_stage
        for tmpl_morph, tmpl_stage, _, tmpl_fix in self._ROOT_CAUSE_TEMPLATES:
            if tmpl_morph == morphology and tmpl_stage == market_stage:
                return tmpl_fix

        # Stage 2: 形态泛匹配
        for tmpl_morph, tmpl_stage, _, tmpl_fix in self._ROOT_CAUSE_TEMPLATES:
            if tmpl_morph == morphology and tmpl_stage is None:
                return tmpl_fix

        # Stage 3: lesson_key 泛匹配
        for tmpl_morph, tmpl_stage, _, tmpl_fix in self._ROOT_CAUSE_TEMPLATES:
            if tmpl_morph == vr.lesson_key and tmpl_stage is None:
                return tmpl_fix

        # 兜底
        if vr.lesson_key == "limit_down":
            return "加入跌停=立即止损规则，仓位上限10%"

        if morphology and market_stage:
            return f"重新校验{morphology}形态在{market_stage}阶段的置信度参数"

        return "持续跟踪形态x阶段的有效性，定期修正参数"

    def extract_batch(self, verify_results: list) -> list:
        """批量提取教训"""
        return [self.extract(**vr) for vr in verify_results]
