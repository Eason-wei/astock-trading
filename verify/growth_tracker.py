"""
growth_tracker.py - BeliefStore & 认知系统增长跟踪器
====================================================
职责：
  1. 统计 BeliefStore 当前状态（条数、version 分布）
  2. 跟踪 BeliefStore version 增长（判断认知是否在迭代）
  3. 统计 WeakAreas 状态（活跃数、已缓解数、history 膨胀检测）
  4. 诊断预测质量趋势（从 step7 结果判断）
  5. 生成增长报告，指出"飞轮是否启动"
  6. [P2-1] 质量监控：准确率预警 / 语义冲突预警 / WA膨胀预警

用法：
    from verify.growth_tracker import GrowthTracker
    tracker = GrowthTracker()
    report = tracker.generate_report()
    print(report)
    alerts = tracker.get_quality_alerts()
"""

import json
from pathlib import Path
from datetime import date
from typing import Dict, Any, List, Optional

# ============================================================
# 路径
# ============================================================

TRADING_STUDY = Path(__file__).parent.parent
COGNITIONS_PATH = TRADING_STUDY / "cognitions.json"
WEAK_AREAS_PATH = TRADING_STUDY / "weak_areas.json"
GROWTH_LOG_PATH = TRADING_STUDY / ".growth_log.json"


# ============================================================
# GrowthTracker
# ============================================================

class GrowthTracker:
    """
    认知系统增长跟踪器。

    用于判断：
      - BeliefStore 是否在增长（version 是否持续增加）
      - 预测质量是否在提升（step7 accuracy trend）
      - WeakAreas 是否在收敛（活跃数下降 or history 不膨胀）
    """

    # [P2-1] 质量预警阈值
    _ACCURACY_MIN_THRESHOLD = 0.40
    _ACCURACY_DECLINE_THRESHOLD = 0.15

    def __init__(self):
        self.today = date.today().isoformat()
        self.cognitions = self._load_cognitions()
        self.weak_areas = self._load_weak_areas()
        self.growth_log = self._load_growth_log()

    # ========================================================
    # 数据加载
    # ========================================================

    def _load_cognitions(self) -> Dict[str, Any]:
        """加载 BeliefStore（Dict格式）"""
        if not COGNITIONS_PATH.exists():
            return {}
        with open(COGNITIONS_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            data = json.loads(content) if content else {}
            return data if isinstance(data, dict) else {}

    def _load_weak_areas(self) -> Dict[str, Any]:
        """加载 WeakAreas（{areas: list, last_updated, counts} 格式）"""
        if not WEAK_AREAS_PATH.exists():
            return {}
        with open(WEAK_AREAS_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            data = json.loads(content) if content else {}
            return data if isinstance(data, dict) else {}

    def _load_growth_log(self) -> List[Dict[str, Any]]:
        """加载增长日志（每次报告时追加）"""
        if not GROWTH_LOG_PATH.exists():
            return []
        with open(GROWTH_LOG_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
            data = json.loads(content) if content else []
            return data if isinstance(data, list) else []

    def _save_growth_log(self):
        """保存增长日志"""
        with open(GROWTH_LOG_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.growth_log, f, ensure_ascii=False, indent=2)

    # ========================================================
    # 核心统计
    # ========================================================

    def belief_store_stats(self) -> Dict[str, Any]:
        """BeliefStore 统计"""
        bs = self.cognitions
        if not bs:
            return {'total': 0, 'total_versions': 0, 'avg_version': 0.0,
                    'conflict_count': 0, 'high_version_items': []}

        total_versions = sum(v.get('version', 1) for v in bs.values())
        conflict_count = sum(1 for v in bs.values() if v.get('conflict'))
        high_version = [(k, v.get('version', 1)) for k, v in bs.items()
                       if v.get('version', 1) >= 10]
        high_version.sort(key=lambda x: -x[1])

        return {
            'total': len(bs),
            'total_versions': total_versions,
            'avg_version': round(total_versions / len(bs), 2),
            'conflict_count': conflict_count,
            'high_version_items': high_version[:5],
        }

    def weak_areas_stats(self) -> Dict[str, Any]:
        """WeakAreas 统计"""
        wa = self.weak_areas
        if not wa or 'areas' not in wa:
            return {'total': 0, 'active': 0, 'mitigated': 0,
                    'total_history': 0, 'avg_history': 0.0,
                    'bloated_areas': []}

        areas = wa.get('areas', [])
        total_history = sum(len(a.get('history', [])) for a in areas)
        avg_history = total_history / len(areas) if areas else 0.0
        bloated = [(a['id'], a.get('root_cause', '?'), len(a.get('history', [])))
                   for a in areas if len(a.get('history', [])) > 10]
        bloated.sort(key=lambda x: -x[2])

        counts = wa.get('counts', {})
        return {
            'total': len(areas),
            'active': counts.get('active', 0),
            'mitigated': counts.get('mitigated', 0),
            'monitoring': counts.get('monitoring', 0),
            'total_history': total_history,
            'avg_history': round(avg_history, 1),
            'bloated_areas': bloated,
        }

    def version_growth_rate(self) -> float:
        """每条信念平均 version 数"""
        bs = self.cognitions
        if not bs:
            return 0.0
        avg = sum(v.get('version', 1) for v in bs.values()) / len(bs)
        return avg

    def is_flywheel_engaged(self) -> Dict[str, Any]:
        """
        判断认知飞轮是否启动。

        飞轮启动条件（满足2条即认为启动）：
          1. avg_version >= 3（认知在持续迭代）
          2. 有 >= 3 条信念 version >= 10（核心认知被打磨多次）
          3. WeakAreas 有缓解的条目（mitigated > 0）
          4. weak_areas avg_history <= 5（教训不膨胀，在收敛）
        """
        bs_stats = self.belief_store_stats()
        wa_stats = self.weak_areas_stats()

        avg_ver = self.version_growth_rate()
        high_ver_count = len(bs_stats['high_version_items'])
        mitigated = wa_stats['mitigated']
        avg_history = wa_stats['avg_history']

        signals = {
            'avg_version >= 3': avg_ver >= 3,
            '>=3条信念 version>=10': high_ver_count >= 3,
            '有缓解的WeakAreas': mitigated > 0,
            'WeakAreas avg_history<=5': avg_history <= 5,
        }

        engaged_count = sum(signals.values())

        return {
            'engaged': engaged_count >= 2,
            'engaged_count': engaged_count,
            'total_signals': len(signals),
            'signals': signals,
            'avg_version': avg_ver,
            'high_version_count': high_ver_count,
        }

    # ========================================================
    # 日志追加
    # ========================================================

    def log_snapshot(self, step7_accuracy: Optional[float] = None) -> Dict[str, Any]:
        """
        追加当日快照到增长日志。
        每次复盘结束后 call 一次。
        """
        bs_stats = self.belief_store_stats()
        wa_stats = self.weak_areas_stats()
        flywheel = self.is_flywheel_engaged()

        snapshot = {
            'date': self.today,
            'belief_count': bs_stats['total'],
            'total_versions': bs_stats['total_versions'],
            'avg_version': bs_stats['avg_version'],
            'conflict_count': bs_stats['conflict_count'],
            'weak_areas_active': wa_stats['active'],
            'weak_areas_mitigated': wa_stats['mitigated'],
            'weak_areas_avg_history': wa_stats['avg_history'],
            'flywheel_engaged': flywheel['engaged'],
            'flywheel_signals': flywheel['engaged_count'],
        }
        if step7_accuracy is not None:
            snapshot['step7_accuracy'] = step7_accuracy

        self.growth_log.append(snapshot)
        self._save_growth_log()
        return snapshot

    def get_flywheel_status(self) -> Dict[str, Any]:
        """返回飞轮状态（供 step8 写入报告）"""
        flywheel = self.is_flywheel_engaged()
        return {
            'engaged': flywheel['engaged'],
            'engaged_count': flywheel['engaged_count'],
            'signals': flywheel['signals'],
            'belief_count': self.belief_store_stats()['total'],
            'avg_version': self.version_growth_rate(),
        }

    # ========================================================
    # [P2-1] 质量监控
    # ========================================================

    def _accuracy_alerts(self) -> List[Dict[str, Any]]:
        """检查 accuracy_tracker 中是否有低准确率或下降趋势"""
        from decision.accuracy_tracker import AccuracyTracker
        tracker = AccuracyTracker()
        alerts = []
        all_precisions = tracker.get_all_precisions(min_samples=3)
        all_stats = tracker.get_stats_summary()

        for (morph, stage), blended in all_precisions.items():
            stats = all_stats.get(stage, {}).get(morph, {})
            total = stats.get('total', 0)
            correct = stats.get('correct', 0)
            overall = (correct + 6) / (total + 10) if total >= 3 else None

            issue = None
            severity = 'info'

            if blended < self._ACCURACY_MIN_THRESHOLD:
                issue = f"准确率{blended:.1%}低于阈值{self._ACCURACY_MIN_THRESHOLD:.0%}"
                severity = 'critical' if blended < 0.30 else 'warning'
            elif overall is not None and blended < overall - self._ACCURACY_DECLINE_THRESHOLD:
                issue = f"近期下降({blended:.1%} vs 全量{overall:.1%})"
                severity = 'warning'

            if issue:
                alerts.append({
                    'morph': morph, 'stage': stage,
                    'overall': round(overall, 3) if overall else None,
                    'blended': round(blended, 3),
                    'samples': total,
                    'issue': issue, 'severity': severity,
                })
        return alerts

    def _semantic_conflict_alerts(self) -> List[Dict[str, Any]]:
        """检查 BeliefStore 中是否有语义冲突"""
        from cognition.beliefs import BeliefStore
        bs = BeliefStore()
        alerts = []
        for key, entry in bs._cache.items():
            conflict = entry.get('conflict') or ''
            if '语义冲突' in conflict:
                alerts.append({'belief_key': key, 'conflict_summary': conflict[:100]})
        return alerts

    def get_quality_alerts(self) -> Dict[str, Any]:
        """获取所有质量预警（准确率/语义冲突/WA膨胀）"""
        accuracy_alerts = self._accuracy_alerts()
        conflict_alerts = self._semantic_conflict_alerts()
        wa_stats = self.weak_areas_stats()

        weak_area_alerts = [
            {'area_id': aid, 'cause': cause[:40], 'history_count': cnt}
            for aid, cause, cnt in wa_stats.get('bloated_areas', [])
        ]

        critical = sum(1 for a in accuracy_alerts if a['severity'] == 'critical')
        warning = sum(1 for a in accuracy_alerts if a['severity'] == 'warning')
        overall = 'critical' if critical > 0 else ('warning' if warning > 0 or conflict_alerts else 'ok')

        return {
            'accuracy_alerts': accuracy_alerts,
            'conflict_alerts': conflict_alerts,
            'weak_area_alerts': weak_area_alerts,
            'overall_severity': overall,
            'summary': {
                'critical': critical, 'warning': warning,
                'conflicts': len(conflict_alerts),
                'bloated_wa': len(weak_area_alerts),
            }
        }

    # ========================================================
    # 报告生成
    # ========================================================

    def generate_report(self) -> str:
        """
        生成增长跟踪报告 [P2-1 增强版]。

        包含：
          - BeliefStore 状态（信念数、version 分布、语义冲突）
          - WeakAreas 状态（活跃/缓解/膨胀检测）
          - 认知飞轮状态（是否启动）
          - 趋势对比（与上次快照）
          - 质量预警（准确率/语义冲突/WA膨胀）[P2-1]
        """
        from decision.accuracy_tracker import AccuracyTracker

        lines = []
        lines.append("=" * 60)
        lines.append(f"认知系统增长报告 - {self.today}")
        lines.append("=" * 60)

        # ---- BeliefStore ----
        bs = self.belief_store_stats()
        lines.append("\n【BeliefStore 状态】")
        lines.append(f"  总信念数: {bs['total']}")
        lines.append(f"  总 version 数: {bs['total_versions']} (avg={bs['avg_version']})")
        lines.append(f"  存在 conflict 的信念: {bs['conflict_count']}")
        if bs['high_version_items']:
            lines.append(f"  高版本信念 (version>=10):")
            for name, ver in bs['high_version_items']:
                lines.append(f"    {name}: v{ver}")

        # ---- WeakAreas ----
        wa = self.weak_areas_stats()
        lines.append(f"\n【WeakAreas 状态】")
        lines.append(f"  总数: {wa['total']} (活跃={wa['active']} / 缓解={wa['mitigated']} / 监控={wa['monitoring']})")
        lines.append(f"  总 history 条目: {wa['total_history']} (avg={wa['avg_history']}/条)")
        if wa['bloated_areas']:
            lines.append(f"  ⚠️ history 膨胀 (>10条): {len(wa['bloated_areas'])} 个")
            for area_id, cause, count in wa['bloated_areas'][:3]:
                lines.append(f"    {area_id} ({cause[:30]}...): {count}条")
        else:
            lines.append(f"  ✅ history 无膨胀")

        # ---- 飞轮状态 ----
        flywheel = self.is_flywheel_engaged()
        lines.append(f"\n【认知飞轮状态】({'🟢 启动中' if flywheel['engaged'] else '🔴 未启动'})")
        lines.append(f"  满足 {flywheel['engaged_count']}/{flywheel['total_signals']} 条启动条件:")
        for sig, status in flywheel['signals'].items():
            icon = '✅' if status else '❌'
            lines.append(f"    {icon} {sig}")
        lines.append(f"  avg_version={flywheel['avg_version']:.1f}, "
                     f"高版本信念={flywheel['high_version_count']}")

        # ---- 趋势（对比上次快照）----
        if len(self.growth_log) >= 2:
            prev = self.growth_log[-2]
            curr = self.growth_log[-1] if self.growth_log else {}
            lines.append(f"\n【趋势对比（{prev.get('date','?')} → {self.today}）】")
            delta_beliefs = curr.get('belief_count', 0) - prev.get('belief_count', 0)
            delta_versions = curr.get('total_versions', 0) - prev.get('total_versions', 0)
            delta_wa = curr.get('weak_areas_active', 0) - prev.get('weak_areas_active', 0)
            lines.append(f"  信念数: {prev.get('belief_count',0)} → {curr.get('belief_count',0)} "
                         f"({'+' if delta_beliefs > 0 else ''}{delta_beliefs})")
            lines.append(f"  总version: {prev.get('total_versions',0)} → {curr.get('total_versions',0)} "
                         f"({'+' if delta_versions > 0 else ''}{delta_versions})")
            lines.append(f"  活跃WA: {prev.get('weak_areas_active',0)} → {curr.get('weak_areas_active',0)} "
                         f"({'+' if delta_wa > 0 else ''}{delta_wa})")

        elif len(self.growth_log) == 1:
            lines.append(f"\n【趋势】首次记录（上次: {self.growth_log[0].get('date','?')}）")
        else:
            lines.append(f"\n【趋势】尚无历史快照")

        # ---- [P2-1] 质量预警 ----
        tracker = AccuracyTracker()
        quality = self.get_quality_alerts()

        lines.append(f"\n【质量预警】({quality['overall_severity']})")
        summary = quality['summary']
        lines.append(f"  准确率预警: {summary['critical']} critical, {summary['warning']} warning")
        lines.append(f"  语义冲突: {summary['conflicts']} 个")
        lines.append(f"  WA膨胀: {summary['bloated_wa']} 个")

        # 准确率预警详情
        if quality['accuracy_alerts']:
            lines.append(f"\n  📉 准确率预警详情:")
            for alert in quality['accuracy_alerts']:
                icon = '🔴' if alert['severity'] == 'critical' else '🟡'
                lines.append(f"    {icon} {alert['morph']}×{alert['stage']}: "
                             f"{alert['issue']} (样本{alert['samples']})")

        # 语义冲突预警详情
        if quality['conflict_alerts']:
            lines.append(f"\n  ⚠️ 语义冲突详情:")
            for alert in quality['conflict_alerts'][:3]:
                lines.append(f"    • {alert['belief_key']}: {alert['conflict_summary']}")

        # WA膨胀预警详情
        if quality['weak_area_alerts']:
            lines.append(f"\n  ⚠️ WA膨胀详情:")
            for alert in quality['weak_area_alerts'][:3]:
                lines.append(f"    • {alert['area_id']} ({alert['cause']}): "
                             f"{alert['history_count']}条history")

        lines.append("=" * 60)
        return '\n'.join(lines)
