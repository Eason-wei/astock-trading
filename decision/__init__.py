"""
decision/ - A股交易决策系统
=====================================
职责：
  - 形态×阶段T+1矩阵（5条核心规则代码化）
  - 仓位配置规则
  - 三问定乾坤
  - 风险控制器

核心模块：
  - morphology_matrix.py      : 形态×阶段 T+1 预测矩阵
  - position_rules.py        : 各阶段仓位配置
  - three_questions.py       : 三问定乾坤过滤
  - risk_controller.py       : 风险收益比 + 止损
  - accuracy_tracker.py      : 实时准确率追踪（贝叶斯平滑）
  - pain_effect_analyzer.py  : 亏钱效应专项分析（四维度+一票否决）

使用方式：
  from decision import MorphologyMatrix, PositionRules, ThreeQuestions, RiskController, AccuracyTracker
  from decision.pain_effect_analyzer import run as pain_run, print_report as pain_print
"""

from .morphology_matrix import MorphologyMatrix, Morphology
from .position_rules import PositionRules
from .three_questions import ThreeQuestions
from .risk_controller import RiskController
from .accuracy_tracker import AccuracyTracker

__all__ = [
    'MorphologyMatrix',
    'Morphology',
    'PositionRules',
    'ThreeQuestions',
    'RiskController',
    'AccuracyTracker',
]
