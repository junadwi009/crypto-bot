"""
notifications/messages.py
Template semua pesan Telegram.
Terpusat di satu file agar mudah diubah.
"""

from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")


def _now_wib() -> str:
    return datetime.now(WIB).strftime("%d %b %Y %H:%M WIB")


# ── Bot lifecycle ─────────────────────────────────────────────────────────────

def bot_started(paper_trade: bool, capital: float, tier: str) -> str:
    mode = "PAPER TRADE" if paper_trade else "LIVE TRADING"
    return (
        f"Bot started\n\n"
        f"Mode:    {mode}\n"
        f"Capital: ${capital:.2f}\n"
        f"Tier:    {tier.upper()}\n"
        f"Time:    {_now_wib()}"
    )

def bot_stopped(reason: str) -> str:
    return (
        f"Bot stopped\n\n"
        f"Reason: {reason}\n"
        f"Time:   {_now_wib()}\n\n"
        f"Ketik /start untuk menghidupkan kembali."
    )

def bot_paused() -> str:
    return f"Bot di-pause. Tidak ada order baru.\nKetik /resume untuk melanjutkan."

def bot_resumed() -> str:
    return f"Bot dilanjutkan. Trading aktif kembali."


# ── Trading events ────────────────────────────────────────────────────────────

def trade_opened(pair: str, side: str, size: float,
                 price: float, source: str) -> str:
    emoji = "BUY" if side == "buy" else "SELL"
    return (
        f"{emoji} {pair}\n\n"
        f"Size:    ${size:.2f}\n"
        f"Price:   {price:.4f}\n"
        f"Source:  {source}\n"
        f"Time:    {_now_wib()}"
    )

def trade_closed(pair: str, pnl: float, reason: str,
                 exit_price: float) -> str:
    sign   = "+" if pnl >= 0 else ""
    result = "WIN" if pnl >= 0 else "LOSS"
    return (
        f"CLOSE {pair} — {result}\n\n"
        f"PnL:    {sign}${pnl:.2f}\n"
        f"Exit:   {exit_price:.4f}\n"
        f"Reason: {reason}\n"
        f"Time:   {_now_wib()}"
    )


# ── Alerts & warnings ─────────────────────────────────────────────────────────

def circuit_breaker_tripped(drawdown: float, capital: float) -> str:
    return (
        f"CIRCUIT BREAKER AKTIF\n\n"
        f"Drawdown: {drawdown*100:.1f}% (limit 15%)\n"
        f"Modal:    ${capital:.2f}\n"
        f"Status:   Semua trading dihentikan\n\n"
        f"Ketik /reset_cb untuk melanjutkan setelah review."
    )

def drawdown_warning(drawdown: float, capital: float) -> str:
    return (
        f"PERINGATAN DRAWDOWN\n\n"
        f"Drawdown hari ini: {drawdown*100:.1f}%\n"
        f"Modal sekarang:    ${capital:.2f}\n\n"
        f"Bot masih berjalan. Circuit breaker aktif di 15%."
    )


# ── Tier changes ──────────────────────────────────────────────────────────────

def tier_upgraded(from_tier: str, to_tier: str,
                  capital: float, days: int) -> str:
    return (
        f"NAIK TIER: {from_tier.upper()} → {to_tier.upper()}\n\n"
        f"Modal sekarang: ${capital:.2f}\n"
        f"Waktu di {from_tier}: {days} hari\n\n"
        f"Claude budget dan pair limit otomatis diupdate.\n"
        f"Cek /status untuk detail tier baru."
    )

def tier_almost(to_tier: str, gap: float, eta_days: float) -> str:
    return (
        f"Hampir naik ke tier {to_tier.upper()}\n\n"
        f"Butuh: +${gap:.2f} lagi\n"
        f"Estimasi: ~{eta_days:.0f} hari"
    )


# ── Infra reminders ───────────────────────────────────────────────────────────

def render_reminder(days_left: int, fund_balance: float) -> str:
    urgency = "HARI INI" if days_left <= 0 else f"{days_left} hari lagi"
    return (
        f"Pengingat tagihan — Render.com\n\n"
        f"Jatuh tempo: {urgency}\n"
        f"Tagihan:     $7.00\n"
        f"Infra fund:  ${fund_balance:.2f} tersedia\n\n"
        f"Klik tombol di bawah untuk membayar."
    )

def claude_credit_warning(balance: float, days_left: float,
                           level: str) -> str:
    levels = {
        "warning":  "Peringatan awal",
        "topup":    "Perlu topup segera",
        "critical": "KRITIS — Claude hampir mati",
    }
    action = {
        "warning":  "Bot tetap normal.",
        "topup":    "Sonnet dikurangi otomatis.",
        "critical": "Hanya Haiku aktif. Trading rule-based saja.",
    }
    return (
        f"Kredit Claude — {levels.get(level, level)}\n\n"
        f"Sisa kredit:    ${balance:.2f}\n"
        f"Estimasi habis: ~{days_left:.1f} hari\n\n"
        f"{action.get(level, '')}\n"
        f"Klik tombol di bawah untuk topup."
    )


# ── Opus weekly report ────────────────────────────────────────────────────────

def opus_weekly_report(summary: dict, actions: list) -> str:
    p0 = [a for a in actions if a.get("priority") == "P0"]
    p1 = [a for a in actions if a.get("priority") == "P1"]

    lines = [
        f"OPUS WEEKLY REPORT\n",
        f"Win rate:  {float(summary.get('win_rate',0))*100:.1f}%",
        f"PnL:       ${float(summary.get('total_pnl',0)):.2f}",
        f"Drawdown:  {float(summary.get('max_drawdown',0))*100:.1f}%",
        f"Trades:    {summary.get('total_trades', 0)}",
        f"\n{summary.get('assessment', '')}",
    ]

    if p0:
        lines.append(f"\nP0 — LAKUKAN HARI INI:")
        for a in p0:
            lines.append(f"  • {a.get('title', '')}")

    if p1:
        lines.append(f"\nP1 — Minggu ini:")
        for a in p1:
            lines.append(f"  • {a.get('title', '')}")

    lines.append(f"\nDetail lengkap di dashboard.")
    return "\n".join(lines)


# ── Paper trade completion ────────────────────────────────────────────────────

def paper_trade_complete(metrics: dict, credit_balance: float,
                          all_passed: bool) -> str:
    status = "SEMUA GATE PASSED" if all_passed else "BEBERAPA GATE BELUM PASSED"
    return (
        f"PAPER TRADE SELESAI\n\n"
        f"Status:      {status}\n"
        f"Win rate:    {float(metrics.get('win_rate',0))*100:.1f}%\n"
        f"Sharpe:      {float(metrics.get('sharpe',0)):.2f}\n"
        f"Max DD:      {float(metrics.get('max_drawdown',0))*100:.1f}%\n"
        f"Uptime:      {float(metrics.get('uptime',0))*100:.1f}%\n\n"
        f"Kredit Claude tersisa: ${credit_balance:.2f}\n\n"
        + ("Bot siap go live!" if all_passed
           else "Review gate yang belum passed dulu sebelum go live.")
    )


# ── Auth ──────────────────────────────────────────────────────────────────────

def need_pin() -> str:
    return "Sesi baru terdeteksi.\nMasukkan PIN 6 digit:"

def pin_correct(session_hours: int = 4) -> str:
    return f"PIN benar. Sesi aktif {session_hours} jam."

def pin_wrong(attempts_left: int) -> str:
    return f"PIN salah. Sisa percobaan: {attempts_left}"

def account_locked(minutes: int) -> str:
    return f"Akun terkunci {minutes} menit karena terlalu banyak percobaan PIN salah."

def unauthorized() -> str:
    return "Akses tidak diizinkan."
