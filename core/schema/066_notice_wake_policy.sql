-- The authenticated publisher may opt one notice into bounded routine wake
-- deferral. Event creation time anchors availability across retries/restarts.
ALTER TABLE cairn.agent_event ADD COLUMN notice_policy jsonb;
ALTER TABLE cairn.agent_event ADD CONSTRAINT agent_notice_policy_owner CHECK (
 notice_policy IS NULL OR (
  kind='notice' AND jsonb_typeof(notice_policy)='object'
  AND notice_policy ?& ARRAY['owner','version','urgency','defer_seconds']
  AND jsonb_typeof(notice_policy->'owner')='string'
  AND jsonb_typeof(notice_policy->'version')='number'
  AND jsonb_typeof(notice_policy->'urgency')='string'
  AND jsonb_typeof(notice_policy->'defer_seconds')='number'
  AND notice_policy->>'owner'=publisher
  AND notice_policy->>'version'='1'
  AND notice_policy->>'urgency' IN ('routine','urgent')
  AND notice_policy->>'defer_seconds' ~ '^(0|[1-9][0-9]?)$'
  AND (notice_policy->>'defer_seconds')::int BETWEEN 0 AND 60
  AND (notice_policy->>'urgency'='routine' OR notice_policy->>'defer_seconds'='0')
 ));
