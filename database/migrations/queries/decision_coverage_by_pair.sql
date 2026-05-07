-- SCHEMA_VERSION: 1
-- File: decision_coverage_by_pair.sql
-- Purpose: Verify every active pair produces decision rows at the
--          expected cadence. Gap detection — if a pair is "active" in
--          pair_config but has zero decisions, the orchestrator wiring
--          is missing for it.

WITH active_pairs AS (
    SELECT pair FROM pair_config WHERE active = true
),
pair_decisions AS (
    SELECT
        pair,
        COUNT(*)                                     AS decision_count,
        MIN(decision_time)                           AS first_decision,
        MAX(decision_time)                           AS last_decision,
        SUM(CASE WHEN final_action = 'hold' THEN 1 ELSE 0 END)
                                                     AS hold_count,
        SUM(CASE WHEN final_action <> 'hold' THEN 1 ELSE 0 END)
                                                     AS active_count
    FROM orchestrator_decisions
    WHERE decision_time >= now() - INTERVAL '14 days'
    GROUP BY pair
)
SELECT
    a.pair,
    COALESCE(d.decision_count, 0) AS decision_count,
    COALESCE(d.hold_count, 0)     AS hold_count,
    COALESCE(d.active_count, 0)   AS active_signal_count,
    d.first_decision,
    d.last_decision,
    CASE
        WHEN d.decision_count IS NULL THEN 'MISSING_COVERAGE'
        WHEN now() - d.last_decision > INTERVAL '2 hour' THEN 'STALE'
        ELSE 'ok'
    END AS coverage_status
FROM active_pairs a
LEFT JOIN pair_decisions d USING (pair)
ORDER BY coverage_status DESC, a.pair;
