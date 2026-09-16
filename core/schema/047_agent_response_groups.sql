-- Fixed delivery membership and retained response observations, never payload copies.
CREATE TABLE cairn.agent_response_group (
 event_id uuid PRIMARY KEY REFERENCES cairn.agent_event(event_id),
 deadline timestamptz NOT NULL,
 partial_policy text NOT NULL CHECK(partial_policy IN ('all','partial')),
 state text NOT NULL DEFAULT 'open' CHECK(state IN ('open','collected','partial','incomplete')),
 closed_at timestamptz,
 code text NOT NULL DEFAULT '',
 CHECK((state='open')=(closed_at IS NULL)),
 CHECK((state='open')=(code=''))
);
CREATE INDEX agent_response_group_due ON cairn.agent_response_group(deadline,event_id) WHERE state='open';
CREATE TABLE cairn.agent_response_group_member (
 event_id uuid NOT NULL REFERENCES cairn.agent_response_group(event_id),
 consumer text NOT NULL,
 delivery_id uuid NOT NULL UNIQUE REFERENCES cairn.agent_delivery(delivery_id),
 response_event_id uuid UNIQUE REFERENCES cairn.agent_event(event_id),
 PRIMARY KEY(event_id,consumer)
);
CREATE TABLE cairn.agent_response_observation (
 response_event_id uuid PRIMARY KEY REFERENCES cairn.agent_event(event_id),
 group_event_id uuid NOT NULL REFERENCES cairn.agent_response_group(event_id),
 disposition text NOT NULL CHECK(disposition IN ('responded','duplicate','late','unmatched')),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX agent_response_observation_group ON cairn.agent_response_observation(group_event_id,response_event_id);
