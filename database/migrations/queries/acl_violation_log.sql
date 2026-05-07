-- SCHEMA_VERSION: 1
-- File: acl_violation_log.sql
-- Purpose: Surface all ACL violations recorded in audit_trail during
--          the observation window. Any non-zero count is a security
--          finding requiring root-cause analysis before enforcement
--          mode promotion.

SELECT
    event_time,
    actor_type,
    actor_id,
    payload->>'attempted_op'        AS attempted_op,
    payload->>'attempted_key'       AS attempted_key,
    payload->'allowed_prefixes'     AS allowed_prefixes
FROM audit_trail
WHERE action = 'redis_acl_violation'
  AND event_time >= now() - INTERVAL '14 days'
ORDER BY event_time DESC
LIMIT 1000;
