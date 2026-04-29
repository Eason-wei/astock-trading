"""
快捷运行脚本
Usage:
    python run_review.py 2026-04-03                 # T日复盘+预判
    python run_review.py 2026-04-03 2026-04-07      # T日复盘+T+1验证
    python run_review.py --step 1 2026-04-03        # 单步执行
"""
import sys
import json
import traceback
from pathlib import Path

# 添加项目根目录到path
sys.path.insert(0, str(Path(__file__).parent))
import trading_system as ts_module
TradingSystem = ts_module.TradingSystem
quick_review = ts_module.quick_review


def main():
    args = sys.argv[1:]

    if not args:
        print("Usage:")
        print("  python run_review.py 2026-04-03                  # T日复盘+预判")
        print("  python run_review.py 2026-04-03 2026-04-07      # T日复盘+T+1验证")
        print("  python run_review.py --step 1 2026-04-03         # 单步执行")
        return

    output_dir = Path(__file__).parent / 'reports'
    output_dir.mkdir(exist_ok=True)

    try:
        # 单步执行模式
        if args[0] == '--step':
            step_num = int(args[1])
            date = args[2]
            with TradingSystem() as engine:
                result = engine.run_step(step_num, date)
            return

        # 完整流程
        date = args[0]
        verify_date = args[1] if len(args) > 1 else None

        with TradingSystem(verbose=True) as engine:
            results = engine.run(date, verify_date)

        # 保存结果
        filename = f"report_{date.replace('-', '')}"
        if verify_date:
            filename += f"_verify_{verify_date.replace('-', '')}"

        output_file = output_dir / f"{filename}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n✅ 报告已保存: {output_file}")

    except Exception as e:
        print(f"\n❌ 运行异常: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
