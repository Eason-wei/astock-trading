#!/usr/bin/env python3
"""
auto_check_and_fix.py — 每20分钟自动检测项目状态，有bug则尝试修复
运行方式: python auto_check_and_fix.py
"""
import subprocess
import json
import os
import sys
import re
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))
os.chdir(str(BASE))

REPORT = []

def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    REPORT.append(line)

def run(cmd, cwd=None, timeout=120):
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd or BASE,
                         capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT", -1

def check_syntax():
    """检查所有Python文件语法"""
    log("🔍 检查语法...")
    py_files = list(BASE.glob("**/*.py"))
    bad = []
    for f in py_files:
        if '.venv' in f.parts or '__pycache__' in str(f):
            continue
        _, stderr, code = run(f"python3 -m py_compile {f}")
        if code != 0:
            bad.append(f"{f.name}: {stderr[:100]}")
    if bad:
        log(f"  ❌ 语法错误: {bad[:3]}")
        return False
    log("  ✅ 语法OK")
    return True

def check_tests():
    """运行测试"""
    log("🧪 运行测试...")
    # test_morphology_matrix_54.py 直接执行（非 pytest）
    out1, err1, code1 = run(
        f"source ~/.hermes/venv/bin/activate && "
        f"PYTHONPATH={BASE} python3 verify/test_morphology_matrix_54.py 2>&1 | tail -5",
        timeout=120
    )
    t54_pass = "全部通过" in out1
    t54_line = out1.strip().split('\n')[-1] if out1.strip() else "no output"
    log(f"  test_morphology_matrix_54: {t54_line} {'✅' if t54_pass else '❌'}")

    # test_cognition_modules.py 直接执行
    out2, err2, code2 = run(
        f"source ~/.hermes/venv/bin/activate && "
        f"PYTHONPATH={BASE} python3 verify/test_cognition_modules.py 2>&1 | tail -3",
        timeout=120
    )
    t2_pass = "12/12" in out2 or "全部通过" in out2
    t2_line = out2.strip().split('\n')[-1] if out2.strip() else "no output"
    log(f"  test_cognition_modules: {t2_line} {'✅' if t2_pass else '❌'}")

    all_pass = t54_pass and t2_pass
    return all_pass, t54_line, t2_line

def check_imports():
    """检查关键模块能否正常导入"""
    log("📦 检查模块导入...")
    critical = [
        "from decision.accuracy_tracker import AccuracyTracker",
        "from decision.predictor import T1Predictor",
        "from cognition.beliefs import BeliefStore",
        "from cognition.weak_areas import WeakAreasStore",
        "from verify.lesson_extractor import LessonExtractor",
        "from verify.growth_tracker import GrowthTracker",
    ]
    results = {}
    for imp in critical:
        name = imp.split("import ")[-1]
        code = f"import sys; sys.path.insert(0,'{BASE}'); {imp}; print('OK')"
        out, err, rc = run(f'python3 -c "{code}"')
        ok = rc == 0 and "OK" in out
        results[name] = ok
        log(f"  {'✅' if ok else '❌'} {name}")
    return all(results.values()), results

def check_critical_files():
    """检查关键文件是否存在"""
    log("📁 检查关键文件...")
    files = [
        "run_real.py",
        "decision/accuracy_tracker.py",
        "decision/predictor.py",
        "cognition/beliefs.py",
        "cognition/weak_areas.py",
        "cognition/updater.py",
        "verify/lesson_extractor.py",
        "verify/growth_tracker.py",
        "verify/test_morphology_matrix_54.py",
        "project/steps/step7_verification.py",
    ]
    missing = [f for f in files if not (BASE / f).exists()]
    if missing:
        log(f"  ❌ 缺失文件: {missing}")
        return False
    log("  ✅ 所有关键文件存在")
    return True

def check_beliefs_signature():
    """检查 beliefs.py 是否有 _semantic_conflicts_with 方法引用不存在的函数"""
    log("🔍 检查 beliefs.py 方法完整性...")
    beliefs = BASE / "cognition" / "beliefs.py"
    content = beliefs.read_text()
    
    # 检查 _semantic_conflicts_with 是否存在
    has_method = "_semantic_conflicts_with" in content
    calls_extract = "_extract_claims(" in content
    
    if has_method and calls_extract:
        # 检查 _extract_claims 是从哪里来的
        import_match = re.search(r'from\s+\S+\s+import\s+(.*)', content)
        if import_match:
            imports = import_match.group(1)
            if '_extract_claims' not in imports:
                log("  ⚠️  _semantic_conflicts_with 引用 _extract_claims() 但未导入")
                return False, "missing _extract_claims"
    
    # 检查 _compute_semantic_signature 是否存在
    has_sig = "_compute_semantic_signature" in content
    if not has_sig:
        log("  ⚠️  缺少 _compute_semantic_signature 方法")
        return False, "missing _compute_semantic_signature"
    
    log("  ✅ beliefs.py 方法完整")
    return True, "ok"

def check_weak_areas_add():
    """检查 weak_areas.py add() 方法是否完整"""
    log("🔍 检查 weak_areas.py add() 方法...")
    wa = BASE / "cognition" / "weak_areas.py"
    content = wa.read_text()
    
    # 检查 add 方法是否存在且完整
    add_match = re.search(r'def add\(self[^)]*\):(.*?)(?=\n    def |\nclass |\Z)', content, re.DOTALL)
    if not add_match:
        log("  ⚠️  找不到 add() 方法定义")
        return False, "missing add()"
    
    add_body = add_match.group(1)
    if len(add_body.strip()) < 50:
        log(f"  ⚠️  add() 方法体不完整 ({len(add_body)} chars)")
        return False, "add() incomplete"
    
    log("  ✅ weak_areas.py add() 完整")
    return True, "ok"

def main():
    log("=" * 60)
    log("🚀 A股项目自动检测开始")
    log("=" * 60)
    
    issues = []
    
    # 1. 关键文件检查
    files_ok = check_critical_files()
    if not files_ok:
        issues.append("关键文件缺失")
    
    # 2. 语法检查
    syntax_ok = check_syntax()
    if not syntax_ok:
        issues.append("语法错误")
    
    # 3. 导入检查
    imports_ok, import_results = check_imports()
    if not imports_ok:
        issues.append(f"模块导入失败: {[k for k,v in import_results.items() if not v]}")
    
    # 4. beliefs.py _semantic_conflicts_with 方法完整性
    beliefs_ok = True
    beliefs_msg = "ok"
    log("🔍 检查 beliefs.py 方法完整性...")
    beliefs = BASE / "cognition" / "beliefs.py"
    content = beliefs.read_text()

    # 检查关键方法是否存在
    for method in ["_compute_semantic_signature", "_semantic_conflicts_with",
                   "_extract_claims", "update", "get"]:
        if method not in content:
            log(f"  ⚠️  缺少方法: {method}")
            beliefs_ok = False
            beliefs_msg = f"missing {method}"
    if beliefs_ok:
        log("  ✅ beliefs.py 方法完整")
    else:
        issues.append(f"beliefs.py: {beliefs_msg}")
    
    # 5. weak_areas.py add() 完整性
    wa_ok = True
    wa_msg = "ok"
    log("🔍 检查 weak_areas.py add() 方法...")
    wa = BASE / "cognition" / "weak_areas.py"
    content = wa.read_text()
    if "    def add(" not in content or len(content) < 5000:
        log("  ⚠️  weak_areas.py 文件异常")
        wa_ok = False
        wa_msg = "file too short"
    else:
        log("  ✅ weak_areas.py add() 存在")
    if not wa_ok:
        issues.append(f"weak_areas.py: {wa_msg}")
    
    # 6. 测试
    tests_ok, t54, tcog = check_tests()
    
    log("")
    if not issues and tests_ok:
        log("🎉 所有检测通过，无问题！")
        return 0
    
    if issues:
        log(f"⚠️  发现 {len(issues)} 个问题: {issues}")
    
    if not tests_ok:
        log(f"  test_morphology_matrix_54: {t54}")
        log(f"  test_cognition_modules: {tcog}")
    
    # 尝试自动修复已知问题
    log("")
    log("🔧 尝试自动修复...")
    fixed = []
    
    # 修复1: beliefs.py _extract_claims 问题
    if not beliefs_ok and beliefs_msg == "missing _extract_claims":
        log("  → 修复 beliefs.py _extract_claims 引用...")
        beliefs = BASE / "cognition" / "beliefs.py"
        content = beliefs.read_text()
        # 在 _semantic_conflicts_with 方法里，把 _extract_claims 改成用 self._compute_semantic_signature
        new_content = re.sub(
            r'claims[12]\s*=\s*_extract_claims\(',
            'claims12_sig = self._compute_semantic_signature(',
            content
        )
        if new_content != content:
            beliefs.write_text(new_content)
            log("    ✅ 已用 _compute_semantic_signature 替换 _extract_claims")
            fixed.append("beliefs.py _extract_claims")
        else:
            # 另一个方案：把 _semantic_conflicts_with 整个方法改成直接比较签名
            # 找方法体
            pattern = r'(    def _semantic_conflicts_with\(self.*?\n)(        # .*?\n)(.*?)(        return False)'
            def replacer(m):
                return (m.group(1) + 
                        '        sig1 = self._compute_semantic_signature(b1)\n'
                        '        sig2 = self._compute_semantic_signature(b2)\n'
                        '        overlap = len(sig1 & sig2)\n'
                        '        min_len = min(len(sig1), len(sig2))\n'
                        '        if min_len == 0: return False\n'
                        '        ratio = overlap / min_len\n'
                        '        if ratio >= 0.75: return True\n'
                        '        return False\n')
            new_content = re.sub(pattern, replacer, content, flags=re.DOTALL)
            if new_content != content:
                beliefs.write_text(new_content)
                log("    ✅ 已重写 _semantic_conflicts_with 方法")
                fixed.append("beliefs.py _semantic_conflicts_with")
    
    # 修复2: weak_areas.py add() 方法
    if not wa_ok and wa_msg == "add() incomplete":
        log("  → 修复 weak_areas.py add() 方法...")
        wa = BASE / "cognition" / "weak_areas.py"
        content = wa.read_text()
        # 找到 add 方法并替换为完整实现
        pattern = r'(    def add\(self.*?(?=\n    def |\nclass |\Z))'
        def add_impl(m):
            return '''    def add(self, area_type: str, description: str,
                 severity: str = "medium", source: str = "system",
                 related_morphologies: list = None,
                 market_conditions: list = None) -> bool:
        """添加新的薄弱点，自动去重"""
        if related_morphologies is None:
            related_morphologies = []
        if market_conditions is None:
            market_conditions = []

        # 检查是否与已有薄弱点语义重复
        new_sig = self._compute_semantic_signature(description)
        for existing in self.store.get('areas', []):
            exist_sig = self._compute_semantic_signature(existing.get('description', ''))
            overlap = len(new_sig & exist_sig)
            min_len = min(len(new_sig), len(exist_sig))
            if min_len > 0 and overlap / min_len >= 0.75:
                # 找到重复，追加关联信息
                existing.setdefault('related_morphologies', [])
                for m in related_morphologies:
                    if m not in existing['related_morphologies']:
                        existing['related_morphologies'].append(m)
                existing.setdefault('market_conditions', [])
                for c in market_conditions:
                    if c not in existing['market_conditions']:
                        existing['market_conditions'].append(c)
                existing['count'] = existing.get('count', 1) + 1
                self._save()
                return False

        area = WeakArea(
            area_type=area_type,
            description=description,
            severity=severity,
            source=source,
            related_morphologies=related_morphologies,
            market_conditions=market_conditions
        )
        self.store.setdefault('areas', []).append(area.to_dict())
        self._save()
        return True
'''
        new_content = re.sub(pattern, add_impl, content, flags=re.DOTALL)
        if new_content != content:
            wa.write_text(new_content)
            log("    ✅ 已修复 weak_areas.py add() 方法")
            fixed.append("weak_areas.py add()")
    
    if fixed:
        log(f"  已修复: {fixed}")
        log("  重新检查...")
        # 重新检查
        beliefs_ok2, _ = check_beliefs_signature()
        wa_ok2, _ = check_weak_areas_add()
        if beliefs_ok2 and wa_ok2:
            log("  ✅ 修复成功，重新运行测试...")
            tests_ok, t54, tcog = check_tests()
    
    # 生成报告
    log("")
    log("=" * 60)
    summary = {
        "time": datetime.now().isoformat(),
        "files_ok": files_ok,
        "syntax_ok": syntax_ok,
        "imports_ok": imports_ok,
        "beliefs_ok": beliefs_ok,
        "weak_areas_ok": wa_ok,
        "tests_ok": tests_ok,
        "test_54": t54,
        "test_cog": tcog,
        "issues": issues,
        "fixed": fixed if 'fixed' in dir() else [],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # ========== 第二步：集成运行（供 Agent 判断是否执行）==========
    # 如果检测全部通过，Agent 可以选择跑一天 run_real.py
    # 日期映射（确保T0→T1间隔1个交易日）：
    #   04-03(T0) → 04-07(T1)
    #   04-07(T0) → 04-08(T1)
    #   04-17(T0) → 04-20(T1)
    INTEGRATION_DATES = [
        ("2026-04-03", "2026-04-07"),
        ("2026-04-07", "2026-04-08"),
        ("2026-04-17", "2026-04-20"),
    ]
    if tests_ok:
        import random
        t0, t1 = random.choice(INTEGRATION_DATES)
        log(f"\n🚀 运行集成测试: {t0} → {t1}")
        cmd = (f"cd {BASE} && source ~/.hermes/venv/bin/activate && "
               f"python run_real.py {t0} {t1} 2>&1")
        out, err, rc = run(cmd, timeout=300)
        last_lines = '\n'.join(out.strip().split('\n')[-15:]) if out else err[-500:]
        log(f"  run_real.py 输出（最后15行）:\n{last_lines}")
        summary["integration"] = {"t0": t0, "t1": t1, "rc": rc, "ok": rc == 0}
        log(f"  ✅ 集成运行成功" if rc == 0 else f"  ❌ 集成运行失败 (rc={rc})")

    report_file = BASE / ".auto_check_report.json"
    report_file.write_text(json.dumps(summary, ensure_ascii=False))
    log(f"\n报告已保存: {report_file}")
    return 0 if tests_ok else 1

if __name__ == "__main__":
    sys.exit(main())
