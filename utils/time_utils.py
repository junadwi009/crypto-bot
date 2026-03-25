"""
utils/time_utils.py
Helper timezone — semua waktu bot dalam WIB (Asia/Jakarta).
"""

from __future__ import annotations
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")
UTC = ZoneInfo("UTC")


def now_wib() -> datetime:
    """Waktu sekarang dalam WIB."""
    return datetime.now(WIB)


def now_utc() -> datetime:
    """Waktu sekarang dalam UTC."""
    return datetime.now(UTC)


def today_wib() -> date:
    """Tanggal hari ini dalam WIB."""
    return now_wib().date()


def to_wib(dt: datetime) -> datetime:
    """Convert datetime apapun ke WIB."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(WIB)


def format_wib(dt: datetime | None = None, fmt: str = "%d %b %Y %H:%M WIB") -> str:
    """Format datetime ke string WIB yang readable."""
    if dt is None:
        dt = now_wib()
    return to_wib(dt).strftime(fmt)


def start_of_week_wib() -> date:
    """Senin minggu ini dalam WIB."""
    today = today_wib()
    return today - timedelta(days=today.weekday())


def start_of_month_wib() -> date:
    """Tanggal 1 bulan ini dalam WIB."""
    return today_wib().replace(day=1)


def is_market_hours() -> bool:
    """
    Crypto market buka 24/7 — selalu True.
    Disediakan kalau nanti mau tambah filter jam tertentu.
    """
    return True


def seconds_until(hour: int, minute: int = 0,
                  tz: ZoneInfo = WIB) -> int:
    """
    Hitung detik sampai jam tertentu berikutnya.
    Berguna untuk sleep tepat sampai jam 10:00 WIB misalnya.
    """
    now    = datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())
