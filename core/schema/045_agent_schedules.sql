-- One occurrence per one-shot schedule. UUID is also the eventual event ID.
CREATE TABLE cairn.agent_schedule (
 occurrence_id uuid PRIMARY KEY,
 repo text NOT NULL,
 scheduled_by text NOT NULL DEFAULT current_setting('cairn.caller'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 not_before timestamptz NOT NULL,
 grace_seconds integer NOT NULL CHECK(grace_seconds BETWEEN 1 AND 604800),
 last_checked_at timestamptz,
 publication jsonb NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','fired','skipped','cancelled')),
 code text NOT NULL DEFAULT '',
 finished_at timestamptz,
 event_id uuid UNIQUE REFERENCES cairn.agent_event(event_id),
 CHECK((state='pending')=(finished_at IS NULL)),
 CHECK((state='fired')=(event_id IS NOT NULL)),
 CHECK(event_id IS NULL OR event_id=occurrence_id)
);
CREATE INDEX agent_schedule_due ON cairn.agent_schedule(repo,scheduled_by,not_before,occurrence_id) WHERE state='pending';
