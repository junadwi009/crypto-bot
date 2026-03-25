"""
config/settings.py
Load semua environment variable ke satu objek settings.
Gunakan settings.NAMA_VAR di seluruh codebase — tidak pernah os.getenv langsung.
"""

import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Load .env file otomatis — berlaku untuk semua modul yang import settings
# override=False agar env vars dari Render/sistem tidak ditimpa .env lokal
load_dotenv(override=False)


class Settings:
    # ── Bybit ──────────────────────────────────────────────
    BYBIT_API_KEY:    str  = os.getenv("BYBIT_API_KEY", "")
    BYBIT_API_SECRET: str  = os.getenv("BYBIT_API_SECRET", "")
    BYBIT_TESTNET:    bool = os.getenv("BYBIT_TESTNET", "false").lower() == "true"

    # ── Anthropic ──────────────────────────────────────────
    ANTHROPIC_API_KEY:        str   = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_SPENDING_LIMIT: float = float(os.getenv("ANTHROPIC_SPENDING_LIMIT", "30"))

    # ── Telegram ───────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID:   int = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

    # ── Database ───────────────────────────────────────────
    SUPABASE_URL:         str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")
    REDIS_URL:            str = os.getenv("REDIS_URL", "redis://localhost:6379")

    # ── Auth ───────────────────────────────────────────────
    BOT_PIN_HASH:   str = os.getenv("BOT_PIN_HASH", "")
    SESSION_TTL:    int = int(os.getenv("SESSION_TTL", str(4 * 3600)))  # 4 jam
    IDLE_TTL:       int = int(os.getenv("IDLE_TTL",   str(1 * 3600)))  # 1 jam idle
    MAX_PIN_ATTEMPTS: int = 3
    LOCKOUT_TTL:    int = 15 * 60  # 15 menit

    # ── Bot behaviour ──────────────────────────────────────
    PAPER_TRADE:     bool  = os.getenv("PAPER_TRADE", "true").lower() == "true"
    INITIAL_CAPITAL: float = float(os.getenv("INITIAL_CAPITAL", "213"))
    MAX_RISK_PER_TRADE: float = 0.02   # 2% modal per trade
    MAX_DAILY_DRAWDOWN: float = 0.15   # 15% → circuit breaker aktif
    MAX_ORDERS_PER_MIN: int   = 3

    # ── Timezone ───────────────────────────────────────────
    BOT_TIMEZONE: str     = os.getenv("BOT_TIMEZONE", "Asia/Jakarta")
    TZ:           ZoneInfo = ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Jakarta"))
    REMINDER_HOUR: int    = 10   # 10:00 pagi WIB

    # ── Infra billing ──────────────────────────────────────
    RENDER_BILLING_DAY: int = int(os.getenv("RENDER_BILLING_DAY", "1"))
    RENDER_MONTHLY_COST: float = 7.0

    # ── Claude rate limits per tier ────────────────────────
    CLAUDE_LIMITS = {
        "seed":   {"haiku": 30,  "sonnet": 5,  "opus_per_week": 1},
        "growth": {"haiku": 80,  "sonnet": 15, "opus_per_week": 2},
        "pro":    {"haiku": 150, "sonnet": 30, "opus_per_week": 3},
        "elite":  {"haiku": -1,  "sonnet": 50, "opus_per_week": 7},
    }

    # ── Claude credit thresholds ───────────────────────────
    CREDIT_WARNING:  float = 15.0
    CREDIT_TOPUP:    float = 8.0
    CREDIT_CRITICAL: float = 3.0

    # ── Tier thresholds ────────────────────────────────────
    TIER_THRESHOLDS = {
        "seed":   (50,    299),
        "growth": (300,   699),
        "pro":    (700,   1499),
        "elite":  (1500,  999999),
    }

    # ── Monitoring ─────────────────────────────────────────

    def get_tier(self, capital: float) -> str:
        for tier, (low, high) in self.TIER_THRESHOLDS.items():
            if low <= capital <= high:
                return tier
        return "elite"

    def get_claude_limits(self, capital: float) -> dict:
        tier = self.get_tier(capital)
        return self.CLAUDE_LIMITS.get(tier, self.CLAUDE_LIMITS["seed"])


settings = Settings()