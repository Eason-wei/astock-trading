"""market_monitor.py - Real-time market monitoring during trading hours"""
from datetime import datetime, time
from typing import Dict, Any, Optional


class MarketMonitor:
    """Intraday market monitor"""

    MORNING_START = time(9, 30)
    MORNING_END = time(11, 30)
    AFTERNOON_START = time(13, 0)
    AFTERNOON_END = time(15, 0)

    def __init__(self):
        self.snapshots = []

    @staticmethod
    def is_trading_hours(dt: datetime = None) -> bool:
        """Check if current time is within trading hours"""
        dt = dt or datetime.now()
        t = dt.time()
        if dt.weekday() >= 5:
            return False
        return (MarketMonitor.MORNING_START <= t <= MarketMonitor.MORNING_END or
                MarketMonitor.AFTERNOON_START <= t <= MarketMonitor.AFTERNOON_END)

    @staticmethod
    def is_market_open(dt: datetime = None) -> bool:
        """Check if market is open"""
        dt = dt or datetime.now()
        return dt.weekday() < 5 and MarketMonitor.is_trading_hours(dt)

    def snapshot(self, data: Dict[str, Any]) -> None:
        """Record a market snapshot"""
        self.snapshots.append({"time": datetime.now(), "data": data})

    def get_latest(self) -> Optional[Dict]:
        """Get the latest snapshot"""
        return self.snapshots[-1] if self.snapshots else None

    def get_snapshots(self, last_n: int = 10) -> list:
        """Get last N snapshots"""
        return self.snapshots[-last_n:]
