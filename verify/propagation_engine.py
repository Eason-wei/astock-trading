"""
propagation_engine.py — 形态×阶段语义一致性自动传播检测引擎
================================================================
职责：
  1. 扫描 morphology_config.json 的完整9×6形态×阶段矩阵
  2. 基于"机制相似性"分组，自动识别"语义断裂"组合
  3. 从1015条真实T+1数据中查找证据，对断裂组合评分
  4. 高置信度(>0.8)自动推荐修复，中(0.5-0.8)输出对比，低(<0.5)仅记录

用法：
  python propagation_engine.py [--auto-apply] [--min-confidence 0.8]
"""

import json
import sys
import os
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any

# ============================================================
# 阶段分组（机制相似性）
# ============================================================
# 原理：某些阶段在"形态→T+1方向"的机制上具有相似性
# 当发现某形态在A阶段需要修改时，应检查同组B阶段是否一致

STAGE_GROUPS: Dict[str, List[str]] = {
    # 高情绪反转类：从底部快速拉升
    # 共同机制：主力洗盘完毕 + 情绪反转双击 + 跟风盘次日砸盘压力小
    'high_rebound': ['冰点期', '修复期'],
    
    # 高潮延续类：强势市场继续
    # 共同机制：板块带动效应明显，正期望
    'high_continuation': ['高潮期'],
    
    # 弱势延续类：弱势继续
    # 共同机制：跟风盘跑路，主力护盘失败率高
    'low_continuation': ['退潮期', '降温期'],
    
    # 升温/方向摸索类：情绪缓慢恢复
    # 共同机制：方向不明，个股分化大
    'neutral_staging': ['升温期'],
}

# ============================================================
# 形态基础语义（无阶段覆盖时的fallback方向）
# ============================================================
MORPH_BASE_SEMANTICS: Dict[str, str] = {
    'A类一字板': 'positive',
    'B类正常涨停': 'positive',
    'C1冲高回落': 'negative',
    'D1低开低走': 'negative',
    'D2尾盘急拉': 'negative',
    'E1普通波动': 'neutral',
    'E2宽幅震荡': 'neutral',
    'F1温和放量': 'positive',
    'H横向整理': 'neutral',
}

# ============================================================
# 关键传播规则（当X×A改了，强烈建议检查X×B）
# ============================================================
KEY_PROPAGATIONS: Dict[Tuple[str, str], List[str]] = {
    # 形态: [(from_stage, [to_stages])]
    # 当 E1×冰点期 改成 positive → 必须检查 E1×修复期
    ('E1普通波动', '冰点期'): ['修复期', '升温期'],
    ('E1普通波动', '高潮期'): ['冰点期', '修复期', '升温期'],
    ('E1普通波动', '修复期'): ['冰点期', '升温期'],
    # 当 E2×某阶段改了 → 检查同类机制的 E1×同组
    ('E2宽幅震荡', '高潮期'): ['冰点期'],
    ('E2宽幅震荡', '冰点期'): ['高潮期'],
    # D2 × 冰点期 从 neutral 改 → 检查 D2 × 修复期
    ('D2尾盘急拉', '退潮期'): ['降温期'],
    ('D2尾盘急拉', '降温期'): ['退潮期'],
    # C1 × 高潮期 = negative → 但 C1 × 退潮期/降温期 = ?
    ('C1冲高回落', '高潮期'): ['退潮期', '降温期'],
    # B类 × 高潮期 降级为 neutral → B类 × 降温期/退潮期 是否应该降级？
}

# ============================================================
# 置信度评分
# ============================================================
def propagation_confidence(
    morph_key: str,          # 如 'E1普通波动'
    from_stage: str,
    to_stage: str,
    from_direction: str,
    to_direction: str,
    evidence: List[float],    # 真实T+1变化列表
    n_samples: int,
) -> Dict[str, Any]:
    """
    计算从 from_stage 传播到 to_stage 的置信度
    
    评分维度：
      1. 阶段同组 (+0.4) — high_rebound/high_continuation等
      2. 数据验证 (+0.3 per sample, max +0.3) — 样本均值方向
      3. 样本量 (+0.2 if n>=10, +0.1 if n>=5)
      4. 机制合理性 (+0.1) — 形态基础语义是否支持
    """
    score = 0.0
    reasons = []
    
    # 1. 阶段同组
    same_group = False
    for group_name, stages in STAGE_GROUPS.items():
        if from_stage in stages and to_stage in stages:
            same_group = True
            reasons.append(f"同组'{group_name}': {from_stage}∈{stages} ∧ {to_stage}∈{stages}")
            break
    if same_group:
        score += 0.4
    else:
        # 检查是否在KEY_PROPAGATIONS里（跨组但已知相关）
        key = (morph_key, from_stage)
        if key in KEY_PROPAGATIONS and to_stage in KEY_PROPAGATIONS[key]:
            score += 0.25
            reasons.append(f"KEY_PROPAGATIONS: {from_stage}→{to_stage}已知相关")
    
    # 2. 数据验证
    if evidence and n_samples >= 3:
        avg_change = sum(evidence) / len(evidence)
        win_rate = sum(1 for c in evidence if c > 0.5) / len(evidence)
        
        # 判断证据方向
        if from_direction in ('positive', 'neutral') and avg_change > 1.0:
            score += 0.3
            reasons.append(f"数据支持: avg={avg_change:+.1f}%, win={win_rate:.0%}")
        elif from_direction == 'negative' and avg_change < -1.0:
            score += 0.3
            reasons.append(f"数据支持: avg={avg_change:+.1f}%, win={win_rate:.0%}")
        elif abs(avg_change) < 1.0 and from_direction == 'neutral':
            score += 0.15
            reasons.append(f"数据支持neutral: avg={avg_change:+.1f}%")
        else:
            score += 0.05
            reasons.append(f"数据方向不一致: avg={avg_change:+.1f}% vs direction={from_direction}")
    else:
        reasons.append(f"数据不足: n={n_samples}")
    
    # 3. 样本量
    if n_samples >= 10:
        score += 0.2
    elif n_samples >= 5:
        score += 0.1
    elif n_samples >= 3:
        score += 0.05
    
    # 4. 形态基础语义合理性
    base = MORPH_BASE_SEMANTICS.get(morph_key, 'neutral')
    # positive/negative 形态在不同阶段的方向变化有规律
    # 这里是正向传播（from→to），同向加分
    if from_direction == to_direction:
        score += 0.1
        reasons.append(f"方向一致: {from_direction}=={to_direction}")
    
    # 分类
    if score >= 0.8:
        action = 'AUTO_APPLY'
    elif score >= 0.5:
        action = 'CONFIRM'
    else:
        action = 'RECORD_ONLY'
    
    return {
        'score': round(score, 2),
        'action': action,
        'reasons': reasons,
        'n_samples': n_samples,
        'avg_change': round(sum(evidence)/len(evidence), 2) if evidence else None,
    }


# ============================================================
# 主引擎
# ============================================================
class PropagationEngine:
    def __init__(self, config_path: str, t1_records_path: str):
        self.config_path = config_path
        self.t1_records_path = t1_records_path
        self.config = self._load_config()
        self.t1_data = self._load_t1_data()
        
        # 构建当前矩阵
        self.current_matrix = self._build_current_matrix()
        # 构建真实数据矩阵
        self.actual_matrix = self._build_actual_matrix()
    
    def _load_config(self) -> Dict:
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _load_t1_data(self) -> List[Dict]:
        if os.path.exists(self.t1_records_path):
            with open(self.t1_records_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    
    def _build_current_matrix(self) -> Dict[str, Dict[str, Dict]]:
        """构建当前配置的完整矩阵（所有6个标准阶段）"""
        matrix = {}
        morphologies = self.config.get('morphologies', {})
        overrides = self.config.get('stage_overrides', {})
        standard_stages = ['冰点期', '退潮期', '升温期', '高潮期', '降温期', '修复期']
        
        for morph_key, morph_cfg in morphologies.items():
            matrix[morph_key] = {}
            for stage in standard_stages:
                if stage in overrides and morph_key in overrides[stage]:
                    override = overrides[stage][morph_key]
                    matrix[morph_key][stage] = {
                        'direction': override.get('t1_direction'),
                        'confidence': override.get('t1_confidence'),
                        'expected': override.get('t1_expected_change'),
                        'rule': override.get('rule_applied'),
                        'source': 'override',
                    }
                else:
                    # fallback到基础配置
                    matrix[morph_key][stage] = {
                        'direction': morph_cfg.get('t1_bias'),
                        'confidence': morph_cfg.get('t1_confidence'),
                        'expected': None,
                        'rule': None,
                        'source': 'base',
                    }
        return matrix
    
    def _build_actual_matrix(self) -> Dict[str, Dict[str, Dict]]:
        """
        从1015条真实T+1数据构建"真实表现"矩阵
        由于没有分钟级形态数据，我们用 tag(ban1/ban2/20cm/zhaban/bads)
        作为形态的代理变量，结合阶段统计
        """
        # 按 stage × tag 聚合
        stage_tag_data = defaultdict(list)
        for r in self.t1_data:
            stage_tag_data[(r['stage'], r['tag'])].append(r['change'])
        
        # 对每个阶段，统计各tag类型的平均表现
        actual = defaultdict(dict)
        all_stages = set(r['stage'] for r in self.t1_data)
        all_tags = set(r['tag'] for r in self.t1_data)
        
        for stage in all_stages:
            for tag in all_tags:
                key = (stage, tag)
                if key in stage_tag_data and len(stage_tag_data[key]) >= 3:
                    changes = stage_tag_data[key]
                    avg = sum(changes) / len(changes)
                    wins = sum(1 for c in changes if c > 0.5)
                    actual[stage][tag] = {
                        'avg_change': round(avg, 2),
                        'win_rate': round(wins / len(changes), 2),
                        'n': len(changes),
                    }
        
        return dict(actual)
    
    def detect_breaches(self) -> List[Dict[str, Any]]:
        """
        检测语义断裂：
        当某形态×A阶段有了override（方向X），
        但该形态×B阶段（机制相关）仍走fallback（方向Y≠X），
        且方向Y和X存在语义矛盾
        
        注意：B阶段可能在stage_overrides里不存在（完全没有覆盖），
        这种"完全没有"的断裂更容易被遗漏，危害更大
        """
        breaches = []
        # 所有需要检测的阶段 = stage_overrides中的所有阶段 + 6个标准阶段
        all_stages_in_config = set(self.config.get('stage_overrides', {}).keys())
        standard_stages = {'冰点期', '退潮期', '升温期', '高潮期', '降温期', '修复期'}
        all_stages = all_stages_in_config | standard_stages
        
        for morph_key, stage_data in self.current_matrix.items():
            # 找该形态所有有override的阶段
            overridden_stages = {
                s: d for s, d in stage_data.items() 
                if d['source'] == 'override'
            }
            
            for from_stage, from_info in overridden_stages.items():
                from_dir = from_info['direction']
                
                # 找传播目标（KEY_PROPAGATIONS显式指定 + 同组阶段）
                target_stages = set()
                
                # 显式传播规则
                key = (morph_key, from_stage)
                if key in KEY_PROPAGATIONS:
                    target_stages.update(KEY_PROPAGATIONS[key])
                
                # 同组阶段
                for group_name, group_stages in STAGE_GROUPS.items():
                    if from_stage in group_stages:
                        target_stages.update(group_stages)
                
                for to_stage in target_stages:
                    if to_stage == from_stage:
                        continue
                    if to_stage not in stage_data:
                        continue
                    
                    to_info = stage_data[to_stage]
                    to_dir = to_info['direction']
                    
                    # 断裂判定：
                    # 1. to_stage没有override（走fallback）
                    # 2. from_dir和to_dir方向不一致
                    # 3. 不包括 neutral→neutral（本来就是中性）
                    is_breach = (
                        to_info['source'] == 'base' and 
                        from_dir != to_dir and
                        from_dir in ('positive', 'negative')
                    )
                    
                    if not is_breach:
                        continue
                    
                    # 获取真实数据证据
                    evidence = self._get_evidence(morph_key, to_stage)
                    
                    # 计算置信度
                    rating = propagation_confidence(
                        morph_key=morph_key,
                        from_stage=from_stage,
                        to_stage=to_stage,
                        from_direction=from_dir,
                        to_direction=to_dir,
                        evidence=evidence.get('changes', []),
                        n_samples=evidence.get('n', 0),
                    )
                    
                    if rating['score'] < 0.3:
                        continue  # 置信度太低，跳过
                    
                    breaches.append({
                        'morphology': morph_key,
                        'from_stage': from_stage,
                        'from_info': from_info,
                        'to_stage': to_stage,
                        'to_info': to_info,
                        'rating': rating,
                        'evidence': evidence,
                    })
        
        # 按置信度排序
        breaches.sort(key=lambda x: x['rating']['score'], reverse=True)
        return breaches
    
    def _get_evidence(self, morph_key: str, stage: str) -> Dict:
        """
        获取形态×阶段的真实数据证据

        精确匹配策略：
          1. 先用 tag + amplitude 联合过滤（最精确）
          2. 数据不足时只用 tag 过滤（fallback）
          3. 再不足时用 actual_matrix 聚合估算

        morph_tag_conditions 结构：
          tags: 关联的tag列表
          amplitude_max: E1 = amplitude < 5%
          amplitude_min: E2 = amplitude > 8%
          amplitude_range: F1 = 3% < amplitude < 8%
        """
        morph_tag_conditions = {
            'E1普通波动': {
                'tags': ['ban1'],
                'amplitude_max': 5.0,
                'description': 'amplitude < 5% + 首板tag'
            },
            'E2宽幅震荡': {
                'tags': ['zhaban', 'ban1'],
                'amplitude_min': 8.0,
                'description': 'amplitude > 8% + 炸板/首板'
            },
            'F1温和放量': {
                'tags': ['ban2', 'fanbao'],
                'amplitude_range': (3.0, 8.0),
                'description': '3% < amplitude < 8% + 连板/反包'
            },
            'D2尾盘急拉': {
                'tags': ['zhaban', 'ban1'],
                'amplitude_min': 3.0,
                'description': 'amplitude > 3% + 尾盘偷袭特征'
            },
            'C1冲高回落': {
                'tags': ['ban1', 'ban2'],
                'amplitude_min': 8.0,
                'description': 'amplitude > 8% + 冲高回落'
            },
            'D1低开低走': {
                'tags': ['bads', 'ban1'],
                'amplitude_max': 6.0,
                'description': 'amplitude < 6% + 低开特征'
            },
            'B类正常涨停': {
                'tags': ['ban1', 'ban2'],
                'amplitude_range': (3.0, 10.0),
                'description': '3% < amplitude < 10% + 实体涨停'
            },
            'A类一字板': {
                'tags': ['ban2', 'ban3', 'ban4'],
                'amplitude_max': 2.0,
                'description': 'amplitude < 2% + 一字板'
            },
            'H横向整理': {
                'tags': ['zhaban'],
                'amplitude_max': 3.0,
                'description': 'amplitude < 3% + 横向整理'
            },
        }

        cond = morph_tag_conditions.get(morph_key, {'tags': ['ban1']})
        relevant_tags = cond.get('tags', ['ban1'])
        amp_max = cond.get('amplitude_max')
        amp_min = cond.get('amplitude_min')
        amp_range = cond.get('amplitude_range')

        tag_stats = {}
        total_n = 0

        # 方式1：tag + amplitude 精确过滤（v2数据才有amplitude）
        filtered_changes_precise = []
        for r in self.t1_data:
            if r.get('stage') != stage:
                continue
            if r.get('tag') not in relevant_tags:
                continue
            amp = r.get('amplitude')
            if amp is None:
                continue  # v1数据无amplitude，跳过

            # amplitude过滤
            skip = False
            if amp_max is not None and amp >= amp_max:
                skip = True
            if amp_min is not None and amp <= amp_min:
                skip = True
            if amp_range:
                lo, hi = amp_range
                if not (lo <= amp <= hi):
                    skip = True

            if not skip:
                filtered_changes_precise.append(r['change'])

        # 方式2：只用tag过滤（fallback，当amplitude数据不足时）
        raw_changes = [
            r['change'] for r in self.t1_data
            if r.get('stage') == stage and r.get('tag') in relevant_tags
        ]

        # 优先用精确过滤，数据不足时降级
        if len(filtered_changes_precise) >= 3:
            return {
                'changes': filtered_changes_precise[:50],
                'n': len(filtered_changes_precise),
                'tag_stats': {},
                'avg_change': round(sum(filtered_changes_precise) / len(filtered_changes_precise), 2),
                'source': 'precise_filtered',
                'filter': cond.get('description', ''),
            }
        elif raw_changes:
            # actual_matrix统计（用于tag_stats）
            if stage in self.actual_matrix:
                for tag in relevant_tags:
                    if tag in self.actual_matrix[stage]:
                        tag_stats[tag] = self.actual_matrix[stage][tag]
            return {
                'changes': raw_changes[:50],
                'n': len(raw_changes),
                'tag_stats': tag_stats,
                'avg_change': round(sum(raw_changes) / len(raw_changes), 2),
                'source': 'tag_fallback',
            }
        elif tag_stats:
            # 用聚合估算
            all_changes_est = [s['avg_change'] for s in tag_stats.values()]
            total_n = sum(s['n'] for s in tag_stats.values())
            return {
                'changes': [],
                'n': total_n,
                'tag_stats': tag_stats,
                'avg_change': round(sum(all_changes_est) / len(all_changes_est), 2),
                'source': 'aggregated_estimate',
            }
        else:
            return {
                'changes': [],
                'n': 0,
                'tag_stats': {},
                'avg_change': None,
                'source': 'no_data',
            }
    
    def generate_report(self, breaches: List[Dict]) -> str:
        lines = [
            "=" * 70,
            "形态×阶段 语义一致性检测报告",
            "=" * 70,
            "",
            f"当前配置: {self.config_path}",
            f"真实T+1数据: {len(self.t1_data)}条",
            f"检测到断裂: {len(breaches)}处",
            "",
        ]
        
        for b in breaches:
            morph = b['morphology']
            from_s = b['from_stage']
            to_s = b['to_stage']
            rating = b['rating']
            evidence = b['evidence']
            
            badge = {
                'AUTO_APPLY': '🟢[自动应用]',
                'CONFIRM': '🟡[需确认]',
                'RECORD_ONLY': '⚪[仅记录]',
            }.get(rating['action'], '❓')
            
            lines.append(f"{badge} {morph}")
            lines.append(f"  断裂: {from_s}(override:{b['from_info']['direction']}) → {to_s}(fallback:{b['to_info']['direction']})")
            lines.append(f"  置信度: {rating['score']} | 行动: {rating['action']}")
            lines.append(f"  理由: {'; '.join(rating['reasons'])}")
            
            if evidence.get('n'):
                lines.append(f"  数据: n={evidence['n']}, avg={evidence.get('avg_change')}, tags={list(evidence.get('tag_stats',{}).keys())}")
            
            lines.append("")
        
        return '\n'.join(lines)
    
    def apply_auto_fixes(self, breaches: List[Dict]) -> List[str]:
        """对高置信度断裂执行自动修复"""
        applied = []
        for b in breaches:
            if b['rating']['action'] != 'AUTO_APPLY':
                continue
            
            morph = b['morphology']
            to_stage = b['to_stage']
            from_info = b['from_info']
            
            # 在stage_overrides中添加缺失的覆盖
            if to_stage not in self.config['stage_overrides']:
                self.config['stage_overrides'][to_stage] = {}
            
            # 复制from_stage的配置到to_stage（调整置信度）
            new_override = {
                't1_direction': from_info['direction'],
                't1_confidence': round(from_info['confidence'] * 0.9, 2),  # 传播后置信度略降
                't1_expected_change': from_info.get('expected') or self._estimate_expected(morph, from_info['direction']),
                'rule_applied': f"[传播引擎] 继承{morph}×{b['from_stage']}的配置（{b['rating']['reasons'][0]}）",
            }
            
            self.config['stage_overrides'][to_stage][morph] = new_override
            applied.append(f"{morph}×{to_stage} → {from_info['direction']} (conf={new_override['t1_confidence']})")
        
        if applied:
            # 写回配置
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        
        return applied
    
    def _estimate_expected(self, morph: str, direction: str) -> str:
        est = {
            ('E1普通波动', 'positive'): '+2%~+8%',
            ('E1普通波动', 'negative'): '-3%~-1%',
            ('E2宽幅震荡', 'positive'): '+2%~+10%',
            ('E2宽幅震荡', 'neutral'): '-2%~+3%',
            ('F1温和放量', 'positive'): '+2%~+5%',
        }
        return est.get((morph, direction), '+1%~+5%' if direction == 'positive' else '-3%~+1%')


# ============================================================
# CLI
# ============================================================
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='形态×阶段传播检测引擎')
    parser.add_argument('--config', default='/Users/eason/.hermes/trading_study/decision/config/morphology_config.json')
    parser.add_argument('--data', default='/tmp/all_t1_records_v2.json')
    parser.add_argument('--auto-apply', action='store_true', help='自动应用高置信度修复')
    parser.add_argument('--min-confidence', type=float, default=0.5, help='最小置信度阈值')
    args = parser.parse_args()
    
    engine = PropagationEngine(args.config, args.data)
    
    print(f"加载配置: {args.config}")
    print(f"加载T+1数据: {len(engine.t1_data)}条")
    print()
    
    breaches = engine.detect_breaches()
    breaches = [b for b in breaches if b['rating']['score'] >= args.min_confidence]
    
    report = engine.generate_report(breaches)
    print(report)
    
    if args.auto_apply:
        applied = engine.apply_auto_fixes(breaches)
        if applied:
            print(f"\n🟢 自动修复了 {len(applied)} 处:")
            for a in applied:
                print(f"  ✓ {a}")
        else:
            print("\n无高置信度修复可自动应用")
    else:
        auto = [b for b in breaches if b['rating']['action'] == 'AUTO_APPLY']
        confirm = [b for b in breaches if b['rating']['action'] == 'CONFIRM']
        print(f"\n汇总: 🟢{len(auto)}处可自动应用 | 🟡{len(confirm)}处需确认")
        if auto:
            print("  使用 --auto-apply 自动应用")
