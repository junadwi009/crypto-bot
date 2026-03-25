"""
database/models.py
Pydantic models untuk validasi data sebelum masuk/keluar database.
Setiap model merepresentasikan satu tabel di Supabase.
"""

from __future__ import annotations
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, Field


# ── Helpers ─────────────────────────────────────────────────────────────────

class BaseDBModel(BaseModel):
    class Config:
        from_attributes = True
        populate_by_name = True


# ── CORE TRADING ─────────────────────────────────────────────────────────────

class Trade(BaseDBModel):
    id:             UUID    = Field(default_factory=uuid4)
    pair:           str
    side:           str                     # buy / sell
    amount_usd:     float
    entry_price:    float
    exit_price:     float | None = None
    pnl_usd:        float | None = None
    fee_usd:        float        = 0.0
    status:         str          = "open"   # open / closed / cancelled
    trigger_source: str | None   = None     # rule_based / haiku / sonnet / news
    bybit_order_id: str | None   = None
    is_paper:       bool         = True
    opened_at:      datetime     = Field(default_factory=datetime.utcnow)
    closed_at:      datetime | None = None

    @property
    def is_winner(self) -> bool:
        return (self.pnl_usd or 0) > 0

    def to_db(self) -> dict:
        d = self.model_dump()
        d["id"] = str(d["id"])
        d["opened_at"] = d["opened_at"].isoformat() if d["opened_at"] else None
        d["closed_at"] = d["closed_at"].isoformat() if d["closed_at"] else None
        return d


class TradeCreate(BaseDBModel):
    """Untuk membuat trade baru — id dan timestamps di-generate otomatis."""
    pair:           str
    side:           str
    amount_usd:     float
    entry_price:    float
    trigger_source: str | None = None
    bybit_order_id: str | None = None
    is_paper:       bool       = True


class PortfolioSnapshot(BaseDBModel):
    id:               UUID  = Field(default_factory=uuid4)
    snapshot_date:    date
    total_capital:    float
    trading_capital:  float
    infra_reserve:    float = 0.0
    emergency_buffer: float = 0.0
    current_tier:     str   = "seed"
    active_pairs:     list[str] = Field(default_factory=list)
    daily_pnl:        float = 0.0
    drawdown_pct:     float = 0.0

    def to_db(self) -> dict:
        d = self.model_dump()
        d["id"]            = str(d["id"])
        d["snapshot_date"] = d["snapshot_date"].isoformat()
        return d


class StrategyParams(BaseDBModel):
    pair:                    str   = "global"
    rsi_period:              int   = 14
    rsi_oversold:            float = 32.0
    rsi_overbought:          float = 71.0
    macd_fast:               int   = 12
    macd_slow:               int   = 26
    macd_signal:             int   = 9
    stop_loss_pct:           float = 2.2
    take_profit_pct:         float = 4.5
    atr_no_trade_threshold:  float = 0.8
    position_multiplier:     float = 1.0
    updated_by:              str   = "manual"


class PairConfig(BaseDBModel):
    pair:                  str
    active:                bool  = False
    strategy:              str   = "rsi_momentum"
    category:              str   = "Layer1"
    max_allocation_pct:    float = 100.0
    min_capital_required:  float = 50.0
    lrhr_score:            float = 0.0
    win_rate_30d:          float = 0.0
    inactive_reason:       str | None = None
    review_date:           date | None = None


# ── AI & MEMORY ──────────────────────────────────────────────────────────────

class OpusMemory(BaseDBModel):
    id:               UUID     = Field(default_factory=uuid4)
    week_start:       date
    week_end:         date
    win_rate:         float    = 0.0
    total_pnl:        float    = 0.0
    max_drawdown:     float    = 0.0
    total_trades:     int      = 0
    sharpe_ratio:     float    = 0.0
    patterns_found:   list     = Field(default_factory=list)
    actions_required: list     = Field(default_factory=list)
    params_updated:   dict     = Field(default_factory=dict)
    raw_analysis:     str      = ""
    token_cost:       float    = 0.0

    def to_db(self) -> dict:
        d = self.model_dump()
        d["id"]         = str(d["id"])
        d["week_start"] = d["week_start"].isoformat()
        d["week_end"]   = d["week_end"].isoformat()
        return d


class NewsItem(BaseDBModel):
    id:                 UUID     = Field(default_factory=uuid4)
    headline:           str
    source:             str      = ""
    url:                str      = ""
    pairs_mentioned:    list[str]= Field(default_factory=list)
    haiku_relevance:    float | None = None
    haiku_sentiment:    float | None = None
    haiku_urgency:      float | None = None
    sonnet_impact:      str | None   = None
    sonnet_action:      str | None   = None
    sonnet_confidence:  float | None = None
    price_at_news:      dict     = Field(default_factory=dict)
    price_1h_after:     dict     = Field(default_factory=dict)
    price_24h_after:    dict     = Field(default_factory=dict)
    prediction_correct: bool | None  = None
    injection_detected: bool     = False
    published_at:       datetime = Field(default_factory=datetime.utcnow)

    def to_db(self) -> dict:
        d = self.model_dump()
        d["id"]           = str(d["id"])
        d["published_at"] = d["published_at"].isoformat()
        return d


class NewsWeight(BaseDBModel):
    category:     str
    weight:       float = 0.5
    accuracy_1h:  float = 0.0
    accuracy_24h: float = 0.0
    sample_size:  int   = 0
    description:  str   = ""


class ClaudeUsage(BaseDBModel):
    usage_date:    date
    model:         str           # haiku / sonnet / opus
    calls_count:   int   = 0
    input_tokens:  int   = 0
    output_tokens: int   = 0
    cost_usd:      float = 0.0
    purpose:       str   = ""    # signal_validation / news / evaluation

    def calc_cost(self) -> float:
        """Hitung biaya berdasarkan model dan token usage."""
        rates = {
            "haiku":  {"input": 0.80,  "output": 4.00},
            "sonnet": {"input": 3.00,  "output": 15.00},
            "opus":   {"input": 15.00, "output": 75.00},
        }
        r = rates.get(self.model, rates["haiku"])
        return (
            self.input_tokens  / 1_000_000 * r["input"] +
            self.output_tokens / 1_000_000 * r["output"]
        )


# ── SYSTEM & LOG ─────────────────────────────────────────────────────────────

class BotEvent(BaseDBModel):
    id:          UUID     = Field(default_factory=uuid4)
    event_type:  str
    severity:    str      = "info"   # info / warning / critical
    message:     str      = ""
    data:        dict     = Field(default_factory=dict)
    notif_sent:  bool     = False
    created_at:  datetime = Field(default_factory=datetime.utcnow)

    def to_db(self) -> dict:
        d = self.model_dump()
        d["id"]         = str(d["id"])
        d["created_at"] = d["created_at"].isoformat()
        return d


class InfraTransaction(BaseDBModel):
    id:            UUID  = Field(default_factory=uuid4)
    txn_date:      date  = Field(default_factory=date.today)
    type:          str               # credit / debit
    amount:        float
    description:   str   = ""
    balance_after: float = 0.0

    def to_db(self) -> dict:
        d = self.model_dump()
        d["id"]       = str(d["id"])
        d["txn_date"] = d["txn_date"].isoformat()
        return d


class TierChange(BaseDBModel):
    id:                 UUID     = Field(default_factory=uuid4)
    from_tier:          str | None
    to_tier:            str
    capital_at_change:  float
    days_in_prev_tier:  int      = 0
    changed_at:         datetime = Field(default_factory=datetime.utcnow)

    def to_db(self) -> dict:
        d = self.model_dump()
        d["id"]         = str(d["id"])
        d["changed_at"] = d["changed_at"].isoformat()
        return d


class BacktestResult(BaseDBModel):
    id:           UUID  = Field(default_factory=uuid4)
    pair:         str
    strategy:     str
    period_start: date
    period_end:   date
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate:     float = 0.0
    max_drawdown: float = 0.0
    total_trades: int   = 0
    params_used:  dict  = Field(default_factory=dict)

    def to_db(self) -> dict:
        d = self.model_dump()
        d["id"]           = str(d["id"])
        d["period_start"] = d["period_start"].isoformat()
        d["period_end"]   = d["period_end"].isoformat()
        return d


# ── Signal (tidak disimpan ke DB — hanya used in-memory) ─────────────────────

class TradeSignal(BaseDBModel):
    """Output dari signal generator sebelum dikirim ke order manager."""
    pair:           str
    action:         str           # buy / sell / hold
    confidence:     float         # 0.0–1.0
    source:         str           # rule_based / haiku / sonnet
    reason:         str   = ""
    price:          float = 0.0
    suggested_size: float = 0.0   # USD amount
    stop_loss:      float = 0.0
    take_profit:    float = 0.0

    @property
    def is_actionable(self) -> bool:
        return self.action in ("buy", "sell") and self.confidence >= 0.6
