-- Drain admitted transactions and invalidate blocked old RR/Serializable snapshots.
-- A lock alone does not change the tuple and can leave an old snapshot usable.
UPDATE cairn.restore_admission SET paused=paused WHERE singleton;

-- Admission starts disabled. Only explicit operator configuration creates a row.
CREATE TABLE cairn.agent_event_admission_policy (
 repo text PRIMARY KEY,
 revision bigint NOT NULL CHECK (revision > 0),
 policy jsonb NOT NULL CHECK (jsonb_typeof(policy)='object' AND policy->>'version'='1' AND policy->>'mode' IN ('disabled','observe','enforce')),
 updated_by text NOT NULL DEFAULT current_setting('cairn.caller'),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX agent_event_publisher_recent ON cairn.agent_event(repo,publisher,created_at);
CREATE INDEX agent_delivery_outstanding ON cairn.agent_delivery(consumer,event_id) WHERE state IN ('pending','leased');
CREATE INDEX agent_subscription_topic_active ON cairn.agent_subscription(repo,topic,consumer) WHERE active;
CREATE INDEX agent_event_collection_recent ON cairn.agent_event(repo,created_at);
