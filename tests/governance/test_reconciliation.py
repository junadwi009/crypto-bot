"""
Phase-2 tests for governance/reconciliation.

Council review questions:
  Q2: Can the system prove DB state matches exchange reality?
       — verified by reconciliation divergence detection test
  Verifier-must-fail-suspicious: every uncertainty path returns UNKNOWN
  Single-flight enforcement: overlap returns UNKNOWN + SEV-1
  Lock release ownership: only acquiring invocation can release
"""

from __future__ import annotations
from unittest.mock import patch, AsyncMock, MagicMock
import pytest

from governance import reconciliation as recon
from governance.exceptions import LayerZeroViolation
from governance.redis_acl import RedisACLViolation


# ── Governance mapping invariants (asserted at module load) ──────────────

def test_unknown_does_not_collapse_to_normal():
    assert recon.RECON_TO_GOVERNANCE[recon.ReconciliationStatus.UNKNOWN] != "normal"

def test_divergent_does_not_collapse_to_normal():
    assert recon.RECON_TO_GOVERNANCE[recon.ReconciliationStatus.DIVERGENT] != "normal"

def test_stale_does_not_collapse_to_normal():
    assert recon.RECON_TO_GOVERNANCE[recon.ReconciliationStatus.STALE] != "normal"

def test_clean_collapses_to_normal():
    assert recon.RECON_TO_GOVERNANCE[recon.ReconciliationStatus.CLEAN] == "normal"

def test_governance_mapping_complete():
    """Every enum value must have a governance mapping."""
    for status in recon.ReconciliationStatus:
        assert status in recon.RECON_TO_GOVERNANCE


# ── Verifier-must-fail-suspicious ────────────────────────────────────────

@pytest.mark.parametrize("failure_mode,fixture", [
    ("bybit_returns_none",      lambda: AsyncMock(return_value=None)),
    ("bybit_raises_timeout",    lambda: AsyncMock(side_effect=__import__('asyncio').TimeoutError())),
    ("bybit_raises_runtime",    lambda: AsyncMock(side_effect=RuntimeError("bybit down"))),
    ("bybit_returns_malformed", lambda: AsyncMock(return_value="not a list")),
])
@pytest.mark.asyncio
async def test_recon_never_returns_clean_on_failure(failure_mode, fixture):
    """All uncertainty paths must resolve to UNKNOWN, never CLEAN."""
    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis") as fake_redis, \
         patch.object(recon, "_fetch_bybit_state", new=fixture()), \
         patch.object(recon, "_fetch_db_state", new=AsyncMock(return_value=[])):

        fake_settings.PAPER_TRADE = False
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.release_lock = AsyncMock(return_value=True)

        result = await recon.reconcile()

        assert result != recon.ReconciliationStatus.CLEAN, \
            f"Failure mode {failure_mode} produced CLEAN — verifier optimistic"


# ── Q2: divergence detection ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_synthetic_divergence_detected():
    """Q2: when DB and Bybit disagree, status must be DIVERGENT."""
    bybit_orders = [{"orderId": "BYBIT_ONLY"}]
    db_trades = [{"bybit_order_id": "DB_ONLY", "pair": "BTC/USDT",
                  "amount_usd": 10.0, "entry_price": 100.0, "id": "x"}]

    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis") as fake_redis, \
         patch.object(recon, "_fetch_bybit_state",
                      new=AsyncMock(return_value=bybit_orders)), \
         patch.object(recon, "_fetch_db_state",
                      new=AsyncMock(return_value=db_trades)), \
         patch("governance.reconciliation.l0_supervisor.request_pause"
               if False else "governance.l0_supervisor.request_pause",
               new=AsyncMock()), \
         patch("notifications.telegram_bot.telegram") as fake_tg:

        fake_settings.PAPER_TRADE = False
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.release_lock = AsyncMock(return_value=True)
        fake_tg.send = AsyncMock()

        result = await recon.reconcile()

    assert result == recon.ReconciliationStatus.DIVERGENT


# ── Single-flight enforcement ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_overlap_returns_unknown_and_alerts(caplog):
    """When lock already held with VALID JSON payload, second invocation
    returns UNKNOWN + SEV-1.

    Updated for R2: lock value is now structured JSON, not raw token.
    The overlap behavior contract (UNKNOWN + alert) is preserved.
    """
    import json as _json
    from datetime import datetime, timedelta, timezone
    valid_prior_lock = _json.dumps({
        "token": "abc" * 10 + "12",   # 32-char hex-ish placeholder
        "started_at": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
        "scheduler_invocation_id": "def" * 10 + "34",
    }, sort_keys=True, separators=(",", ":"))

    with patch.object(recon, "redis") as fake_redis, \
         patch("notifications.telegram_bot.telegram") as fake_tg:
        fake_redis.set = AsyncMock(return_value=False)   # NX failed
        fake_redis.get = AsyncMock(return_value=valid_prior_lock)
        fake_redis.release_lock = AsyncMock(return_value=True)
        fake_tg.send = AsyncMock()

        result = await recon.reconcile()

    assert result == recon.ReconciliationStatus.UNKNOWN
    # SEV-1 alert text emitted to Telegram
    assert fake_tg.send.called


# ── Lock release uses UUID-token ownership ──────────────────────────────

@pytest.mark.asyncio
async def test_lock_acquired_with_unique_token():
    """Each invocation generates a fresh UUID token used as lock value.

    R2 update: lock value is structured JSON {token, started_at,
    scheduler_invocation_id}. The TOKEN field inside the JSON is the
    UUID4 hex (invariant 11). Two invocations must produce different
    tokens AND different invocation IDs.
    """
    import json as _json
    captured_values = []

    async def capture_set(key, value, **kwargs):
        if key == recon.RECON_LOCK_KEY:
            captured_values.append(value)
        return True

    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis") as fake_redis:

        fake_settings.PAPER_TRADE = True   # short-circuit body to CLEAN
        fake_redis.set = AsyncMock(side_effect=capture_set)
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.release_lock = AsyncMock(return_value=True)

        await recon.reconcile()
        await recon.reconcile()

    assert len(captured_values) >= 2
    assert captured_values[0] != captured_values[1]
    parsed = [_json.loads(v) for v in captured_values[:2]]
    # Invariant 11: each token is UUID4 hex
    for p in parsed:
        assert len(p["token"]) == 32
        assert len(p["scheduler_invocation_id"]) == 32
    # Distinct across invocations
    assert parsed[0]["token"] != parsed[1]["token"]
    assert parsed[0]["scheduler_invocation_id"] != parsed[1]["scheduler_invocation_id"]


@pytest.mark.asyncio
async def test_lock_released_via_release_lock_with_token():
    """Lock release goes through release_lock(key, lock_value), not delete.

    R2 update: the second argument is now the FULL JSON lock_value (the
    Lua script compares stored value to this exactly). The release path
    still uses Lua compare-and-delete; only this invocation's value
    matches. Invariant 11 (UUID token ownership) preserved within the JSON.
    """
    import json as _json
    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis") as fake_redis:

        fake_settings.PAPER_TRADE = True
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.release_lock = AsyncMock(return_value=True)
        # Critically, delete should NOT be called for lock release
        fake_redis.delete = AsyncMock()

        await recon.reconcile()

    fake_redis.release_lock.assert_called()
    args = fake_redis.release_lock.call_args.args
    assert args[0] == recon.RECON_LOCK_KEY
    # arg[1] is now full JSON lock_value; parse and verify token is UUID
    parsed = _json.loads(args[1])
    assert len(parsed["token"]) == 32
    assert "started_at" in parsed
    assert len(parsed["scheduler_invocation_id"]) == 32
    fake_redis.delete.assert_not_called()


# ── UNKNOWN is terminal default ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_starts_as_unknown_in_body():
    """Verify the body's `status = UNKNOWN` default is reachable.
    A function that simply raises before any branch must not produce CLEAN."""
    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis") as fake_redis, \
         patch.object(recon, "_fetch_bybit_state",
                      new=AsyncMock(side_effect=RuntimeError("immediate failure"))):
        fake_settings.PAPER_TRADE = False
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.release_lock = AsyncMock(return_value=True)

        result = await recon.reconcile()
    assert result == recon.ReconciliationStatus.UNKNOWN


# ── Paper mode short-circuit ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_paper_mode_returns_clean():
    """Paper mode is defined as CLEAN (no exchange state to compare)."""
    with patch("governance.reconciliation.settings") as fake_settings, \
         patch.object(recon, "redis") as fake_redis:
        fake_settings.PAPER_TRADE = True
        fake_redis.set = AsyncMock(return_value=True)
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.release_lock = AsyncMock(return_value=True)

        result = await recon.reconcile()
    assert result == recon.ReconciliationStatus.CLEAN
