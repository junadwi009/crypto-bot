"""
database/client.py
Koneksi Supabase dan semua helper query.
Satu-satunya file yang boleh berbicara langsung ke Supabase.
"""

from __future__ import annotations
import logging
from datetime import date, datetime, timedelta

from supabase import create_client, Client
from config.settings import settings
from database.models import (
    TradeCreate, PortfolioSnapshot, StrategyParams,
    PairConfig, OpusMemory, NewsItem, NewsWeight,
    BotEvent, InfraTransaction, TierChange, BacktestResult,
)

log = logging.getLogger("database")


class DBClient:

    def __init__(self):
        self._client: Client | None = None

    def _get(self) -> Client:
        if self._client is None:
            self._client = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_SERVICE_KEY,
            )
        return self._client

    async def ping(self):
        self._get().table("pair_config").select("pair").limit(1).execute()
        log.info("Supabase: ping OK")

    # ════════════════════════════════════════════════════════
    # PORTFOLIO
    # ════════════════════════════════════════════════════════

    async def get_current_capital(self) -> float:
        res = (
            self._get()
            .table("portfolio_state")
            .select("total_capital")
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            return float(res.data[0]["total_capital"])
        return settings.INITIAL_CAPITAL

    async def get_current_tier(self) -> str:
        capital = await self.get_current_capital()
        return settings.get_tier(capital)

    async def save_portfolio_snapshot(self, snap: PortfolioSnapshot):
        self._get().table("portfolio_state").upsert(
            snap.to_db(), on_conflict="snapshot_date"
        ).execute()

    async def get_portfolio_history(self, days: int = 30) -> list[dict]:
        since = (date.today() - timedelta(days=days)).isoformat()
        res = (
            self._get()
            .table("portfolio_state")
            .select("*")
            .gte("snapshot_date", since)
            .order("snapshot_date", desc=True)
            .execute()
        )
        return res.data or []

    async def get_max_drawdown(self, days: int = 7) -> float:
        history = await self.get_portfolio_history(days)
        if not history:
            return 0.0
        return max(float(r.get("drawdown_pct", 0)) for r in history)

    # ════════════════════════════════════════════════════════
    # PAIRS
    # ════════════════════════════════════════════════════════

    async def get_active_pairs(self) -> list[str]:
        res = (
            self._get()
            .table("pair_config")
            .select("pair")
            .eq("active", True)
            .execute()
        )
        return [r["pair"] for r in (res.data or [])]

    async def get_pair_config(self, pair: str) -> PairConfig | None:
        res = (
            self._get()
            .table("pair_config")
            .select("*")
            .eq("pair", pair)
            .execute()
        )
        return PairConfig(**res.data[0]) if res.data else None

    async def update_pair_lrhr_score(self, pair: str, score: float, win_rate: float):
        self._get().table("pair_config").update({
            "lrhr_score":   round(score, 3),
            "win_rate_30d": round(win_rate, 4),
            "updated_at":   datetime.utcnow().isoformat(),
        }).eq("pair", pair).execute()

    async def set_pair_active(self, pair: str, active: bool, reason: str = ""):
        update = {"active": active, "updated_at": datetime.utcnow().isoformat()}
        if not active and reason:
            update["inactive_reason"] = reason
        self._get().table("pair_config").update(update).eq("pair", pair).execute()
        log.info("Pair %s set active=%s", pair, active)

    # ════════════════════════════════════════════════════════
    # STRATEGY PARAMS
    # ════════════════════════════════════════════════════════

    async def get_strategy_params(self, pair: str = "global") -> StrategyParams:
        # Coba cari pair-specific dulu
        if pair != "global":
            res = (
                self._get()
                .table("strategy_params")
                .select("*")
                .eq("pair", pair)
                .execute()
            )
            if res.data:
                return StrategyParams(**res.data[0])

        # Fallback ke global
        res = (
            self._get()
            .table("strategy_params")
            .select("*")
            .eq("pair", "global")
            .execute()
        )
        if res.data:
            return StrategyParams(**res.data[0])

        # Tidak ada di DB sama sekali — return default
        return StrategyParams()

    async def update_strategy_params(self, pair: str, params: dict, updated_by: str = "opus"):
        params["updated_at"] = datetime.utcnow().isoformat()
        params["updated_by"] = updated_by
        self._get().table("strategy_params").update(params).eq("pair", pair).execute()
        log.info("Strategy params updated for %s by %s", pair, updated_by)

    # ════════════════════════════════════════════════════════
    # TRADES
    # ════════════════════════════════════════════════════════

    async def save_trade(self, trade: TradeCreate) -> str:
        data = {
            "pair":           trade.pair,
            "side":           trade.side,
            "amount_usd":     trade.amount_usd,
            "entry_price":    trade.entry_price,
            "trigger_source": trade.trigger_source,
            "bybit_order_id": trade.bybit_order_id,
            "is_paper":       trade.is_paper,
            "status":         "open",
        }
        res = self._get().table("trades").insert(data).execute()
        trade_id = res.data[0]["id"] if res.data else ""
        log.debug("Trade saved: %s %s @ %.2f (id=%s)",
                  trade.side, trade.pair, trade.entry_price, trade_id)
        return trade_id

    async def close_trade(self, trade_id: str, exit_price: float,
                          pnl_usd: float, fee_usd: float = 0):
        self._get().table("trades").update({
            "exit_price": exit_price,
            "pnl_usd":    round(pnl_usd, 4),
            "fee_usd":    round(fee_usd, 4),
            "status":     "closed",
            "closed_at":  datetime.utcnow().isoformat(),
        }).eq("id", trade_id).execute()
        log.info("Trade closed: id=%s pnl=$%.2f", trade_id, pnl_usd)

    async def cancel_trade(self, trade_id: str):
        self._get().table("trades").update({
            "status":    "cancelled",
            "closed_at": datetime.utcnow().isoformat(),
        }).eq("id", trade_id).execute()

    async def get_open_trades(self, is_paper: bool | None = None) -> list[dict]:
        q = self._get().table("trades").select("*").eq("status", "open")
        if is_paper is not None:
            q = q.eq("is_paper", is_paper)
        return q.execute().data or []

    async def get_trades_for_period(self, days: int = 7,
                                    is_paper: bool | None = None) -> list[dict]:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        q = (
            self._get()
            .table("trades")
            .select("*")
            .eq("status", "closed")
            .gte("opened_at", since)
            .order("opened_at", desc=True)
        )
        if is_paper is not None:
            q = q.eq("is_paper", is_paper)
        return q.execute().data or []

    async def get_win_rate(self, days: int = 30, is_paper: bool | None = None) -> float:
        trades = await self.get_trades_for_period(days, is_paper)
        if not trades:
            return 0.0
        winners = sum(1 for t in trades if (t.get("pnl_usd") or 0) > 0)
        return round(winners / len(trades), 4)

    async def get_total_pnl(self, days: int = 7) -> float:
        trades = await self.get_trades_for_period(days)
        return round(sum(float(t.get("pnl_usd") or 0) for t in trades), 4)

    # ════════════════════════════════════════════════════════
    # BOT EVENTS
    # ════════════════════════════════════════════════════════

    async def log_event(self, event_type: str, message: str,
                        severity: str = "info", data: dict = None,
                        notif_sent: bool = False):
        event = BotEvent(
            event_type = event_type,
            severity   = severity,
            message    = message,
            data       = data or {},
            notif_sent = notif_sent,
        )
        self._get().table("bot_events").insert(event.to_db()).execute()

    async def get_recent_events(self, hours: int = 24,
                                severity: str | None = None) -> list[dict]:
        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        q = (
            self._get()
            .table("bot_events")
            .select("*")
            .gte("created_at", since)
            .order("created_at", desc=True)
        )
        if severity:
            q = q.eq("severity", severity)
        return q.execute().data or []

    # ════════════════════════════════════════════════════════
    # NEWS
    # ════════════════════════════════════════════════════════

    async def save_news(self, item: NewsItem) -> str:
        res = self._get().table("news_items").insert(item.to_db()).execute()
        return res.data[0]["id"] if res.data else ""

    async def update_news_outcome(self, news_id: str, price_1h: dict,
                                  price_24h: dict, correct: bool):
        self._get().table("news_items").update({
            "price_1h_after":     price_1h,
            "price_24h_after":    price_24h,
            "prediction_correct": correct,
        }).eq("id", news_id).execute()

    async def get_news_weights(self) -> dict[str, NewsWeight]:
        res = self._get().table("news_weights").select("*").execute()
        return {r["category"]: NewsWeight(**r) for r in (res.data or [])}

    async def update_news_weights(self, weights: dict[str, dict]):
        for category, data in weights.items():
            data["last_updated"] = datetime.utcnow().isoformat()
            self._get().table("news_weights").upsert(
                {"category": category, **data}, on_conflict="category"
            ).execute()
        log.info("News weights updated: %d categories", len(weights))

    # ════════════════════════════════════════════════════════
    # CLAUDE USAGE
    # ════════════════════════════════════════════════════════

    async def log_claude_usage(self, model: str, calls: int, input_tok: int,
                               output_tok: int, cost: float, purpose: str):
        today = date.today().isoformat()
        existing = (
            self._get()
            .table("claude_usage")
            .select("calls_count, input_tokens, output_tokens, cost_usd")
            .eq("usage_date", today)
            .eq("model", model)
            .eq("purpose", purpose)
            .execute()
        )
        if existing.data:
            row = existing.data[0]
            self._get().table("claude_usage").update({
                "calls_count":   int(row["calls_count"])   + calls,
                "input_tokens":  int(row["input_tokens"])  + input_tok,
                "output_tokens": int(row["output_tokens"]) + output_tok,
                "cost_usd":      round(float(row["cost_usd"]) + cost, 4),
            }).eq("usage_date", today).eq("model", model).eq("purpose", purpose).execute()
        else:
            self._get().table("claude_usage").insert({
                "usage_date":    today,
                "model":         model,
                "purpose":       purpose,
                "calls_count":   calls,
                "input_tokens":  input_tok,
                "output_tokens": output_tok,
                "cost_usd":      round(cost, 4),
            }).execute()

    async def get_claude_cost_this_month(self) -> float:
        first_day = date.today().replace(day=1).isoformat()
        res = (
            self._get()
            .table("claude_usage")
            .select("cost_usd")
            .gte("usage_date", first_day)
            .execute()
        )
        return round(sum(float(r["cost_usd"]) for r in (res.data or [])), 4)

    async def get_claude_calls_today(self, model: str) -> int:
        today = date.today().isoformat()
        res = (
            self._get()
            .table("claude_usage")
            .select("calls_count")
            .eq("usage_date", today)
            .eq("model", model)
            .execute()
        )
        return sum(int(r["calls_count"]) for r in (res.data or []))

    # ════════════════════════════════════════════════════════
    # OPUS MEMORY
    # ════════════════════════════════════════════════════════

    async def save_opus_memory(self, memory: OpusMemory):
        self._get().table("opus_memory").upsert(
            memory.to_db(), on_conflict="week_start"
        ).execute()
        log.info("Opus memory saved: week %s", memory.week_start)

    async def get_recent_opus_memory(self, weeks: int = 4) -> list[dict]:
        res = (
            self._get()
            .table("opus_memory")
            .select("*")
            .order("week_start", desc=True)
            .limit(weeks)
            .execute()
        )
        return res.data or []

    async def get_latest_opus_actions(self) -> list[dict]:
        res = (
            self._get()
            .table("opus_memory")
            .select("actions_required, week_start")
            .order("week_start", desc=True)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("actions_required") or []
        return []

    # ════════════════════════════════════════════════════════
    # INFRA FUND
    # ════════════════════════════════════════════════════════

    async def get_infra_balance(self) -> float:
        res = (
            self._get()
            .from_("infra_fund_balance")
            .select("current_balance")
            .execute()
        )
        if res.data:
            return float(res.data[0]["current_balance"])
        return 0.0

    async def add_infra_credit(self, amount: float, description: str):
        balance = await self.get_infra_balance()
        txn = InfraTransaction(
            type          = "credit",
            amount        = amount,
            description   = description,
            balance_after = round(balance + amount, 4),
        )
        self._get().table("infra_fund").insert(txn.to_db()).execute()
        log.info("Infra fund +$%.2f | balance=$%.2f", amount, txn.balance_after)

    async def add_infra_debit(self, amount: float, description: str):
        balance = await self.get_infra_balance()
        txn = InfraTransaction(
            type          = "debit",
            amount        = amount,
            description   = description,
            balance_after = round(balance - amount, 4),
        )
        self._get().table("infra_fund").insert(txn.to_db()).execute()

    # ════════════════════════════════════════════════════════
    # TIER HISTORY
    # ════════════════════════════════════════════════════════

    async def log_tier_change(self, from_tier: str | None, to_tier: str,
                               capital: float, days_in_prev: int = 0):
        change = TierChange(
            from_tier         = from_tier,
            to_tier           = to_tier,
            capital_at_change = capital,
            days_in_prev_tier = days_in_prev,
        )
        self._get().table("tier_history").insert(change.to_db()).execute()
        log.info("Tier: %s → %s | capital=$%.2f", from_tier, to_tier, capital)

    # ════════════════════════════════════════════════════════
    # BACKTEST
    # ════════════════════════════════════════════════════════

    async def save_backtest_result(self, result: BacktestResult):
        self._get().table("backtest_results").insert(result.to_db()).execute()
        log.info("Backtest saved: %s | win_rate=%.1f%% | sharpe=%.2f",
                 result.pair, result.win_rate * 100, result.sharpe_ratio)

    async def get_best_backtest(self, pair: str) -> dict | None:
        res = (
            self._get()
            .table("backtest_results")
            .select("*")
            .eq("pair", pair)
            .order("sharpe_ratio", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    # ════════════════════════════════════════════════════════
    # SUMMARY (untuk Opus & dashboard)
    # ════════════════════════════════════════════════════════

    async def get_weekly_summary(self, days: int = 7) -> dict:
        trades   = await self.get_trades_for_period(days)
        capital  = await self.get_current_capital()
        drawdown = await self.get_max_drawdown(days)

        closed  = [t for t in trades if t.get("status") == "closed"]
        winners = [t for t in closed if (t.get("pnl_usd") or 0) > 0]
        pnl     = sum(float(t.get("pnl_usd") or 0) for t in closed)
        fees    = sum(float(t.get("fee_usd") or 0) for t in closed)

        return {
            "period_days":  days,
            "capital":      capital,
            "total_trades": len(closed),
            "win_rate":     round(len(winners) / len(closed), 4) if closed else 0,
            "total_pnl":    round(pnl, 4),
            "total_fees":   round(fees, 4),
            "net_pnl":      round(pnl - fees, 4),
            "max_drawdown": drawdown,
            "tier":         await self.get_current_tier(),
            "active_pairs": await self.get_active_pairs(),
        }

    async def get_tier_history(self) -> list:
        """Ambil riwayat perubahan tier, terbaru dulu."""
        res = (
            self._get()
            .table("tier_history")
            .select("*")
            .order("changed_at", desc=True)
            .limit(10)
            .execute()
        )
        return res.data or []

    async def get_all_pairs(self) -> list:
        """Ambil semua pair (aktif dan tidak aktif) — untuk dashboard."""
        from database.models import PairConfig
        res = self._get().table("pair_config").select("*").execute()
        return [PairConfig(**r) for r in (res.data or [])]


db = DBClient()