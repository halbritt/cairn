-- Preserve the first publisher-owned rollback decision for a deferred notice.
-- The original event, policy, source and delivery rows remain in place.
CREATE TABLE cairn.agent_notice_flush (
 event_id uuid PRIMARY KEY REFERENCES cairn.agent_event(event_id),
 request_id uuid NOT NULL,
 flushed_at timestamptz NOT NULL,
 flushed_by text NOT NULL DEFAULT current_setting('cairn.caller'),
 released integer NOT NULL CHECK (released >= 0),
 already_ready integer NOT NULL CHECK (already_ready >= 0),
 leased integer NOT NULL CHECK (leased >= 0),
 terminal integer NOT NULL CHECK (terminal >= 0),
 UNIQUE(flushed_by, request_id)
);
