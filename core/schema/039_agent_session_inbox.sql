-- Native conversation handling retains ownership beyond lease expiry. The host
-- reconciles completed handling or an observed end of the owning process/turn.
CREATE TABLE cairn.agent_session_attempt (
 attempt_id uuid PRIMARY KEY,
 agent_id uuid NOT NULL REFERENCES cairn.agent_session(agent_id),
 execution_id uuid NOT NULL,
 owner text NOT NULL DEFAULT current_setting('cairn.caller'),
 delivery_id uuid NOT NULL REFERENCES cairn.agent_delivery(delivery_id),
 lease_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 finished_at timestamptz,
 reason text NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX agent_session_one_attempt ON cairn.agent_session_attempt(agent_id) WHERE finished_at IS NULL;
CREATE UNIQUE INDEX agent_session_delivery_hold ON cairn.agent_session_attempt(delivery_id) WHERE finished_at IS NULL;
-- Empty polls are durable too: a lost empty response must not claim a later
-- arrival when an ending native turn retries its original request.
CREATE TABLE cairn.agent_session_poll (
 request_id uuid PRIMARY KEY,
 agent_id uuid NOT NULL REFERENCES cairn.agent_session(agent_id),
 execution_id uuid NOT NULL,
 owner text NOT NULL DEFAULT current_setting('cairn.caller'),
 attempt_id uuid REFERENCES cairn.agent_session_attempt(attempt_id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
