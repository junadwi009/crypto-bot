-- SCHEMA_VERSION: 1
-- File: reconciliation_state_history.sql
-- Purpose: Distribution of raw reconciliation states observed at
--          decision time across the observation window. Surfaces
--          how often the orchestrator was operating under stale or
--          unknown verifier output.

SELECT
    layer_inputs->>'reconciliation_raw'           AS raw_state,
    layer_inputs->>'reconciliation_implication'   AS governance_implication,
    COUNT(*)                                      AS decision_count,
    ROUND(
        100.0 * COUNT(*) / SUM(COUNT(*)) OVER (),
        2
    )                                             AS pct_of_total
FROM orchestrator_decisions
WHERE decision_time >= now() - INTERVAL '14 days'
GROUP BY 1, 2
ORDER BY decision_count DESC;
