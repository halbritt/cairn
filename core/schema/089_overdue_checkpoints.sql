-- Overdue checkpoint and expect_by notices (CAIRN-467): optional advisory
-- expect_by deadline on request events, partial deadline indexes, and durable
-- per-deadline deduplication and pending-notice coalescing across restarts.

CREATE TABLE cairn.agent_overdue_fence (
 generation bigint PRIMARY KEY,
 fenced_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 max_event_position bigint NOT NULL DEFAULT 0
);

ALTER TABLE cairn.agent_event
 ADD COLUMN request_expect_by timestamptz,
 ADD CONSTRAINT agent_request_expect_by_valid CHECK (
  request_expect_by IS NULL OR kind = 'request'
 );

CREATE INDEX agent_event_request_expect_by_idx
 ON cairn.agent_event(repo, request_expect_by)
 WHERE kind = 'request' AND request_expect_by IS NOT NULL;

CREATE INDEX agent_event_notice_expect_next_by_idx
 ON cairn.agent_event(repo, notice_expect_next_by)
 WHERE kind = 'notice' AND notice_expect_next_by IS NOT NULL;

CREATE TABLE cairn.agent_overdue_checkpoint (
 checkpoint_id uuid PRIMARY KEY,
 repo text NOT NULL CHECK (octet_length(btrim(repo)) BETWEEN 1 AND 256 AND repo <> '*'),
 subscriber_consumer text NOT NULL CHECK (octet_length(btrim(subscriber_consumer)) BETWEEN 1 AND 256),
 source_kind text NOT NULL CHECK (source_kind IN ('notice', 'request', 'workstream')),
 source_key text NOT NULL CHECK (octet_length(btrim(source_key)) BETWEEN 1 AND 256),
 topic_key text NOT NULL DEFAULT '' CHECK (octet_length(topic_key) <= 256),
 sender text NOT NULL CHECK (octet_length(btrim(sender)) BETWEEN 1 AND 256),
 sensitivity text NOT NULL CHECK (sensitivity IN ('local', 'shareable')),
 deadline_at timestamptz NOT NULL,
 source_updated_at timestamptz NOT NULL,
 source_event_id uuid REFERENCES cairn.agent_event(event_id),
 source_record_id uuid,
 source_version int,
 workstream_revision bigint,
 workstream_state text NOT NULL DEFAULT '',
 state text NOT NULL CHECK (state IN ('coalesced_pending', 'emitted', 'satisfied', 'skipped')),
 skip_reason text NOT NULL DEFAULT '',
 last_error_code text NOT NULL DEFAULT '',
 notice_event_id uuid REFERENCES cairn.agent_event(event_id),
 notice_record_id uuid REFERENCES cairn.memory_record(record_id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 emitted_at timestamptz,
 UNIQUE (repo, subscriber_consumer, source_kind, source_key, deadline_at),
 CONSTRAINT agent_overdue_checkpoint_emitted_valid CHECK (
  (state = 'emitted' AND notice_event_id IS NOT NULL AND notice_record_id IS NOT NULL AND emitted_at IS NOT NULL)
  OR (state <> 'emitted' AND notice_event_id IS NULL AND notice_record_id IS NULL AND emitted_at IS NULL)
 )
);

CREATE INDEX agent_overdue_checkpoint_pending_idx
 ON cairn.agent_overdue_checkpoint(repo, subscriber_consumer, sensitivity, deadline_at)
 WHERE state = 'coalesced_pending';

CREATE INDEX agent_overdue_checkpoint_emitted_idx
 ON cairn.agent_overdue_checkpoint(repo, subscriber_consumer, sensitivity, notice_event_id)
 WHERE state = 'emitted';
