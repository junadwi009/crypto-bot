# Versioned review queries

Queries committed alongside the migrations that defined the underlying
schemas. Each file carries a `-- SCHEMA_VERSION: N` header indicating
the layer_inputs / event payload shape it was written against.

## Constraint (Council, locked)

- `LAYER_INPUTS_SCHEMA_VERSION` and `EVENT_SCHEMA_VERSION` are
  monotonically increasing integers. Never reuse, never repurpose.
- A query written against schema vN remains semantically valid for rows
  written when vN was the current version.
- When schema is bumped, queries that should work against the new shape
  must be re-versioned (copy to a `_vM.sql` variant) — not edited in place.

## Usage during the 14-day observe-only review (Phase 3 promotion gate)

Run these against the production observe-only dataset before any
ORCHESTRATOR_OBSERVE_ONLY=false flip is considered. The Council will
inspect the output of each.

| Query | Question it answers |
|---|---|
| `veto_false_positive_rate.sql` | Of the vetoes that would have fired, how many would have blocked profitable trades? |
| `counterfactual_vs_actual_pnl.sql` | What would PnL have been if every counterfactual veto had been honored? |
| `governance_mode_distribution.sql` | What fraction of decisions occurred in each governance mode? |
| `decision_coverage_by_pair.sql` | Does every active pair produce decision rows at the expected cadence? |
| `reconciliation_state_history.sql` | What was the recon state at each decision time? Stale/unknown distribution? |
| `acl_violation_log.sql` | Were there ACL violations during the observation window? |
