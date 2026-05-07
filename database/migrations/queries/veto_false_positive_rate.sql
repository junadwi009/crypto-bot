-- SCHEMA_VERSION: 1
-- File: veto_false_positive_rate.sql
-- Purpose: For each distinct counterfactual veto reason, count how often
--          it would have fired during observe-only mode, and how many
--          of those blocked trades would have been profitable.
-- Output:  one row per veto reason with would_block, profitable_blocks,
--          and profitable_block_pct (the false-positive metric).
-- Phase-3 promotion gate: any veto with profitable_block_pct >= 50% is
--          a candidate for redesign before enforcement is enabled.

SELECT
    veto                                        AS veto_reason,
    COUNT(*)                                    AS would_block,
    SUM(CASE WHEN t.pnl_usd > 0 THEN 1 ELSE 0 END) AS profitable_blocks,
    ROUND(
        100.0 * SUM(CASE WHEN t.pnl_usd > 0 THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0),
        2
    )                                           AS profitable_block_pct
FROM orchestrator_decisions o
CROSS JOIN LATERAL unnest(o.layer_vetoes) AS veto
LEFT JOIN trades t
       ON t.bybit_order_id IS NOT NULL
      AND (o.layer_inputs->>'computed_size')::numeric = t.amount_usd
      AND o.pair = t.pair
      AND t.opened_at BETWEEN o.decision_time - INTERVAL '5 minute'
                          AND o.decision_time + INTERVAL '5 minute'
WHERE o.observe_only_passthrough = true
  AND o.decision_time >= now() - INTERVAL '14 days'
  AND array_length(o.layer_vetoes, 1) > 0
GROUP BY veto
ORDER BY would_block DESC;
