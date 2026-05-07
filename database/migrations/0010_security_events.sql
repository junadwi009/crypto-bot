-- ============================================================
-- Migration: 0010_security_events.sql
-- Phase 2.5 R1 — Durable ACL event persistence.
--
-- Append-only sink for security events. Currently consumed by
-- governance/redis_acl.py to persist every RedisACLViolation BEFORE
-- raising. Persistence failure does NOT suppress the raise (R1
-- contract); it only logs CRITICAL.
--
-- Same append-only RLS model as orchestrator_decisions / audit_trail
-- / capital_ledger. UPDATE and DELETE are forbidden at the DB layer,
-- not by application convention.
--
-- AUDIT before apply: none required (new table).
-- ============================================================

CREATE TABLE IF NOT EXISTS security_events (
    id              bigserial PRIMARY KEY,

    -- Caller-supplied event time. Distinct from created_at (DB write
    -- time) to preserve the four-timestamp authority distinction
    -- (Council carve-out from Phase 2).
    event_time      timestamptz NOT NULL,

    event_type      text NOT NULL,
    severity        text NOT NULL CHECK (severity IN
                     ('critical','warning','info')),

    -- Schema version of the payload jsonb. Monotonic per Council
    -- constraint 13 (governance/redis_acl.py EVENT_SCHEMA_VERSION).
    schema_version  integer NOT NULL CHECK (schema_version >= 1),

    -- Module that triggered the event (for ACL violations: the
    -- offending caller; for bootstrap-fail: the undeclared module).
    caller          text NOT NULL,

    -- Full structured payload. Includes attempted_op, attempted_key,
    -- allowed_prefixes, etc. The same jsonb shape that the structured
    -- log line carries — duplication is intentional (R1: persistence
    -- AND log).
    payload         jsonb NOT NULL,

    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_security_events_event_time
    ON security_events(event_time DESC);
CREATE INDEX IF NOT EXISTS idx_security_events_event_type
    ON security_events(event_type);
CREATE INDEX IF NOT EXISTS idx_security_events_caller
    ON security_events(caller);
CREATE INDEX IF NOT EXISTS idx_security_events_severity
    ON security_events(severity);

-- ── Append-only RLS ──
ALTER TABLE security_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS security_events_no_update ON security_events;
CREATE POLICY security_events_no_update
    ON security_events FOR UPDATE USING (false);

DROP POLICY IF EXISTS security_events_no_delete ON security_events;
CREATE POLICY security_events_no_delete
    ON security_events FOR DELETE USING (false);

DROP POLICY IF EXISTS security_events_select ON security_events;
CREATE POLICY security_events_select
    ON security_events FOR SELECT USING (true);

DROP POLICY IF EXISTS security_events_insert ON security_events;
CREATE POLICY security_events_insert
    ON security_events FOR INSERT WITH CHECK (true);
