"""
强结构交易系统 - 配置模块

所有仓位值统一从 decision/position_rules.py 的 PositionRules 运行时获取。
本文件仅存放数据库连接配置和业务常量。
"""
import sys
from pathlib import Path

# 单例，供外部 from config import _position_rules_instance（向后兼容）
from decision.position_rules import PositionRules
_position_rules_instance = PositionRules()

MONGO_CONFIG = {
    'host': 'localhost',
    'port': 27017,
    'username': '',          # 空字符串=无认证；pymongo认证时None会被转成字符串"None"导致AuthFailed
    'password': '',
    'auth_source': 'admin',
    'databases': {
        'fupan': 'vip_fupanwang',      # VIP复盘网
        'lianban': 'vip_fupanwang',    # VIP复盘网-连板天梯
        'jiuyang': 'jiuyangongshe',    # 韭研公社
        'pain': 'vip_fupanwang',       # 亏钱效应评分历史
    },
    'collections': {
        'fupan_data': 'fupan_data',    # 情绪数据
        'lianban_data': 'lianban_data', # 连板天梯
        'analysis': 'analysis',         # 题材分析
        'pain_scores': 'pain_effect_scores',  # 亏钱效应评分历史
    }
}

# MySQL配置
MYSQL_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '675452716zm',
    'port': 3306,
    'database': 'stock_data',
    'charset': 'utf8mb4',
    'table': 'stock_mins_data',        # 分钟数据表
}

# 仓位规则（情绪周期 × 机会质量）
# 注意：此字典已废弃，值统一从 decision/position_rules.py 的 PositionRules 获取
#       保留供外部 from config import POSITION_RULES 兼容导入
# 连板健康度阈值
LIANBAN_HEALTH = {
    'ban1_to_ban2_rate': 0.30,   # 首板→二板晋级率警戒线
    '断层_warning': True,         # 梯队断层预警
    'top_ban_max_height': 7,     # 最高板高度上限（正常市场）
}

# 风险规则
RISK_RULES = {
    'degree_market_danger': 30,   # degree_market < 30 极度谨慎
    'down_up_ratio_danger': 3.0, # 下跌家数/上涨家数 > 3 危险
    'stop_num_danger': 30,        # 跌停数 > 30 极弱
    'up_down_ratio_warning_high': 5.0,   # 上涨/下跌 > 5 极端偏多
    'up_down_ratio_warning_low': 0.33,     # 上涨/下跌 < 0.33 极端偏空（下跌是上涨的3倍以上）
}
