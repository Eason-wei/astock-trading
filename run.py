"""run.py - A股交易体系统一入口===================================职责：  - 一键运行完整复盘 → 预判 → 验证闭环  - 串联 project/steps (8步) + cognition/ + decision/ + verify/  - 支持单个模块独立运行  - 输出结构化报告用法：  # 完整流程  python run.py --date 2026-04-20  # 仅预判  python run.py --step 6 --date 2026-04-20  # 仅验证  python run.py --step 7 --date 2026-04-20  # 仅认知查询  python run.py --query beliefs --keyword 龙头  python run.py --query chains --theme 冰点  python run.py --query weak_areas --active"""

import argparse
import json
import sys
from datetime import datetime, date
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from project.steps import (
    step1_global_scan,
    step2_main_line,
    step3_emotion_cycle,
    step4_lianban_health,
    step5_stock_filter,
    step6_t1_prediction,
    step7_verification,
    step8_closure,
)
from cognition import BeliefStore, CausalChainStore, WeakAreasStore, CognitionUpdater
from decision import MorphologyMatrix, PositionRules, ThreeQuestions, RiskController
from verify import LessonExtractor, PredictionVerifier


class TradingSystemRunner:
    """统一运行器"""

    STEPS = {
        1: ("全局扫描", step1_global_scan),
        2: ("主线分析", step2_main_line),
        3: ("情绪周期", step3_emotion_cycle),
        4: ("连板健康度", step4_lianban_health),
        5: ("成分股筛选", step5_stock_filter),
        6: ("T+1预判", step6_t1_prediction),
        7: ("验证", step7_verification),
        8: ("闭环", step8_closure),
    }

    def __init__(self, target_date: str = None):
        self.date = target_date or datetime.now().strftime("%Y-%m-%d")
        self.results = {}
        self.cog_updater = CognitionUpdater()
        self.mm = MorphologyMatrix()
        self.pr = PositionRules()
        self.tq = ThreeQuestions()
        self.rc = RiskController()
        self.verifier = PredictionVerifier()
        self.extractor = LessonExtractor()

    # ---- 运行入口 ----

    def run_full(self, verify_date: str = None) -> dict:
        """运行完整流程：T日复盘 → T+1预判 → T+1验证（如果提供verify_date）"""
        print(f"\n{'='*60}")
        print(f"  A股交易体系 | 日期: {self.date} | 完整流程{' | 验证日='+verify_date if verify_date else ''}")
        print(f"{'='*60}\n")

        from project.data.datasource import DataSource
        ds = DataSource()
        try:
            # Step 1-5: 数据采集
            for step_num in range(1, 6):
                self.results[step_num] = self._run_step_by_num(step_num)

            # Step 6: T+1预判
            self.results[6] = self._run_step_by_num(6)

            # Step 7-8: 验证（如果有verify_date）
            if verify_date:
                print(f"\n{'='*60}")
                print(f"  开始验证 - T+1={verify_date}")
                print(f"{'='*60}\n")
                self.results[7] = self._run_step7_impl(verify_date)
                self.results[8] = self._run_step8_impl()
        finally:
            ds.close()

        # 写入完整报告（含所有step输出，用于可复现性）
        self._write_full_report(verify_date)

        print(f"\n{'='*60}")
        print(f"  完整流程完成 | 共 {len(self.results)} 个步骤")
        print(f"{'='*60}")
        return self.results

    def _write_full_report(self, verify_date: str = None):
        """写入完整报告（含所有step数据，可复现）"""
        report_dir = Path(__file__).parent / 'project' / 'reports'
        report_dir.mkdir(parents=True, exist_ok=True)

        filename = f"full_report_{self.date.replace('-', '')}"
        if verify_date:
            filename += f"_verify_{verify_date.replace('-', '')}"
        report_path = report_dir / f"{filename}.json"

        # 只保留有效step结果（去掉error-only的）
        clean_results = {}
        for k, v in self.results.items():
            if isinstance(v, dict) and 'error' not in v:
                clean_results[k] = v

        report = {
            'date': self.date,
            'verify_date': verify_date,
            'steps_run': list(clean_results.keys()),
            'steps': clean_results,
        }

        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  [报告] 完整报告已写入: {report_path}")

    def run_step(self, step_num: int) -> dict:
        """运行单个步骤（使用正确的step签名）"""
        if step_num not in self.STEPS:
            print(f"[!] 无效步骤: {step_num}")
            return {}

        name, module = self.STEPS[step_num]
        print(f"\n[{step_num}/8] {name}...")

        try:
            # 每个step签名不同，必须按实际参数表调用
            result = self._run_step_by_num(step_num)
            self.results[step_num] = result
            print(f"    -> 完成 | keys={list(result.keys()) if isinstance(result, dict) else type(result).__name__}")
            return result
        except Exception as e:
            print(f"    -> 错误: {e}")
            import traceback; traceback.print_exc()
            self.results[step_num] = {"error": str(e)}
            return {}

    def _run_step_by_num(self, step_num: int) -> dict:
        """按step号分发到正确的实现（参考trading_system.py的调用方式）"""
        _, module = self.STEPS[step_num]  # 从STEPS映射获取module
        from project.data.datasource import DataSource

        ds = DataSource()
        try:
            if step_num == 1:
                snap = ds.get_date_snapshot_lite(self.date)
                return module.run(fupan=snap['fupan'], lianban=snap.get('lianban'))
            elif step_num == 2:
                snap = ds.get_date_snapshot_lite(self.date)
                return module.run(lianban=snap['lianban'], jiuyang=snap.get('jiuyang'))
            elif step_num == 3:
                snap = ds.get_date_snapshot_lite(self.date)
                return module.run(fupan=snap['fupan'])
            elif step_num == 4:
                snap = ds.get_date_snapshot_lite(self.date)
                return module.run(lianban=snap['lianban'])
            elif step_num == 5:
                snap = ds.get_date_snapshot_full(self.date)
                mysql_data = ds.get_mysql_minutes_fast(self.date)
                return module.run(lianban=snap['lianban'], jiuyang=snap.get('jiuyang'), mysql_data=mysql_data)
            elif step_num == 6:
                # Step6 依赖 step1-5 的结果；如果是首次单独运行，先跑 step1-5
                if 1 not in self.results:
                    for s in range(1, 6):
                        self.results[s] = self._run_step_by_num(s)
                step1 = self.results.get(1, {})
                step2 = self.results.get(2, {})
                step3 = self.results.get(3, {})
                step4 = self.results.get(4, {})
                step5 = self.results.get(5, {})
                return module.run(step1=step1, step2=step2, step3=step3, step4=step4, step5=step5)
            elif step_num == 7:
                raise ValueError("Step7 需要 verify_date，请用 python run.py --date X --verify Y")
            elif step_num == 8:
                raise ValueError("Step8 需要先跑完 Step7，请用 python run.py --date X --verify Y")
        finally:
            ds.close()

    # ---- 查询入口 ----

    def query(self, target: str, **kwargs) -> dict:
        """查询认知体系"""
        if target == "beliefs":
            store = BeliefStore()
            if keyword := kwargs.get("keyword"):
                return store.search(keyword)
            elif stage := kwargs.get("stage"):
                return store.query_by_stage(stage)
            else:
                return {"total": len(store.get_all_keys()), "keys": store.get_all_keys()[:10]}

        elif target == "chains":
            store = CausalChainStore()
            if theme := kwargs.get("theme"):
                return store.get_by_theme(theme)
            elif stage := kwargs.get("stage"):
                return store.get_by_stage(stage)
            else:
                return {"total": len(store.all_chains()), "themes": store.get_themes()}

        elif target == "weak_areas":
            store = WeakAreasStore()
            if kwargs.get("active_only"):
                return store.get_active()
            else:
                return store.get_statistics()

        elif target == "status":
            return self.cog_updater.get_system_status()

        else:
            return {"error": f"未知查询目标: {target}"}

    # ---- 验证入口 ----
    def _run_step7_impl(self, verify_date: str) -> dict:
        """Step7 验证循环"""
        _, module = self.STEPS[7]
        from project.data.datasource import DataSource
        ds = DataSource()
        try:
            t1_data = ds.get_t1_verification(verify_date)
            result = module.run(
                t1_actual=t1_data,
                predictions=self.results.get(6, {}).get('predictions', []),
                position_plan=self.results.get(6, {}).get('position_plan', {}),
                step6_stock_preds=self.results.get(6, {}).get('stock_predictions', []),
            )
            return result
        finally:
            ds.close()

    def _run_step8_impl(self) -> dict:
        """Step8 系统闭环"""
        _, module = self.STEPS[8]
        result = module.run(
            step7_result=self.results.get(7, {}),
            step1=self.results.get(1, {}),
            step2=self.results.get(2, {}),
            step3=self.results.get(3, {}),
            step4=self.results.get(4, {}),
            step5=self.results.get(5, {}),
            step6=self.results.get(6, {}),
        )
        return result

    def verify_and_update(self, predictions: list, actuals: list) -> dict:
        """验证预测并自动更新认知体系"""
        print(f"\n[验证] 验证 {len(predictions)} 个预测...")
        verify_results = self.verifier.verify_batch(predictions, actuals)

        # 统计
        stats = self.verifier.get_statistics(verify_results)
        print(f"    准确率: {stats['accuracy']:.1%} | 平均分: {stats['avg_score']}")

        # 提取教训并更新认知
        lessons = []
        for vr, pred, actual in zip(verify_results, predictions, actuals):
            lesson = self.extractor.extract(
                verify_result=vr,
                morphology=pred.get("morphology"),
                market_stage=pred.get("stage"),
                stock_name=pred.get("name"),
                prediction=pred,
            )
            lessons.append(lesson)

        stats["lessons"] = lessons
        return stats

    # ---- 辅助 ----

    def report(self) -> str:
        """生成简洁报告"""
        lines = [f"A股交易体系日报 {self.date}", "=" * 40]
        for step_num, result in self.results.items():
            name = self.STEPS.get(step_num, ("?", None))[0]
            if isinstance(result, dict) and "error" not in result:
                summary = json.dumps(result, ensure_ascii=False, indent=2)[:200]
                lines.append(f"\n[{step_num}] {name}:")
                lines.append(f"  {summary[:150]}...")
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="A股交易体系运行器")
    parser.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD")
    parser.add_argument("--verify", default=None, help="T+1验证日期 YYYY-MM-DD")
    parser.add_argument("--step", type=int, default=None, help="运行指定步骤")
    parser.add_argument("--query", default=None, help="查询: beliefs/chains/weak_areas/status")
    parser.add_argument("--keyword", default=None, help="查询关键词")
    parser.add_argument("--theme", default=None, help="查询theme")
    parser.add_argument("--stage", default=None, help="查询市场阶段")
    parser.add_argument("--active", dest="active_only", action="store_true", help="仅活跃薄弱环节")
    args = parser.parse_args()

    runner = TradingSystemRunner(args.date)

    if args.query:
        result = runner.query(args.query, keyword=args.keyword, theme=args.theme,
                            stage=args.stage, active_only=args.active_only)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.step:
        runner.run_step(args.step)
        print("\n" + runner.report())
    elif args.verify:
        # T日复盘 + T+1验证
        runner.run_full(verify_date=args.verify)
        print("\n" + runner.report())
    elif args.date:
        # T日复盘（仅预判，不验证）
        runner.run_full(verify_date=None)
        print("\n" + runner.report())
    else:
        runner.run_full()
        print("\n" + runner.report())


if __name__ == "__main__":
    main()
