-- ============================================================
-- Down: 0010_security_events_down.sql
-- WARNING: dropping security_events removes durable ACL violation
-- persistence. The structured CRITICAL log path remains, but log
-- streams may rotate before incident review. Only run if a follow-up
-- migration immediately replaces the sink.
-- ============================================================

DROP POLICY IF EXISTS security_events_no_update ON security_events;
DROP POLICY IF EXISTS security_events_no_delete ON security_events;
DROP POLICY IF EXISTS security_events_select    ON security_events;
DROP POLICY IF EXISTS security_events_insert    ON security_events;

DROP INDEX IF EXISTS idx_security_events_severity;
DROP INDEX IF EXISTS idx_security_events_caller;
DROP INDEX IF EXISTS idx_security_events_event_type;
DROP INDEX IF EXISTS idx_security_events_event_time;

DROP TABLE IF EXISTS security_events;
