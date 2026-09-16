ALTER TABLE cairn.agent_delivery ADD COLUMN available_at timestamptz NOT NULL DEFAULT clock_timestamp();
CREATE TABLE cairn.agent_wake_attempt (
 attempt_id uuid PRIMARY KEY,
 delivery_id uuid NOT NULL REFERENCES cairn.agent_delivery(delivery_id),
 repo text NOT NULL,
 consumer text NOT NULL DEFAULT current_setting('cairn.caller'),
 lease_id uuid NOT NULL,
 state text NOT NULL DEFAULT 'prepared' CHECK(state IN ('prepared','starting','running','finished')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 finished_at timestamptz,
 receipt_id uuid REFERENCES cairn.retrieval_receipt(receipt_id),
 process_state text NOT NULL DEFAULT '',
 reason text NOT NULL DEFAULT '',
 CHECK((state='finished') = (finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX agent_wake_one_active ON cairn.agent_wake_attempt(repo,consumer) WHERE finished_at IS NULL;
CREATE INDEX agent_wake_delivery ON cairn.agent_wake_attempt(delivery_id);
