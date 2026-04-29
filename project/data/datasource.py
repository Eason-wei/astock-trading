"""
数据源中间件 - 统一访问接口
从三个MongoDB集合 + MySQL分钟数据 中拉取数据
"""
import pymongo
import pymysql
from typing import Dict, List, Optional, Any
try:
    from .config import MONGO_CONFIG, MYSQL_CONFIG
except ImportError:
    from project.config import MONGO_CONFIG, MYSQL_CONFIG


class DataSource:
    """统一数据源接口"""

    def __init__(self):
        self.mc = pymongo.MongoClient(
            host=MONGO_CONFIG['host'],
            port=MONGO_CONFIG['port'],
            username=MONGO_CONFIG.get('username'),
            password=MONGO_CONFIG.get('password'),
            authSource=MONGO_CONFIG.get('auth_source', 'admin'),
        )
        self.conn = pymysql.connect(
            host=MYSQL_CONFIG['host'],
            user=MYSQL_CONFIG['user'],
            password=MYSQL_CONFIG['password'],
            port=MYSQL_CONFIG['port'],
            database=MYSQL_CONFIG['database'],
            charset=MYSQL_CONFIG['charset']
        )
        # 直接引用集合
        self.fupan = self.mc[MONGO_CONFIG['databases']['fupan']][MONGO_CONFIG['collections']['fupan_data']]
        self.lianban = self.mc[MONGO_CONFIG['databases']['lianban']][MONGO_CONFIG['collections']['lianban_data']]
        self.jiuyang = self.mc[MONGO_CONFIG['databases']['jiuyang']][MONGO_CONFIG['collections']['analysis']]
        self.pain = self.mc[MONGO_CONFIG['databases']['pain']][MONGO_CONFIG['collections']['pain_scores']]

    def close(self):
        self.conn.close()
        self.mc.close()

    # ====== fupan_data（情绪数据）======
    def get_fupan(self, date: str) -> Optional[Dict]:
        """获取某日情绪数据"""
        return self.fupan.find_one({'date': date})

    def get_fupan_range(self, start_date: str, end_date: str) -> List[Dict]:
        """获取日期范围内的情绪数据"""
        return list(self.fupan.find({
            'date': {'$gte': start_date, '$lte': end_date}
        }).sort('date', 1))

    # ====== lianban_data（连板天梯）======
    def get_lianban(self, date: str) -> Optional[Dict]:
        """获取某日连板天梯数据"""
        return self.lianban.find_one({'date': date})

    def get_lianban_range(self, start_date: str, end_date: str) -> List[Dict]:
        """获取日期范围内的连板数据"""
        return list(self.lianban.find({
            'date': {'$gte': start_date, '$lte': end_date}
        }).sort('date', 1))

    # ====== jiuyangongshe（题材数据）======
    # P0-①修复: 每日期有15个板块文档，find_one()只返回第一条（"公告"）。
    #           改为 find() 返回全部板块，供 step5 成分股扩展使用。
    def get_jiuyang(self, date: str) -> List[Dict]:
        """
        获取某日全部题材板块数据（返回List，每元素为一个板块文档）。
        每个板块含: name, list[成分股], reason, date。
        """
        return list(self.jiuyang.find(
            {'date': date},
            {'_id': 0}  # 排除MongoDB _id
        ).sort('name', 1))

    def get_jiuyang_by_plate(self, date: str, plate_name: str) -> Optional[Dict]:
        """获取某日某题材的成分股数据"""
        return self.jiuyang.find_one({'date': date, 'name': plate_name}, {'_id': 0})

    # ====== pain_effect_scores（亏钱效应评分历史）======
    def get_pain_score(self, date: str) -> Optional[Dict]:
        """获取某日亏钱效应评分（不含_id）"""
        return self.pain.find_one({'date': date}, {'_id': 0})

    def get_pain_scores(self, start_date: str, end_date: str) -> List[Dict]:
        """获取日期范围内的亏钱效应评分（不含_id）"""
        return list(
            self.pain.find(
                {'date': {'$gte': start_date, '$lte': end_date}},
                {'_id': 0}
            ).sort('date', 1)
        )

    def save_pain_score(self, date: str, score: float,
                        level: str = None,
                        trend: str = None,
                        breakdown: Dict = None,
                        veto_triggered: bool = None,
                        veto_reasons: List = None,
                        signals: List = None,
                        warnings: List = None) -> None:
        """保存某日亏钱效应评分"""
        doc = {'date': date, 'score': score}
        if level is not None: doc['level'] = level
        if trend is not None: doc['trend'] = trend
        if breakdown is not None: doc['breakdown'] = breakdown
        if veto_triggered is not None: doc['veto_triggered'] = veto_triggered
        if veto_reasons is not None: doc['veto_reasons'] = veto_reasons
        if signals is not None: doc['signals'] = signals
        if warnings is not None: doc['warnings'] = warnings
        self.pain.update_one({'date': date}, {'$set': doc}, upsert=True)

    # ====== MySQL分钟数据 ======
    # P2-④修复：cursor 异常路径泄漏 → 用 try/finally 保证 close()
    def get_mysql_stocks(self, date: str) -> List[str]:
        """获取某日在MySQL有分钟数据的股票列表"""
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"SELECT DISTINCT ts_code FROM `{MYSQL_CONFIG['table']}` WHERE fetch_date=%s",
                (date,)
            )
            stocks = [r[0] for r in cursor.fetchall()]
        finally:
            cursor.close()
        return stocks

    def get_mysql_minutes(self, ts_code: str, date: str) -> List[Dict]:
        """获取某只股票某日的241点分钟数据"""
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"SELECT price_time, price, volume, amount, base_price "
                f"FROM `{MYSQL_CONFIG['table']}` WHERE ts_code=%s AND fetch_date=%s ORDER BY price_time",
                (ts_code, date)
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [
            {
                'price_time': r[0],
                'price': float(r[1]) if r[1] is not None else 0.0,
                'volume': r[2],
                'amount': r[3],
                'base_price': float(r[4]) if r[4] is not None else 0.0,
            }
            for r in rows
        ]

    def get_mysql_minutes_fast(self, date: str) -> Dict[str, List[Dict]]:
        """批量获取某日所有股票的分钟数据（按股票分组）"""
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"SELECT ts_code, price_time, price, volume, amount, base_price "
                f"FROM `{MYSQL_CONFIG['table']}` WHERE fetch_date=%s ORDER BY ts_code, price_time",
                (date,)
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()

        result = {}
        for r in rows:
            ts_code = r[0]
            if ts_code not in result:
                result[ts_code] = []
            result[ts_code].append({
                'price_time': r[1],
                'price': float(r[2]) if r[2] is not None else 0.0,
                'volume': r[3],
                'amount': r[4],
                'base_price': float(r[5]) if r[5] is not None else 0.0,
            })
        return result

    # ====== 跨数据源组合查询 ======

    def get_date_snapshot(self, date: str) -> Dict[str, Any]:
        """
        ⚠️ 注意：此方法是 get_date_snapshot_lite() 的别名，保留用于向后兼容。
        文档中的 `get_date_snapshot(date)` 与 `get_date_snapshot_lite(date)` 返回值完全相同。
        推荐使用 get_date_snapshot_lite()（含义更明确）。
        """
        return self.get_date_snapshot_lite(date)

    def get_date_snapshot_lite(self, date: str) -> Dict[str, Any]:
        """
        轻量级快照（Step1-4使用，不查MySQL）
        P2-①修复：避免 Step1-4 做多余的 SELECT DISTINCT 查询。
        """
        return {
            'fupan': self.get_fupan(date),
            'lianban': self.get_lianban(date),
            'jiuyang': self.get_jiuyang(date),
        }

    def get_date_snapshot_full(self, date: str) -> Dict[str, Any]:
        """
        完整快照（Step5使用，含MySQL股票列表）
        P2-①修复：只在Step5才查 MySQL。
        """
        return {
            'fupan': self.get_fupan(date),
            'lianban': self.get_lianban(date),
            'jiuyang': self.get_jiuyang(date),
            'mysql_stocks': self.get_mysql_stocks(date),
        }

    def get_t1_verification(self, date: str) -> Dict[str, Any]:
        """拉取T+1验证所需数据"""
        return {
            'fupan': self.get_fupan(date),
            'lianban': self.get_lianban(date),
            'mysql_stocks': self.get_mysql_stocks(date),
        }
