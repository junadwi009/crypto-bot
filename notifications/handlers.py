"""
notifications/handlers.py
Handler semua perintah Telegram.
Setiap handler selalu cek auth dulu sebelum eksekusi.
"""

from __future__ import annotations
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import settings
from database.client import db
from engine.circuit_breaker import circuit_breaker
from engine.position_manager import position_manager
from notifications.auth import auth
from notifications.messages import (
    need_pin, pin_correct, pin_wrong, account_locked,
    bot_paused, bot_resumed, unauthorized,
)
from utils.redis_client import redis

log = logging.getLogger("handlers")


# ── Auth middleware ───────────────────────────────────────────────────────────

async def _check_auth(update: Update) -> tuple[bool, str]:
    """Cek auth untuk setiap pesan masuk. Return (ok, reason)."""
    chat_id = update.effective_chat.id

    # Layer 1 — chat_id whitelist
    if not auth.is_allowed_chat(chat_id):
        log.warning("Unauthorized access attempt from chat_id=%d", chat_id)
        await update.message.reply_text(unauthorized())
        return False, "unauthorized"

    # Layer 2 & 3 — session check
    has_session, reason = await auth.check_session(chat_id)
    if not has_session:
        return False, reason

    return True, "ok"


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not auth.is_allowed_chat(chat_id):
        await update.message.reply_text(unauthorized())
        return

    # Cek apakah sudah ada session
    has_session, reason = await auth.check_session(chat_id)

    if reason.startswith("locked_"):
        mins = int(reason.split("_")[1]) // 60 + 1
        await update.message.reply_text(account_locked(mins))
        return

    if not has_session:
        await update.message.reply_text(need_pin())
        # Set state tunggu PIN
        await redis.setex(f"awaiting_pin:{chat_id}", 120, "1")
        return

    await _send_status(update)


# ── PIN handler (bukan command — plain text) ──────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle semua pesan teks — terutama untuk PIN input."""
    chat_id = update.effective_chat.id
    text    = (update.message.text or "").strip()

    if not auth.is_allowed_chat(chat_id):
        return

    # Cek apakah sedang menunggu PIN
    if await redis.get(f"awaiting_pin:{chat_id}"):
        if len(text) == 6 and text.isdigit():
            await redis.delete(f"awaiting_pin:{chat_id}")
            success, reason = await auth.verify_pin(chat_id, text)

            if success:
                await update.message.reply_text(pin_correct())
                await _send_status(update)
            elif reason == "locked_now":
                mins = auth.get_lockout_minutes()
                await update.message.reply_text(account_locked(mins))
            elif reason.startswith("wrong_"):
                left = int(reason.split("_")[1])
                await update.message.reply_text(pin_wrong(left))
                await redis.setex(f"awaiting_pin:{chat_id}", 120, "1")
        else:
            await update.message.reply_text(
                "PIN harus 6 digit angka. Coba lagi:"
            )
        return

    # Cek konfirmasi "KONFIRMASI" untuk aksi level 3
    if text == "KONFIRMASI":
        pending = await redis.get(f"pending_critical:{chat_id}")
        if pending:
            await redis.delete(f"pending_critical:{chat_id}")
            await _execute_critical_action(update, pending)
        return


# ── /status ───────────────────────────────────────────────────────────────────

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ok, reason = await _check_auth(update)
    if not ok:
        if reason == "need_pin":
            await update.message.reply_text(need_pin())
            await redis.setex(f"awaiting_pin:{update.effective_chat.id}", 120, "1")
        return
    await _send_status(update)


async def _send_status(update: Update):
    capital      = await db.get_current_capital()
    tier         = await db.get_current_tier()
    active_pairs = await db.get_active_pairs()
    open_trades  = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
    daily_pnl    = await position_manager.get_daily_pnl()
    cb_status    = await circuit_breaker.get_status()
    claude_mode  = await redis.get("claude_mode") or "normal"
    is_paused    = bool(await redis.get("bot_paused"))

    mode_label   = "PAPER TRADE" if settings.PAPER_TRADE else "LIVE"
    status_label = "PAUSED" if is_paused else "RUNNING"
    cb_label     = f"TRIPPED ({cb_status['reason']})" if cb_status["tripped"] else "OK"
    pnl_sign     = "+" if daily_pnl >= 0 else ""

    msg = (
        f"Status Bot\n\n"
        f"Mode:         {mode_label}\n"
        f"Status:       {status_label}\n"
        f"Capital:      ${capital:.2f}\n"
        f"Tier:         {tier.upper()}\n"
        f"Daily PnL:    {pnl_sign}${daily_pnl:.2f}\n"
        f"Open trades:  {len(open_trades)}\n"
        f"Active pairs: {', '.join(active_pairs) or 'none'}\n"
        f"Circuit CB:   {cb_label}\n"
        f"Claude mode:  {claude_mode}\n"
    )

    keyboard = [
        [InlineKeyboardButton("Pause bot",    callback_data="pause_bot"),
         InlineKeyboardButton("Lihat trades", callback_data="view_trades")],
        [InlineKeyboardButton("Weekly report",callback_data="weekly_report")],
    ]
    if cb_status["tripped"]:
        keyboard.insert(0, [
            InlineKeyboardButton("Reset circuit breaker",
                                 callback_data="reset_cb")
        ])

    await update.message.reply_text(
        msg,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ── /pause ────────────────────────────────────────────────────────────────────

async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ok, _ = await _check_auth(update)
    if not ok:
        return

    open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
    token = await auth.create_confirmation(
        update.effective_chat.id, "pause_bot"
    )

    keyboard = [[
        InlineKeyboardButton("Ya, pause sekarang",
                             callback_data=f"confirm_{token}"),
        InlineKeyboardButton("Batal",
                             callback_data="cancel"),
    ]]
    await update.message.reply_text(
        f"Pause bot trading?\n\n"
        f"Posisi terbuka: {len(open_trades)} "
        f"(tetap berjalan sampai SL/TP)\n"
        f"Tidak ada order baru yang akan dibuka.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── /resume ───────────────────────────────────────────────────────────────────

async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ok, _ = await _check_auth(update)
    if not ok:
        return

    await redis.delete("bot_paused")
    await db.log_event("bot_resumed", "Bot resumed via Telegram")
    await update.message.reply_text(bot_resumed())


# ── /reset_cb ─────────────────────────────────────────────────────────────────

async def cmd_reset_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ok, _ = await _check_auth(update)
    if not ok:
        return

    cb = await circuit_breaker.get_status()
    if not cb["tripped"]:
        await update.message.reply_text("Circuit breaker tidak aktif.")
        return

    token = await auth.create_confirmation(
        update.effective_chat.id, "reset_cb"
    )
    keyboard = [[
        InlineKeyboardButton("Ya, reset circuit breaker",
                             callback_data=f"confirm_{token}"),
        InlineKeyboardButton("Batal", callback_data="cancel"),
    ]]
    await update.message.reply_text(
        f"Reset circuit breaker?\n\n"
        f"Alasan trip: {cb['reason']}\n\n"
        f"Pastikan kamu sudah review kenapa circuit breaker aktif "
        f"sebelum melanjutkan.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── /emergency_lock ───────────────────────────────────────────────────────────

async def cmd_emergency_lock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not auth.is_allowed_chat(chat_id):
        return

    # Emergency lock tidak butuh session — siapapun yang punya HP bisa aktifkan
    await auth.emergency_lock(chat_id)
    await redis.set("bot_paused", "1")
    await update.message.reply_text(
        "EMERGENCY LOCK AKTIF\n\n"
        "Semua sesi dihapus.\n"
        "Bot di-pause.\n"
        "Ketik /start untuk login ulang."
    )
    log.warning("Emergency lock activated")


# ── /trades ───────────────────────────────────────────────────────────────────

async def cmd_trades(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ok, _ = await _check_auth(update)
    if not ok:
        return

    open_trades   = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
    recent_trades = await db.get_trades_for_period(days=3,
                                                    is_paper=settings.PAPER_TRADE)
    closed = [t for t in recent_trades if t.get("status") == "closed"]

    lines = [f"Open positions ({len(open_trades)}):"]
    for t in open_trades[:5]:
        lines.append(
            f"  {t['side'].upper()} {t['pair']} "
            f"${float(t['amount_usd']):.2f} @ {float(t['entry_price']):.4f}"
        )

    if closed:
        lines.append(f"\nRecent closed (3 hari):")
        for t in closed[:5]:
            pnl  = float(t.get("pnl_usd") or 0)
            sign = "+" if pnl >= 0 else ""
            lines.append(
                f"  {t['pair']} {sign}${pnl:.2f} "
                f"({t.get('trigger_source', '?')})"
            )

    await update.message.reply_text("\n".join(lines))


# ── Callback query handler ────────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    chat_id = query.message.chat_id
    data    = query.data

    await query.answer()

    if not auth.is_allowed_chat(chat_id):
        return

    # Konfirmasi token
    if data.startswith("confirm_"):
        token  = data.replace("confirm_", "")
        action = await auth.verify_confirmation(chat_id, token)
        if action:
            await _execute_confirmed_action(query, action)
        else:
            await query.edit_message_text("Token expired. Coba lagi.")

    elif data == "cancel":
        await query.edit_message_text("Dibatalkan.")

    elif data == "pause_bot":
        token = await auth.create_confirmation(chat_id, "pause_bot")
        keyboard = [[
            InlineKeyboardButton("Ya, pause",
                                 callback_data=f"confirm_{token}"),
            InlineKeyboardButton("Batal", callback_data="cancel"),
        ]]
        await query.edit_message_text(
            "Konfirmasi pause bot?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "reset_cb":
        token = await auth.create_confirmation(chat_id, "reset_cb")
        keyboard = [[
            InlineKeyboardButton("Ya, reset",
                                 callback_data=f"confirm_{token}"),
            InlineKeyboardButton("Batal", callback_data="cancel"),
        ]]
        await query.edit_message_text(
            "Konfirmasi reset circuit breaker?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "view_trades":
        open_trades = await db.get_open_trades(is_paper=settings.PAPER_TRADE)
        if not open_trades:
            await query.edit_message_text("Tidak ada open position.")
        else:
            lines = ["Open positions:"]
            for t in open_trades[:8]:
                lines.append(
                    f"  {t['side'].upper()} {t['pair']} "
                    f"${float(t['amount_usd']):.2f}"
                )
            await query.edit_message_text("\n".join(lines))

    elif data == "paid_render":
        from notifications.payment_reminder import payment_reminder
        await payment_reminder.on_render_paid()
        await query.edit_message_text(
            "Pembayaran Render dikonfirmasi. Terima kasih!\n"
            "Reminder berikutnya bulan depan."
        )

    elif data == "snooze_render":
        from notifications.payment_reminder import payment_reminder
        await payment_reminder.on_render_snooze()
        await query.edit_message_text("Oke, akan diingatkan besok.")

    elif data == "paid_claude":
        from notifications.payment_reminder import payment_reminder
        await payment_reminder.on_claude_paid()
        await query.edit_message_text(
            "Topup Anthropic dikonfirmasi!\n"
            "Claude mode kembali normal."
        )

    elif data == "stop_bot":
        token = await auth.create_confirmation(chat_id, "stop_bot")
        keyboard = [[
            InlineKeyboardButton("Ya, stop sekarang",
                                 callback_data=f"confirm_{token}"),
            InlineKeyboardButton("Batal", callback_data="cancel"),
        ]]
        await query.edit_message_text(
            "Stop bot?\n\nPosisi terbuka tetap berjalan sampai SL/TP.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data == "weekly_report":
        memories = await db.get_recent_opus_memory(weeks=1)
        if not memories:
            await query.edit_message_text(
                "Belum ada weekly report. "
                "Opus evaluation berjalan setiap Senin 08:00 WIB."
            )
        else:
            m = memories[0]
            wr = float(m.get("win_rate", 0)) * 100
            pnl = float(m.get("total_pnl", 0))
            actions = m.get("actions_required", [])
            p0 = [a for a in actions if a.get("priority") == "P0"]
            msg = (
                f"Weekly Report ({m['week_start']})\n\n"
                f"Win rate: {wr:.1f}%\n"
                f"PnL: ${pnl:.2f}\n"
                f"Trades: {m.get('total_trades', 0)}\n"
            )
            if p0:
                msg += f"\nP0 actions:\n"
                for a in p0:
                    msg += f"  • {a.get('title', '')}\n"
            await query.edit_message_text(msg)


async def _execute_confirmed_action(query, action: str):
    """Eksekusi aksi setelah konfirmasi."""
    if action == "pause_bot":
        await redis.set("bot_paused", "1")
        await db.log_event("bot_paused", "Bot paused via Telegram")
        await query.edit_message_text(bot_paused())

    elif action == "reset_cb":
        await circuit_breaker.reset()
        await query.edit_message_text(
            "Circuit breaker di-reset. Trading dilanjutkan."
        )

    elif action == "stop_bot":
        await redis.set("bot_stopping", "1")
        await query.edit_message_text(
            "Bot sedang berhenti dengan aman...\n"
            "Posisi terbuka tetap berjalan sampai SL/TP."
        )


async def _execute_critical_action(update: Update, action: str):
    """Eksekusi aksi kritis setelah ketik KONFIRMASI."""
    await update.message.reply_text(f"Mengeksekusi: {action}...")
