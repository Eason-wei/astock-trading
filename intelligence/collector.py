"""collector.py - MongoDB/MySQL market data collector"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from project.data.datasource import DataSource
except ImportError:
    from project.config import MONGO_CONFIG, MYSQL_CONFIG
    import pymongo, pymysql


class MarketDataCollector:
    """MongoDB + MySQL data collector for daily review"""

    def __init__(self):
        self.ds = DataSource()

    def collect_day(self, date: str) -> dict:
        """Collect all data for a given date"""
        snapshot = self.ds.get_date_snapshot(date)
        return {
            "date": date,
            "fupan": snapshot["fupan"],
            "lianban": snapshot["lianban"],
            "jiuyang": snapshot["jiuyang"],
            "mysql_stocks_count": len(snapshot["mysql_stocks"]),
            "collected_at": datetime.now().isoformat(),
        }

    def collect_range(self, start_date: str, end_date: str) -> list:
        """Collect data for a date range"""
        results = []
        d = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        while d <= end:
            date_str = d.strftime("%Y-%m-%d")
            try:
                r = self.collect_day(date_str)
                results.append(r)
                print(f"  [{date_str}] fupan={r['fupan'] is not None} lianban={r['lianban'] is not None}")
            except Exception as e:
                print(f"  [{date_str}] ERROR: {e}")
            d += timedelta(days=1)
        return results

    def collect_t1_verification(self, date: str) -> dict:
        """Collect T+1 verification data"""
        return self.ds.get_t1_verification(date)

    def get_mysql_minutes(self, ts_code: str, date: str) -> list:
        """Get minute-level data for a specific stock"""
        return self.ds.get_mysql_minutes(ts_code, date)

    def get_batch_minutes(self, date: str) -> dict:
        """Get all stocks minute data for a date"""
        return self.ds.get_mysql_minutes_fast(date)

    def close(self):
        self.ds.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
