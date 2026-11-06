-- Fixed dimensions bound retained diagnostics even when producers churn UUIDs.
CREATE TABLE cairn.agent_event_admission_refusal (
 repo text NOT NULL,
 dimension text NOT NULL CHECK (dimension IN ('topic_recipients','recipient_outstanding','producer_recipient_outstanding','collection_outstanding','producer_publications','producer_deliveries','owner_publications','owner_deliveries','collection_publications','collection_deliveries')),
 policy_revision bigint NOT NULL CHECK (policy_revision>0),
 refusal_count bigint NOT NULL DEFAULT 1 CHECK (refusal_count>0),
 retry_after_seconds integer NOT NULL CHECK (retry_after_seconds BETWEEN 0 AND 3600),
 last_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(repo,dimension)
);
