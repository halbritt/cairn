-- Operational communication, deliberately separate from searchable memory.
CREATE TABLE cairn.agent_event (
 event_id uuid PRIMARY KEY,
 position bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 repo text NOT NULL,
 publisher text NOT NULL DEFAULT current_setting('cairn.caller'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 kind text NOT NULL,
 record_id uuid NOT NULL,
 version integer NOT NULL,
 sensitivity text NOT NULL CHECK(sensitivity IN ('local','shareable')),
 destination_type text NOT NULL CHECK(destination_type IN ('agent','topic')),
 destination_name text NOT NULL,
 causation_id uuid REFERENCES cairn.agent_event(event_id),
 correlation_id uuid,
 FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version)
);
CREATE INDEX agent_event_repo_position ON cairn.agent_event(repo,position);

CREATE TABLE cairn.agent_subscription (
 repo text NOT NULL,
 consumer text NOT NULL DEFAULT current_setting('cairn.caller'),
 topic text NOT NULL,
 active boolean NOT NULL,
 changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(repo,consumer,topic)
);
CREATE TABLE cairn.agent_subscription_change (
 change_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 repo text NOT NULL,
 consumer text NOT NULL DEFAULT current_setting('cairn.caller'),
 topic text NOT NULL,
 active boolean NOT NULL,
 changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE cairn.agent_delivery (
 delivery_id uuid PRIMARY KEY,
 event_id uuid NOT NULL REFERENCES cairn.agent_event(event_id),
 consumer text NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','leased','handled','ignored','failed')),
 lease_id uuid,
 lease_until timestamptz,
 attempts integer NOT NULL DEFAULT 0,
 completed_at timestamptz,
 code text NOT NULL DEFAULT '',
 result_id uuid,
 result_version integer,
 UNIQUE(event_id,consumer),
 FOREIGN KEY(result_id,result_version) REFERENCES cairn.record_version(record_id,version),
 CHECK((result_id IS NULL) = (result_version IS NULL)),
 CHECK((state='leased') = (lease_id IS NOT NULL AND lease_until IS NOT NULL)),
 CHECK((state IN ('handled','ignored','failed')) = (completed_at IS NOT NULL))
);
CREATE INDEX agent_delivery_inbox ON cairn.agent_delivery(consumer,state,event_id);
