"""
强结构交易系统 - 主程序
=======================
框架：T日观察 → 预判T+1 → 验证 → 反思 → 修正 → 闭环

Usage:
    from trading_system import TradingSystem
    sys = TradingSystem()
    sys.run('2026-04-03')              # T日复盘
    sys.run('2026-04-03', verify='2026-04-07')  # T日复盘 + T+1验证
"""
import sys as _sys
import traceback
from pathlib import Path as _Path

# 兼容两种运行方式：直接运行 或 作为包导入
try:
    from .data.datasource import DataSource
    from .steps import step1_global_scan
    from .steps import step2_main_line
    from .steps import step3_emotion_cycle
    from .steps import step4_lianban_health
    from .steps import step5_stock_filter
    from .steps import step6_t1_prediction
    from .steps import step7_verification
    from .steps import step8_closure
except ImportError:
    # 直接运行时，添加parent到path
    _sys.path.insert(0, str(_Path(__file__).parent))
    from data.datasource import DataSource
    import steps.step1_global_scan as step1_global_scan
    import steps.step2_main_line as step2_main_line
    import steps.step3_emotion_cycle as step3_emotion_cycle
    import steps.step4_lianban_health as step4_lianban_health
    import steps.step5_stock_filter as step5_stock_filter
    import steps.step6_t1_prediction as step6_t1_prediction
    import steps.step7_verification as step7_verification
    import steps.step8_closure as step8_closure


class TradingSystem:
    """
    强结构交易系统主类

    每个Step都是独立模块，可单独运行，也可串联运行。
    认知逻辑嵌入在各Step中，不是硬编码规则。
    """

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.ds = DataSource()
        self.results = {}  # 存储各Step输出

    def close(self):
        self.ds.close()

    def __del__(self):
        # P1-B修复：兜底关闭，防止用户不用with/不调close()导致连接泄漏
        if hasattr(self, 'ds'):
            try:
                self.ds.close()
            except Exception:
                pass

    # ===== 核心运行方法 =====

    def run(self, date: str, verify_date: str = None) -> dict:
        """
        主运行方法

        Args:
            date: T日日期（如 '2026-04-03'）
            verify_date: T+1日期（如 '2026-04-07'），可选
        """
        print(f"\n{'='*60}")
        print(f"  强结构交易系统 - T日={date}  {'验证日='+verify_date if verify_date else '(仅预判)'}")
        print(f"{'='*60}\n")

        # ===== Step 1-5: T日数据采集 =====
        self.results['step1'] = self._run_step1(date)
        self.results['step2'] = self._run_step2(date)
        self.results['step3'] = self._run_step3(date)
        self.results['step4'] = self._run_step4(date)
        self.results['step5'] = self._run_step5(date)

        # ===== Step 6: T+1预判 =====
        self.results['step6'] = self._run_step6()

        # ===== Step 7-8: 验证（如果有T+1数据）=====
        if verify_date:
            print(f"\n{'='*60}")
            print(f"  开始验证 - T+1={verify_date}")
            print(f"{'='*60}\n")
            self.results['step7'] = self._run_step7(verify_date)
            self.results['step8'] = self._run_step8()

        return self.results

    def run_step(self, step_num: int, date: str, verify_date: str = None) -> dict:
        """单独运行某个Step

        Args:
            step_num: 步骤号(1-8)
            date: T日日期
            verify_date: T+1验证日期（仅step7需要）
        """
        step_map = {
            1: (self._run_step1, '全局扫描'),
            2: (self._run_step2, '主线分析'),
            3: (self._run_step3, '情绪周期'),
            4: (self._run_step4, '连板健康度'),
            5: (self._run_step5, '成分股筛选'),
            6: (self._run_step6, 'T+1预判'),
            7: (self._run_step7, '验证循环'),      # P1-①新增
            8: (self._run_step8, '系统闭环'),      # P1-①新增
        }
        if step_num not in step_map:
            raise ValueError(f"Step {step_num} 不存在（支持1-8）")
        fn, name = step_map[step_num]
        print(f"\n{'='*40}")
        print(f"  Step {step_num}: {name}")
        print(f"{'='*40}\n")
        if step_num == 7:
            if not verify_date:
                raise ValueError("Step7 需要传入 verify_date 参数")
            return fn(verify_date)
        elif step_num == 8:
            raise ValueError("Step8 需要先运行Step7，请用 engine.run(date, verify_date)")
        else:
            return fn(date)

    # ===== 各Step实现（统一异常处理） =====

    def _safe_step(self, name: str, fn, *args, **kwargs):
        """统一异常处理包装器：任一步失败不阻断后续步骤"""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            print(f"[{name}] ⚠️异常: {e}")
            print(f"  → 继续执行后续步骤（{name}返回空字典）")
            traceback.print_exc()
            return {}

    def _run_step1(self, date: str) -> dict:
        snapshot = self.ds.get_date_snapshot_lite(date)   # P2-①修复：Step1-4用lite，跳过MySQL查询
        return self._safe_step('Step1', self._run_step1_impl, date, snapshot)

    def _run_step1_impl(self, date: str, snapshot: dict) -> dict:
        fupan = snapshot['fupan']
        lianban = snapshot['lianban']
        result = step1_global_scan.run(fupan=fupan, lianban=lianban)
        self._print_step('Step1 全局扫描', result)
        return result

    def _run_step2(self, date: str) -> dict:
        snapshot = self.ds.get_date_snapshot_lite(date)   # P2-①修复
        return self._safe_step('Step2', self._run_step2_impl, date, snapshot)

    def _run_step2_impl(self, date: str, snapshot: dict) -> dict:
        lianban = snapshot['lianban']
        jiuyang = snapshot['jiuyang']
        fupan = snapshot['fupan']
        result = step2_main_line.run(lianban=lianban, jiuyang=jiuyang, fupan=fupan)
        self._print_step('Step2 主线分析', result)
        return result

    def _run_step3(self, date: str) -> dict:
        snapshot = self.ds.get_date_snapshot_lite(date)   # P2-①修复
        return self._safe_step('Step3', self._run_step3_impl, date, snapshot)

    def _run_step3_impl(self, date: str, snapshot: dict) -> dict:
        fupan = snapshot['fupan']
        result = step3_emotion_cycle.run(fupan=fupan)
        self._print_step('Step3 情绪周期', result)
        return result

    def _run_step4(self, date: str) -> dict:
        snapshot = self.ds.get_date_snapshot_lite(date)   # P2-①修复
        return self._safe_step('Step4', self._run_step4_impl, date, snapshot)

    def _run_step4_impl(self, date: str, snapshot: dict) -> dict:
        lianban = snapshot['lianban']
        result = step4_lianban_health.run(lianban=lianban)
        self._print_step('Step4 连板健康度', result)
        return result

    def _run_step5(self, date: str) -> dict:
        snapshot = self.ds.get_date_snapshot_full(date)    # P2-①修复：Step5才查MySQL
        mysql_data = self.ds.get_mysql_minutes_fast(date)
        return self._safe_step('Step5', self._run_step5_impl, date, snapshot, mysql_data)

    def _run_step5_impl(self, date: str, snapshot: dict, mysql_data: dict) -> dict:
        lianban = snapshot['lianban']
        jiuyang = snapshot['jiuyang']
        result = step5_stock_filter.run(
            lianban=lianban,
            jiuyang=jiuyang,
            mysql_data=mysql_data
        )
        self._print_step('Step5 成分股筛选', result)
        return result

    def _run_step6(self) -> dict:
        return self._safe_step('Step6', self._run_step6_impl)

    def _run_step6_impl(self) -> dict:
        # 从 step1 的 date 字段获取目标日期，查询原始 fupan 供 pain_effect_analyzer 使用
        date = self.results['step1'].get('date')
        fupan_raw = self.ds.get_fupan(date) if date else None
        result = step6_t1_prediction.run(
            step1=self.results['step1'],
            step2=self.results['step2'],
            step3=self.results['step3'],
            step4=self.results['step4'],
            step5=self.results['step5'],
            fupan=fupan_raw,
            ds=self.ds,
        )
        # 每次运行后保存 pain 评分到 MongoDB
        pe = result.get('pain_effect')
        if pe and date:
            self.ds.save_pain_score(date, pe['score'], pe['level'])
        self._print_step('Step6 T+1预判', result)
        return result

    def _run_step7(self, verify_date: str) -> dict:
        t1_data = self.ds.get_t1_verification(verify_date)
        return self._safe_step('Step7', self._run_step7_impl, verify_date, t1_data)

    def _run_step7_impl(self, verify_date: str, t1_data: dict) -> dict:
        # P0-②修复：传入 step6_stock_preds，否则个股验证循环永远跳过
        # 透传 T日市场阶段（step3.qingxu），用于 accuracy_tracker 记录
        market_stage = self.results['step3'].get('qingxu', 'unknown')
        result = step7_verification.run(
            t1_actual=t1_data,
            predictions=self.results['step6'].get('predictions', []),
            position_plan=self.results['step6'].get('position_plan', {}),
            step6_stock_preds=self.results['step6'].get('stock_predictions', []),
            market_stage=market_stage,   # ← T日阶段，用于tracker记录
        )
        self._print_step('Step7 验证循环', result)
        return result

    def _run_step8(self) -> dict:
        return self._safe_step('Step8', self._run_step8_impl)

    def _run_step8_impl(self) -> dict:
        result = step8_closure.run(
            step7_result=self.results['step7'],
            step1=self.results['step1'],
            step2=self.results['step2'],
            step3=self.results['step3'],
            step4=self.results['step4'],
            step5=self.results['step5'],
            step6=self.results['step6'],
        )
        self._print_step('Step8 系统闭环', result)
        return result

    # ===== 工具方法 =====

    def _print_step(self, name: str, result: dict):
        if not self.verbose:
            return
        print(f"\n{'─'*40}")
        print(f"  {name}")
        print(f"{'─'*40}")
        # 打印关键字段
        for k, v in result.items():
            if k in ['date', 'verdict', 'suggestion', 'position_plan',
                     'matrix', 'score_rate', 'summary', 'candidates',
                     'ladder', 'zhuxian', 'tier1', 'qingxu', 'degree_market',
                     'health_score', 'warnings', 'filter_summary']:
                if v is not None:
                    print(f"  {k}: {v}")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ====== 快捷运行函数 ======
def quick_review(date: str, verify_date: str = None) -> dict:
    """
    一行命令运行完整流程
    """
    with TradingSystem() as engine:
        return engine.run(date, verify_date)
