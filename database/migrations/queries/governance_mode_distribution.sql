-- SCHEMA_VERSION: 1
-- File: governance_mode_distribution.sql
-- Purpose: Distribution of governance modes observed during the
--          observation window. Both actual mode at decision time and
--          counterfactual mode reported.

SELECT
    governance_mode             AS actual_mode,
    counterfactual_mode         AS would_be_mode,
    COUNT(*)                    AS decisions,
    ROUND(
        100.0 * COUNT(*) / SUM(COUNT(*)) OVER (),
        2
    )                           AS pct_of_total
FROM orchestrator_decisions
WHERE decision_time >= now() - INTERVAL '14 days'
GROUP BY governance_mode, counterfactual_mode
ORDER BY decisions DESC;
