-- SCHEMA_VERSION: 1
-- File: counterfactual_vs_actual_pnl.sql
-- Purpose: Aggregate the realized PnL of trades that the orchestrator's
--          counterfactual would have rejected vs accepted, during the
--          observe-only window.
-- Output:  two rows: counterfactual_decision IN ('would_block','would_allow')
--          with sum(pnl), count, win_rate.
-- Interpretation: if "would_block" PnL is sharply negative, enforcement
--          is profitable to enable. If positive, enforcement is destroying
--          alpha — investigate vetoes individually with veto_false_positive_rate.

SELECT
    CASE
        WHEN array_length(o.layer_vetoes, 1) > 0 THEN 'would_block'
        ELSE 'would_allow'
    END                                                       AS counterfactual_decision,
    COUNT(*)                                                  AS decision_count,
    COUNT(t.id)                                               AS trades_actually_executed,
    COALESCE(SUM(t.pnl_usd), 0)                               AS total_pnl_usd,
    COALESCE(AVG(t.pnl_usd), 0)                               AS avg_pnl_usd,
    ROUND(
        100.0 * SUM(CASE WHEN t.pnl_usd > 0 THEN 1 ELSE 0 END)
        / NULLIF(COUNT(t.id), 0),
        2
    )                                                         AS win_rate_pct
FROM orchestrator_decisions o
LEFT JOIN trades t
       ON t.bybit_order_id IS NOT NULL
      AND o.pair = t.pair
      AND t.opened_at BETWEEN o.decision_time - INTERVAL '5 minute'
                          AND o.decision_time + INTERVAL '5 minute'
      AND t.status = 'closed'
WHERE o.observe_only_passthrough = true
  AND o.decision_time >= now() - INTERVAL '14 days'
GROUP BY 1
ORDER BY 1;
