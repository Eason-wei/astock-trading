"""Step 8: System Closure (Rewritten)Inputs: Step7 verification results + all step outputsOutputs:  - Calls CognitionUpdater to write to cognitions.json / logic_chains.json / weak_areas.json  - Generates improvement plan  - Writes report to file  - OLD bug: computed cognition_updates but never wrote them anywhereCore fix:  - Uses CognitionUpdater to actually write the cognitive loop  - Reads update results from LessonExtractor"""

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from verify.growth_tracker import GrowthTracker


def run(step7_result: dict, step1, step2, step3, step4, step5, step6, **kwargs) -> dict:
    """
    Step 8 - System Closure (Rewritten)

    Key change from old version:
      - OLD: computed cognition_updates but never persisted them
      - NEW: calls CognitionUpdater to actually write to cognitions.json
    """
    # 注意：CognitionUpdater 的 receive_verification_result()
    # 已由 LessonExtractor 内部调用，无需在此重复调用
    # step8 只负责收集 update_result 计数 + 写日志
    result = {
        'date': step1.get('date'),
        'score': step7_result.get('score_rate', 'N/A'),
        'accuracy': step7_result.get('accuracy', 0.0),
        'cognitive_analysis': [],  # 分析文本：wrong_preds/right_preds + 特殊情绪期 review
        'system_fixes': [],
        'next_improvements': [],
        'summary': '',
    }

    lessons = step7_result.get('lessons', [])
    predictions = step7_result.get('predictions', [])
    stock_verifications = step7_result.get('stock_verifications', [])

    # ===== Collect all CognitionUpdater results =====
    all_update_results = []

    # Process stock verification lessons
    # 注意：stock lessons 已在 step7 被加入 result['lessons']，
    #       此处通过 stock_verifications 直接提取 update_result（不走 lessons 列表避免重复）
    for sv in stock_verifications:
        lesson = sv.get('lesson', {})
        if lesson and not lesson.get('lesson_type') == 'emotion':
            update_result = lesson.get('update_result', {})
            if update_result:
                all_update_results.append(update_result)

    # Process emotion verification lessons — separate path, do NOT add to all_update_results
    # Bug D/F 修复：lesson_type='emotion' 的 lessons 不走 CognitionUpdater 的 stock cognition 流程
    emotion_lessons = []
    for le in lessons:
        if isinstance(le, dict) and le.get('lesson_type') == 'emotion':
            emotion_lessons.append(le)
        # 注意：非 emotion 的 stock lessons 已在上面 stock_verifications 循环处理，此处不再重复添加

    # ===== Analyze wrong predictions =====
    wrong_preds = [p for p in predictions if p.get('verdict') == '\u2718\u9519']
    right_preds = [p for p in predictions if p.get('verdict') == '\u2714\u5bf9']

    # P2-b修复：stock lessons → cognitive_analysis 文本转换
    # 每日应聚焦1-3个核心矛盾，不是堆数量
    stock_lessons = [
        sv.get('lesson', {})
        for sv in stock_verifications
        if sv.get('lesson') and isinstance(sv.get('lesson'), dict)
    ]
    wrong_lessons = [l for l in stock_lessons if not l.get('verify_result', {}).get('correct')]
    if wrong_lessons:
        # 取最典型的3个教训，转为文本摘要
        top_lessons = wrong_lessons[:3]
        lines = []
        for l in top_lessons:
            lesson_text = l.get('lesson', '')[:80]
            root_cause = l.get('root_cause', '')
            if root_cause:
                lines.append(f"• {lesson_text}（根因：{root_cause[:40]}）")
            else:
                lines.append(f"• {lesson_text}")
        result['cognitive_analysis'].append({
            'type': '个股预测偏差',
            'content': '\n'.join(lines) if lines else '无显著偏差',
            'count': len(wrong_lessons),
        })
    elif stock_lessons:
        # 无错误但有教训（如正确预测），只记录数量摘要
        result['cognitive_analysis'].append({
            'type': '个股预测概况',
            'content': f'正确{len([l for l in stock_lessons if l.get("verify_result", {}).get("correct")])}/{len(stock_lessons)}个，偏差教训{len(wrong_lessons)}个',
            'count': len(stock_lessons),
        })

    if wrong_preds:
        analysis = _analyze_wrong_preds(wrong_preds, step3, step4)
        result['cognitive_analysis'].append({
            'type': '预测偏差',
            'content': analysis,
            'count': len(wrong_preds),
        })

    if right_preds:
        analysis = _analyze_right_preds(right_preds)
        result['cognitive_analysis'].append({
            'type': '\u9884\u6d4b\u51c6\u786e',
            'content': analysis,
            'count': len(right_preds),
        })

    # ===== Stage-specific reviews =====
    if step3.get('qingxu') == '\u51b0\u70b9\u671f':
        result['cognitive_analysis'].append(_ice_point_special_review(step7_result, step1))

    if step3.get('qingxu') == '\u9ad8\u6f6e\u671f':
        result['cognitive_analysis'].append(_peak_special_review(step7_result, step4))

    # ===== System fixes =====
    result['system_fixes'] = _get_system_fixes(step7_result, step1, step3, step4)

    # ===== Improvement plan =====
    result['next_improvements'] = _get_next_improvements(step7_result, wrong_preds, lessons)

    # ===== Count total cognition updates =====
    total_updates = len(all_update_results)
    successful_updates = sum(1 for u in all_update_results if u.get('actions'))

    # ===== Summary (must be before _write_report) =====
    result['summary'] = _build_summary(result, step7_result, len(all_update_results), successful_updates)
    result['beliefstore_writes'] = len(all_update_results)
    result['beliefstore_successful'] = successful_updates

    # ===== Write to files (after summary is populated) =====
    _write_report(result, step7_result, step6, all_update_results)
    _write_verification_log(step7_result, step1.get('date'), all_update_results, emotion_lessons)

    # ===== Growth tracking =====
    tracker = GrowthTracker()
    accuracy = step7_result.get('accuracy')
    tracker.log_snapshot(step7_accuracy=accuracy)
    result['growth_report'] = tracker.generate_report()
    result['flywheel_status'] = tracker.get_flywheel_status()

    return result


def _analyze_wrong_preds(wrong_preds, step3, step4) -> str:
    """Analyze prediction errors"""
    lines = []
    for p in wrong_preds:
        item = p.get('item', 'N/A')
        predicted = p.get('predicted', 'N/A')
        actual = p.get('actual', 'N/A')
        lines.append(f"\u2718 {item}: \u9884\u5219={predicted} \u5b9e\u9645={actual}")

    if step3.get('qingxu') == '\u51b0\u70b9\u671f':
        lines.append("\u1f4a1 \u8bc6\u5224\u4fee\u6b63\uFF1A\u51b0\u70b9\u671f\u60c5\u7ee9\u53ef\u5feb\u901f\u8df3\u8f6c\u5230\u9ad8\u6f6e\uFF0C\u4e0d\u9700\u8981\u6e10\u8fdb\u5f0f\u4fee\u590d")

    return "; ".join(lines)


def _analyze_right_preds(right_preds) -> str:
    """Analyze correct predictions"""
    lines = []
    for p in right_preds:
        lines.append(f"\u2714 {p.get('item', 'N/A')}: \u9884\u6d4b\u51c6\u786e")
    return "; ".join(lines)


def _ice_point_special_review(step7, step1) -> dict:
    """Ice point special review"""
    return {
        'type': '\u51b0\u70b9\u671f\u4e13\u9879',
        'content': "\u6838\u5fc3\u6559\u8bad\uFF1A\u51b0\u70b9\u6b21\u65e5\u9ad8\u5f00\u662f\u9677\u9631\uFF0C\u4e0d\u662f\u673a\u4f1a\uFF08\u5386\u53f2\u8bc6\u5224\uff09",
    }


def _peak_special_review(step7, step4) -> dict:
    """Peak market special review"""
    zhaban = step4.get('ladder', {}).get('zhaban', {}).get('cnt', 0)
    prog = step4.get('progression', [{}])
    rate = prog[0].get('rate', 0) if prog else 0
    return {
        'type': '\u9ad8\u6f6e\u671f\u4e13\u9879',
        'content': f"\u9ad8\u6f6e\u671f\u9a8c\u8bc1\uFF1A\u70b8\u677f{zhaban}\u53ea\uFF0C\u664b\u7ea7\u7387={rate}\uFF05\uFF0C\u9ad8\u4f4d\u80a1\u53ca\u65f6\u642c\u9003",
    }


def _get_system_fixes(step7, step1, step3, step4) -> list:
    """Get system fix items"""
    fixes = []
    wrong_count = len([p for p in step7.get('predictions', []) if p.get('verdict') == '\u2718\u9519'])

    if wrong_count > 0:
        fixes.append(f"\u9884\u6d4b\u504f\u5dee>{wrong_count}\u6761\uFF0C\u9700\u68c0\u67e5{step3.get('qingxu')}\u671f\u5224\u65ad\u903b\u8f91")

    progression = step4.get('progression', [])
    if progression:
        first = progression[0]
        if first.get('rate', 0) < 20:
            fixes.append(f"\u9996\u677f\u2192\u4e8c\u677f\u664b\u7ea7\u7387\u4ec5{first.get('rate')}\uFF05<20\uFF05\uFF0C\u63d0\u9ad8\u9996\u677f\u64cd\u4f5c\u95e8\u69db")

    health = step4.get('health_score', 100)
    if health < 50:
        fixes.append(f"\u8fde\u677f\u5065\u5eb7\u5ea6{health}<50\uFF0C\u8003\u8651\u964d\u4f4e\u6574\u4f53\u4ed3\u4f4d\u4e0a\u9650")

    return fixes


def _get_next_improvements(step7, wrong_preds, lessons) -> list:
    """Get next improvement items"""
    improvements = []

    improvements.append("\u5fc5\u987b\u627e\u52301-2\u4e2a\u8bc6\u77e5\u77db\u76fe\u70b9\uFF0C\u4e0d\u662f\u5806\u6570\u91cf")

    if wrong_preds:
        improvements.append(f"\u9488\u5bf9{len(wrong_preds)}\u6761\u9519\u8bef\u9884\u6d4b\uFF0C\u68c0\u67e5\u80f8\u540e\u539f\u56e0")

    improvements.append("\u51b0\u70b9\u671f\u8bc6\u77e5\u9700\u6301\u7eed\u9a8c\u8bc1\uFF1A\u5f3a\u52bf\u9f99\u5934\u7a7f\u8d8a\u51b0\u70b9\u7684\u6761\u4ef6\u662f\u4ec0\u4e48\uFF1F")

    return improvements[:3]


def _write_report(result, step7, step6, all_update_results) -> None:
    """Write closure report to file"""
    date = result.get('date', 'unknown')
    report_dir = Path(__file__).parent.parent / 'reports'  # 统一到 project/reports/
    report_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / f"closure_{date.replace('-', '')}.json"

    report = {
        'date': date,
        't1_date': step7.get('t1_date'),
        'summary': result.get('summary', ''),  # P2-①修复：summary从未写入文件
        'score': result['score'],
        'accuracy': result.get('accuracy', 0.0),
        'cognitive_analysis': result.get('cognitive_analysis', []),
        'system_fixes': result.get('system_fixes', []),
        'next_improvements': result.get('next_improvements', []),
        'beliefstore_writes': len(all_update_results),
        'beliefstore_successful': sum(1 for u in all_update_results if u.get('actions')),
        'stock_predictions': step6.get('stock_predictions', []),  # P2-⑥修复：写入全部候选股预测，不切片
        'stock_verifications': [
            {   # P2-⑧修复：写入完整字段，不再只写4个（name/correct/score/data_quality）
                # 原先遗漏：code, predicted_direction, actual_direction, actual_tag, actual_close_pct, morphology, data_quality_note
                'code': sv.get('code', ''),
                'name': sv.get('name', ''),
                'morphology': sv.get('morphology', ''),
                'predicted_direction': sv.get('predicted_direction', ''),
                'actual_direction': sv.get('actual_direction', ''),
                'actual_tag': sv.get('actual_tag', ''),
                'actual_close_pct': sv.get('actual_close_pct', None),
                'correct': sv.get('correct', False),
                'score': sv.get('score', 0),
                'lesson': sv.get('lesson', {}),
                'data_quality': sv.get('data_quality', 'unknown'),
                'data_quality_note': sv.get('data_quality_note', ''),
            }
            for sv in step7.get('stock_verifications', [])
        ],
    }

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"  [Step8] Report written: {report_path}")


def _write_verification_log(step7, date, all_update_results, emotion_lessons: list) -> None:
    """Write verification log"""
    # P2-③修复：统一到 project/reports/cognition_log.json
    log_path = Path(__file__).parent.parent / 'reports' / 'cognition_log.json'
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if log_path.exists():
        with open(log_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
            # 兼容：旧格式可能是 dict，新格式是 list
            log_data = raw if isinstance(raw, list) else [raw]
    else:
        log_data = []

    log_entry = {
        'date': date,
        't1_date': step7.get('t1_date'),
        'score': step7.get('score_rate', 'N/A'),
        'accuracy': step7.get('accuracy', 0.0),
        'total': step7.get('total', 0),
        'correct': step7.get('score', 0),
        'beliefstore_writes': len(all_update_results),
        'verified_at': __import__('datetime').datetime.now().isoformat(),
    }

    # emotion lessons 单独追踪，写入 verification log
    log_entry['emotion_lessons'] = [
        {'item': le.get('item', ''), 'correct': le.get('correct', False), 'score': le.get('score', 0)}
        for le in emotion_lessons
    ]

    log_data.append(log_entry)
    log_data = log_data[-30:]  # Keep last 30 entries

    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)


def _build_summary(result, step7, total_updates, successful_updates) -> str:
    """Build summary text"""
    score = result.get('score', 'N/A')
    updates = len(result.get('cognitive_analysis', []))
    fixes = len(result.get('system_fixes', []))

    next_items = "\n".join(result.get('next_improvements', ['无'][:3]))

    summary = f"""
\u3010{step7.get('t1_date', 'N/A')} \u590d\u76d8\u95ed\u73af\u603b\u7ed3\u3011

\u9884\u6d4b\u5f97\u5206\uFF1A{step7.get('score_rate', 'N/A')}
\u8bc6\u77e5\u66f4\u65b0\uFF1A{total_updates}\u9879\uFF08\u6210\u529f{successful_updates}\u9879\uFF09
\u7cfb\u7edf\u4fee\u6b63\uFF1A{fixes}\u9879

\u6838\u5fc3\u6536\u83b9\uFF1A
{next_items}

\u8bc6\u77e5\u66f4\u65b0\u8be6\u60c5\uFF1A
"""
    for u in result.get('cognitive_analysis', []):
        summary += f"\n  \u2022 [{u.get('type', '')}] {u.get('content', '')[:80]}"

    if result.get('system_fixes'):
        summary += "\n\n\u7cfb\u7edf\u4fee\u6b63\uFF1A"
        for f_item in result.get('system_fixes', []):
            summary += f"\n  \u2192 {f_item}"

    return summary.strip()
