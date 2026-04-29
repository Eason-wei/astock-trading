"""
accuracy_tracker.py - 实时准确率追踪器
======================================
职责：
  1. 记录每个 (形态×市场阶段) 的预测命中情况
  2. 计算贝叶斯平滑后的真实准确率（用于替代 JSON 静态 conf）
  3. 提供 get_real_precision(morphology, stage) → float
  4. [V5新增] 近期窗口机制：近期30条样本 × 60%权重，防止Bayesian平滑掩盖性能下降

用法：
    tracker = AccuracyTracker()
    tracker.record(morphology='E1普通波动', market_stage='冰点期', correct=True)
    precision = tracker.get_real_precision('E1普通波动', '冰点期')
"""

import json, os, threading
from pathlib import Path
from typing import Dict, Optional, Tuple
from collections import defaultdict, deque

# ============================================================
# 贝叶斯平滑参数
# ============================================================
# prior_correct / (prior_correct + prior_wrong) = 基准准确率
# 假设新板块默认 60% 准确率，权重相当于 10 个样本
PRIOR_CORRECT = 6
PRIOR_WRONG = 4
PRIOR_TOTAL = PRIOR_CORRECT + PRIOR_WRONG

# ============================================================
# 近期窗口参数 [V5新增]
# ============================================================
RECENT_WINDOW = 30          # 只保留最近30条记录用于"近期准确率"
RECENT_WEIGHT = 0.6         # 近期窗口在最终结果中的权重
HISTORY_WEIGHT = 0.4        # 全量历史在最终结果中的权重


class AccuracyTracker:
    """
    线程安全的准确率追踪器。
    数据保存在 ~/.hermes/trading_study/decision/accuracy_stats.json

    [V5新增] 双轨准确率：
      - overall_smoothed: 全量历史 Bayesian 平滑
      - recent_smoothed:  近期30条窗口准确率
      - blended:          两者加权混合（近期权重更高）
      get_real_precision() 返回 blended 值
    """

    def __init__(self, stats_path: Optional[str] = None):
        if stats_path is None:
            base = Path.home() / '.hermes' / 'trading_study' / 'decision'
            base.mkdir(parents=True, exist_ok=True)
            stats_path = str(base / 'accuracy_stats.json')
        self.stats_path = stats_path
        # [V5→P1-1] _recent 也持久化到磁盘（进程重启不丢）
        self._recent_path = stats_path.replace('.json', '_recent.json')
        self._lock = threading.Lock()
        self._stats: Dict[str, Dict[str, dict]] = {}  # stage → morph → {correct, total}
        # [V5新增] 近期记录（用于计算近期准确率）
        self._recent: Dict[str, Dict[str, deque]] = {}  # stage → morph → deque of bool
        self._load()

    # ============================================================

    def _load(self):
        """从磁盘加载统计数据和近期记录"""
        if os.path.exists(self.stats_path):
            try:
                with open(self.stats_path, 'r', encoding='utf-8') as f:
                    self._stats = json.load(f)
            except (json.JSONDecodeError, IOError, TypeError):
                self._stats = {}
        else:
            self._stats = {}

        # [P1-1] 加载 _recent 队列
        if os.path.exists(self._recent_path):
            try:
                with open(self._recent_path, 'r', encoding='utf-8') as f:
                    raw = json.load(f)  # {stage: {morph: [True/False, ...]}}
                if not isinstance(raw, dict):
                    raw = {}
                for stage, morphs in raw.items():
                    self._recent[stage] = {}
                    for morph, bools in morphs.items():
                        self._recent[stage][morph] = deque(bools, maxlen=RECENT_WINDOW)
            except (json.JSONDecodeError, IOError, TypeError):
                self._recent = {}
        else:
            self._recent = {}

    def _save(self):
        """持久化到磁盘（含 _recent 队列）"""
        with open(self.stats_path, 'w', encoding='utf-8') as f:
            json.dump(self._stats, f, ensure_ascii=False, indent=2)
        # [P1-1] 持久化 _recent（deque 序列化为 list）
        with open(self._recent_path, 'w', encoding='utf-8') as f:
            serializable = {}
            for stage, morphs in self._recent.items():
                serializable[stage] = {}
                for morph, dq in morphs.items():
                    serializable[stage][morph] = list(dq)
            json.dump(serializable, f, ensure_ascii=False)

    # ============================================================

    def record(self, morphology: str, market_stage: str, correct: bool, profit_ratio: float = 0.0):
        """
        记录一次预测结果。

        Args:
            morphology: 形态标签，如 'E1普通波动', 'A类一字板'
            market_stage: 市场阶段，如 '冰点期', '高潮期'
            correct: 预测是否正确（方向对即正确，不看幅度）
        """
        with self._lock:
            # [P1-1补丁] 防止 _recent 和 _stats 不一致导致 KeyError
            # 场景：_stats 从 accuracy_stats.json 加载了某 stage，但 _recent 的
            # accuracy_stats_recent.json 中该 stage 从未写入过（历史遗留问题）
            if market_stage not in self._recent:
                self._recent[market_stage] = {}
            if morphology not in self._recent[market_stage]:
                self._recent[market_stage][morphology] = deque(maxlen=RECENT_WINDOW)

            if market_stage not in self._stats:
                self._stats[market_stage] = {}
            if morphology not in self._stats[market_stage]:
                self._stats[market_stage][morphology] = {'correct': 0, 'total': 0}

            stats = self._stats[market_stage][morphology]
            stats['total'] += 1
            if correct:
                stats['correct'] += 1
            if profit_ratio > 0:
                stats['profit_sum'] = stats.get('profit_sum', 0.0) + profit_ratio
                stats['profit_count'] = stats.get('profit_count', 0) + 1

            # [V5新增] 维护近期队列
            self._recent[market_stage][morphology].append(correct)

            self._save()

    def record_batch(self, records: list):
        """
        批量记录。records = [{morphology, market_stage, correct}, ...]
        用于从 closure 报告批量导入历史数据。
        """
        with self._lock:
            for r in records:
                morph = r.get('morphology', r.get('morph_tag', '?'))
                stage = r.get('market_stage', r.get('qingxu', '?'))
                if morph == '?' or stage == '?':
                    continue

                # [P1-4] bootstrap 后 _recent 队列也要同步维护，否则双轨机制退化为单轨
                if stage not in self._recent:
                    self._recent[stage] = {}
                if morph not in self._recent[stage]:
                    self._recent[stage][morph] = deque(maxlen=RECENT_WINDOW)
                self._recent[stage][morph].append(r.get('correct', False))

                if stage not in self._stats:
                    self._stats[stage] = {}
                if morph not in self._stats[stage]:
                    self._stats[stage][morph] = {'correct': 0, 'total': 0}
                stats = self._stats[stage][morph]
                stats['total'] += 1
                if r.get('correct'):
                    stats['correct'] += 1
            self._save()

    def get_real_precision(
        self,
        morphology: str,
        market_stage: str,
        min_samples: int = 3,
    ) -> Optional[float]:
        """
        获取某 (形态×阶段) 的真实准确率（双轨混合）。

        [V5变更]
          - overall_smoothed: 全量 Bayesian 平滑
          - recent_smoothed:  近期30条窗口准确率（无平滑）
          - blended: 两者加权混合（近期60% + 历史40%）

        Args:
            morphology: 形态标签
            market_stage: 市场阶段
            min_samples: 至少需要多少样本才返回真实值（否则返回 None）

        Returns:
            双轨混合后的准确率，或 None（样本不足）
        """
        with self._lock:
            blended = self._blended_precision_unlocked(market_stage, morphology, min_samples)
            if blended is None:
                return None
            return round(blended, 3)

    def get_current_blended(self, morphology: str, market_stage: str) -> float:
        """
        [方案C] 获取当前 blended 值，无 min_samples 限制。
        用于峰值监控——只要有任何数据（含1条）就返回真实 blended，
        没有数据才返回默认 0.6。
        """
        with self._lock:
            blended = self._blended_precision_unlocked(market_stage, morphology, min_samples=1)
            if blended is None:
                return 0.6
            return round(blended, 3)

    def get_all_precisions(self, min_samples: int = 1) -> Dict[Tuple[str, str], float]:
        """返回所有有数据的 (morph, stage) → 真实准确率（双轨混合）"""
        with self._lock:
            result = {}
            for stage, morphs in self._stats.items():
                for morph, stats in morphs.items():
                    if stats['total'] >= min_samples:
                        blended = self._blended_precision_unlocked(stage, morph, min_samples=1)
                        result[(morph, stage)] = round(blended, 3)
            return result

    def get_stats_summary(self) -> Dict[str, Dict[str, dict]]:
        """返回原始统计数据（用于调试/查看）"""
        with self._lock:
            return dict(self._stats)

    def get_sample_count(self, morphology: str, market_stage: str) -> Optional[int]:
        """返回指定 (morphology, stage) 的样本数量（用于 cold start 检测）"""
        with self._lock:
            stats = self._stats.get(market_stage, {}).get(morphology)
            return stats['total'] if stats else None

    # ── V5新增：内部辅助方法 ────────────────────────────────

    def _blended_precision_unlocked(self, stage: str, morph: str, min_samples: int = 1) -> Optional[float]:
        """计算双轨混合准确率（已持有锁）"""
        stats = self._stats.get(stage, {}).get(morph)
        if not stats or stats['total'] < min_samples:
            return None  # 无足够数据

        # 全量 Bayesian 平滑
        overall = (stats['correct'] + PRIOR_CORRECT) / (stats['total'] + PRIOR_TOTAL)

        # 近期窗口
        recent_deque = None
        if stage in self._recent and morph in self._recent[stage]:
            recent_deque = self._recent[stage][morph]

        if recent_deque and len(recent_deque) >= 5:
            recent_correct = sum(recent_deque)
            recent_total = len(recent_deque)
            recent = (recent_correct + PRIOR_CORRECT * 0.3) / (recent_total + PRIOR_TOTAL * 0.3)
        else:
            recent = overall

        return overall * HISTORY_WEIGHT + recent * RECENT_WEIGHT

    def bootstrap_from_closures(self, closure_files: list):
        """
        从 closure 报告批量导入历史验证记录，建立初始准确率基线。
        每个 record 需要: {morphology, market_stage, correct}

        策略：T日stage优先从MongoDB查询；假期/无数据时用fallback映射。
        """
        import pymongo
        from datetime import datetime, timedelta

        # MongoDB查询函数（带fallback）
        def get_tday_qingxu(t1_date_str: str) -> str:
            """T+1日期 → T日情绪阶段（从MongoDB查，失败时用hardcoded fallback）"""
            KNOWN_TDAY_FALLBACK = {
                '2026-04-07': '冰点期',   # 2026-04-06是清明假期，MongoDB无记录
            }
            try:
                t_day = (datetime.strptime(t1_date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
                client = pymongo.MongoClient('mongodb://localhost:27017/', serverSelectionTimeoutMS=2000)
                doc = client['vip_fupanwang']['fupan_data'].find_one({'date': t_day})
                client.close()
                if doc and doc.get('qingxu'):
                    return doc['qingxu']
            except Exception:
                pass
            return KNOWN_TDAY_FALLBACK.get(t1_date_str, 'unknown')

        records = []
        for fp in closure_files:
            if isinstance(fp, str):
                with open(fp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = fp

            vers = data.get('stock_verifications', [])
            t1_date = data.get('t1_date', '')
            stage = get_tday_qingxu(t1_date) if t1_date else 'unknown'

            for v in vers:
                if v.get('actual_close_pct') is None:
                    continue
                records.append({
                    'morphology': v.get('morphology', '?'),
                    'market_stage': stage,
                    'correct': v.get('correct', False),
                })

        self.record_batch(records)
        print(f"[AccuracyTracker] 从 {len(closure_files)} 个文件导入 {len(records)} 条有效记录")

# ============================================================
# CLI 调试入口
# ============================================================
if __name__ == '__main__':
    import glob, sys

    tracker = AccuracyTracker()

    if '--bootstrap' in sys.argv:
        base = Path.home() / '.hermes' / 'trading_study' / 'project' / 'reports'
        files = sorted(glob.glob(str(base / 'closure_*.json')))
        tracker.bootstrap_from_closures(files)

    print("\n当前准确率基线:")
    for (morph, stage), prec in tracker.get_all_precisions(min_samples=1).items():
        stats = tracker._stats.get(stage, {}).get(morph, {})
        print(f"  {morph:20s} × {stage:6s}: {prec:.1%}  (样本{stats.get('total',0)})")
