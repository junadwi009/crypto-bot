# Database Migrations

Numbered, ordered migrations against the Supabase Postgres schema.
Files in this directory are applied in lexical order.

## Convention

```
NNNN_description.sql        — up migration (idempotent)
NNNN_description_down.sql   — down migration (reverses up)
```

## Pre-apply checklist

1. **Take a database snapshot.** Supabase paid tier has PITR; on free tier
   run `pg_dump` to a local file before applying.
2. **Run the audit query inside the up migration's `-- AUDIT:` section
   manually first.** If any rows return, the migration will fail mid-flight.
   Generate a data-cleanup migration instead of weakening the constraint.
3. **Apply down migration first on a copy** to verify reversibility.
4. **Apply up migration on a copy** and re-run the integration tests
   that exercise the constrained columns.
5. Only then apply on production.

## Why DO blocks

Postgres pre-15 does not support `ADD CONSTRAINT IF NOT EXISTS` for CHECKs.
Migrations use `DO $$ ... $$` blocks with `pg_constraint` lookups so they
are safe to re-apply.
