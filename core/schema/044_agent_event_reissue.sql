-- Trace reissued deliveries to their fresh request events and operator reasons.
CREATE TABLE cairn.agent_event_reissue (
    delivery_id uuid PRIMARY KEY REFERENCES cairn.agent_delivery(delivery_id),
    event_id uuid NOT NULL UNIQUE REFERENCES cairn.agent_event(event_id),
    reissued_by text NOT NULL DEFAULT current_setting('cairn.caller'),
    reissued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    reason text NOT NULL
);
CREATE INDEX agent_event_reissue_event ON cairn.agent_event_reissue(event_id);
