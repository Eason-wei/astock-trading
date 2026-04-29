#!/usr/bin/env python3
"""
rebuild_t1_v2.py — 每周重建 /tmp/all_t1_records_v2.json

数据源：
  - MongoDB: vip_fupanwang.lianban_data  (连板天梯，含T日tag/价格)
  - MongoDB: vip_fupanwang.fupan_data     (情绪数据，含stage)
  - MySQL:   stock_data.stock_mins_data  (241点分钟数据，用于amplitude)

输出：/tmp/all_t1_records_v2.json
格式：[{
    "T": "2026-04-03", "T1": "2026-04-07", "stage": "冰点期",
    "code": "600488", "name": "津药药业", "tag": "ban6",
    "t_price": 6.96, "t1_price": 7.67, "change": 10.2,
    "direction": "positive", "amplitude": 0.14
}, ...]

用法：
  python scripts/rebuild_t1_v2.py              # 重建全部
  python scripts/rebuild_t1_v2.py --days 30   # 只重建最近30天
  python scripts/rebuild_t1_v2.py --dry-run    # 只看日期范围，不写文件
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import pymongo
import pymysql
from dateutil.parser import parse as parse_date


# ==================== 配置 ====================
MONGO_CONFIG = {
    'host': 'localhost',
    'port': 27017,
    'username': '',
    'password': '',
    'auth_source': 'admin',
}

MYSQL_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '675452716zm',
    'database': 'stock_data',
    'port': 3306,
    'charset': 'utf8mb4',
    'table': 'stock_mins_data',
}

OUTPUT_FILE = '/tmp/all_t1_records_v2.json'


# ==================== 工具函数 ====================

def get_business_dates(start_date: str, end_date: str) -> list:
    """返回start_date和end_date之间所有有lianban数据的日期（从MongoDB查询）"""
    mc = pymongo.MongoClient(
        host=MONGO_CONFIG['host'],
        port=MONGO_CONFIG['port'],
        username=MONGO_CONFIG.get('username') or None,
        password=MONGO_CONFIG.get('password') or None,
        authSource=MONGO_CONFIG.get('auth_source', 'admin'),
    )
    db = mc['vip_fupanwang']
    coll = db['lianban_data']

    dates = sorted(set(
        doc['date'] for doc in coll.find(
            {'date': {'$gte': start_date, '$lte': end_date}},
            {'date': 1}
        )
        if 'date' in doc
    ))
    mc.close()
    return dates


def get_t1_date(t_date: str) -> str:
    """T日 → T+1日（粗略+4个自然日，跳过周末）"""
    dt = parse_date(t_date)
    for _ in range(10):
        dt += timedelta(days=1)
        if dt.weekday() < 5:  # 周一到周五
            return dt.strftime('%Y-%m-%d')
    return (dt + timedelta(days=1)).strftime('%Y-%m-%d')


def get_amplitude_from_mysql(ts_code: str, date: str, conn) -> float:
    """
    从MySQL查询某股票某日的振幅
    amplitude = (high_px - low_px) / base_price * 100
    base_price = 当日第一条记录的 price（开盘参考价）
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT price_time, price, volume, amount, base_price "
            f"FROM `{MYSQL_CONFIG['table']}` "
            f"WHERE ts_code=%s AND fetch_date=%s ORDER BY price_time",
            (ts_code, date)
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()

    if not rows:
        return 0.0

    base_price = None
    high_px = -float('inf')
    low_px = float('inf')

    for r in rows:
        price = float(r[1]) if r[1] is not None else 0.0
        bp = float(r[4]) if r[4] is not None else 0.0
        if base_price is None and bp > 0:
            base_price = bp
        if price > 0:
            high_px = max(high_px, price)
            low_px = min(low_px, price)

    if base_price and base_price > 0 and high_px != float('inf'):
        return round((high_px - low_px) / base_price * 100, 2)
    return 0.0


def get_t_price_from_mysql(ts_code: str, date: str, conn) -> float:
    """从MySQL获取T日开盘参考价（第一条记录的base_price）"""
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT base_price FROM `{MYSQL_CONFIG['table']}` "
            f"WHERE ts_code=%s AND fetch_date=%s ORDER BY price_time LIMIT 1",
            (ts_code, date)
        )
        r = cursor.fetchone()
        return float(r[0]) if r and r[0] is not None else 0.0
    finally:
        cursor.close()


def get_t1_close_from_mysql(ts_code: str, date: str, conn) -> float:
    """从MySQL获取T+1收盘价（最后一条记录的价格）"""
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT price FROM `{MYSQL_CONFIG['table']}` "
            f"WHERE ts_code=%s AND fetch_date=%s ORDER BY price_time DESC LIMIT 1",
            (ts_code, date)
        )
        r = cursor.fetchone()
        return float(r[0]) if r and r[0] is not None else 0.0
    finally:
        cursor.close()


def make_ts_code(code: str) -> str:
    """股票代码 → ts_code格式"""
    code = code.replace('sz', '').replace('sh', '').strip()
    if code.startswith(('0', '3')):
        return code + '.SZ'
    return code + '.SH'


# ==================== 核心重建逻辑 ====================

def rebuild_all(days: int = None, dry_run: bool = False):
    """重建所有或最近N天的T+1 v2数据"""

    # ---- 1. 确定日期范围 ----
    end_date = datetime.now().strftime('%Y-%m-%d')
    if days:
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    else:
        # 默认从2025-01-01开始（覆盖所有历史数据）
        start_date = '2025-01-01'

    print(f"[rebuild_t1_v2] 日期范围: {start_date} → {end_date}")

    # ---- 2. 连接MongoDB ----
    mc = pymongo.MongoClient(
        host=MONGO_CONFIG['host'],
        port=MONGO_CONFIG['port'],
        username=MONGO_CONFIG.get('username') or None,
        password=MONGO_CONFIG.get('password') or None,
        authSource=MONGO_CONFIG.get('auth_source', 'admin'),
    )
    fupan_db = mc['vip_fupanwang']
    fupan_coll = fupan_db['fupan_data']
    lianban_coll = fupan_db['lianban_data']

    # ---- 3. 连接MySQL ----
    conn = pymysql.connect(
        host=MYSQL_CONFIG['host'],
        user=MYSQL_CONFIG['user'],
        password=MYSQL_CONFIG['password'],
        database=MYSQL_CONFIG['database'],
        port=MYSQL_CONFIG['port'],
        charset=MYSQL_CONFIG['charset'],
    )

    # ---- 4. 获取所有有lianban数据的日期 ----
    dates = get_business_dates(start_date, end_date)
    print(f"[rebuild_t1_v2] 找到 {len(dates)} 个有连板数据的交易日")

    if dry_run:
        print(f"[dry-run] 将会处理日期: {dates}")
        mc.close()
        conn.close()
        return

    # ---- 5. 遍历每天，构建T+1记录 ----
    records = []
    total_lianban = 0
    missing_t1_price = 0
    missing_amplitude = 0

    for t_date in dates:
        t1_date = get_t1_date(t_date)

        # 获取T日情绪数据（stage）
        fupan_doc = fupan_coll.find_one({'date': t_date}, {'_id': 0})
        stage = fupan_doc.get('qingxu', 'N/A') if fupan_doc else 'N/A'

        # 获取T日连板天梯
        lianban_doc = lianban_coll.find_one({'date': t_date}, {'_id': 0})
        if not lianban_doc:
            continue

        lianban_list = lianban_doc.get('lianban_list', [])

        for grp in lianban_list:
            tag = grp.get('tag', '')
            if not tag:
                continue

            for stock in grp.get('list', []):
                code = stock.get('stock_code', '')
                code = code.replace('sz', '').replace('sh', '').strip()
                name = stock.get('stock_name', '')
                t_price = stock.get('stock_price', 0.0)
                ts_code = make_ts_code(code)

                # ---- T+1收盘价：优先从lianban_data T+1日查，其次从MySQL ----
                t1_price = None
                lianban_t1 = lianban_coll.find_one({'date': t1_date}, {'_id': 0})
                if lianban_t1:
                    for g1 in lianban_t1.get('lianban_list', []):
                        for s1 in g1.get('list', []):
                            c1 = s1.get('stock_code', '').replace('sz', '').replace('sh', '').strip()
                            if c1 == code:
                                t1_price = s1.get('stock_price')
                                break
                        if t1_price is not None:
                            break

                # 回退：从MySQL查T+1收盘价
                if t1_price is None:
                    t1_price = get_t1_close_from_mysql(ts_code, t1_date, conn)
                    if t1_price == 0.0:
                        missing_t1_price += 1

                # ---- T日价格：优先用lianban_data中的stock_price，其次从MySQL ----
                if t_price == 0.0:
                    t_price = get_t_price_from_mysql(ts_code, t_date, conn)

                # ---- 计算change ----
                if t_price and t_price > 0 and t1_price and t1_price > 0:
                    change = round((t1_price - t_price) / t_price * 100, 2)
                else:
                    change = 0.0
                    missing_t1_price += 1

                # ---- direction ----
                if change > 3:
                    direction = 'positive'
                elif change < -3:
                    direction = 'negative'
                else:
                    direction = 'neutral'

                # ---- amplitude：从MySQL查 ----
                amplitude = get_amplitude_from_mysql(ts_code, t_date, conn)
                if amplitude == 0.0:
                    missing_amplitude += 1

                records.append({
                    'T': t_date,
                    'T1': t1_date,
                    'stage': stage,
                    'code': code,
                    'name': name,
                    'tag': tag,
                    't_price': round(t_price, 2) if t_price else 0.0,
                    't1_price': round(t1_price, 2) if t1_price else 0.0,
                    'change': change,
                    'direction': direction,
                    'amplitude': amplitude,
                })
                total_lianban += 1

    # ---- 6. 写入文件 ----
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"[rebuild_t1_v2] ✅ 完成！共 {len(records)} 条记录")
    print(f"   来源：{total_lianban} 只连板股")
    print(f"   缺失T+1价格：{missing_t1_price} 条")
    print(f"   缺失amplitude：{missing_amplitude} 条")
    print(f"   输出：{OUTPUT_FILE}")

    mc.close()
    conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='每周重建 /tmp/all_t1_records_v2.json')
    parser.add_argument('--days', type=int, default=None,
                        help='只处理最近N天（不指定则处理全部历史）')
    parser.add_argument('--dry-run', action='store_true',
                        help='只显示日期范围，不写文件')
    args = parser.parse_args()

    rebuild_all(days=args.days, dry_run=args.dry_run)
