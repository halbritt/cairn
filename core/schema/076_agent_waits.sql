-- Durable coordination waits: nonblocking subscriptions on response groups or direct replies.
-- Atomic terminal resolution, lock-serialized database timestamps, dedicated outbox drain,
-- and persistent admission fencing across restore generations.

CREATE TABLE cairn.agent_wait (
    wait_uuid uuid PRIMARY KEY,
    collection text NOT NULL,
    owner_principal text NOT NULL,
    owner_agent_uuid uuid NOT NULL,
    creation_execution_uuid uuid NOT NULL,
    registration_destination jsonb NOT NULL,
    sensitivity_ceiling text NOT NULL,
    profile_binding_id text NOT NULL,
    predicate_version integer NOT NULL DEFAULT 1 CHECK (predicate_version = 1),
    predicate_kind text NOT NULL CHECK (predicate_kind IN ('response_group_closed', 'request_response')),
    dependency_event_uuid uuid NOT NULL REFERENCES cairn.agent_event(event_id),
    deadline timestamptz NOT NULL,
    generation bigint NOT NULL,
    state text NOT NULL DEFAULT 'waiting' CHECK (state IN ('waiting', 'satisfied', 'timed_out', 'cancelled', 'restore_fenced')),
    response_ref jsonb,
    reserved_notice_event_uuid uuid NOT NULL UNIQUE,
    reserved_source_record_uuid uuid NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    resolved_at timestamptz,
    terminal_reason text NOT NULL DEFAULT '',
    CHECK ((state = 'waiting') = (resolved_at IS NULL)),
    CHECK ((state = 'waiting') = (terminal_reason = ''))
);

CREATE UNIQUE INDEX agent_wait_active_unique ON cairn.agent_wait (collection, owner_agent_uuid, predicate_kind, dependency_event_uuid) WHERE state = 'waiting';
CREATE INDEX agent_wait_waiting_deadline ON cairn.agent_wait (deadline, wait_uuid) WHERE state = 'waiting';
CREATE INDEX agent_wait_dependency ON cairn.agent_wait (dependency_event_uuid) WHERE state = 'waiting';
CREATE INDEX agent_wait_collection_owner ON cairn.agent_wait (collection, owner_agent_uuid, created_at);

CREATE TABLE cairn.agent_wait_outbox (
    wait_uuid uuid PRIMARY KEY REFERENCES cairn.agent_wait(wait_uuid) ON DELETE CASCADE,
    notice_event_uuid uuid NOT NULL,
    source_record_uuid uuid NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'published', 'restricted', 'restore_fenced')),
    error_reason text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX agent_wait_outbox_pending ON cairn.agent_wait_outbox (updated_at, wait_uuid) WHERE state = 'pending';

CREATE TABLE cairn.agent_wait_notification_fence (
    event_uuid uuid NOT NULL,
    generation bigint NOT NULL,
    fenced_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (event_uuid, generation)
);

CREATE INDEX agent_wait_notification_fence_event ON cairn.agent_wait_notification_fence (event_uuid);

INSERT INTO cairn.work_feature (feature_name, status)
VALUES ('coordination-waits/1', 'disabled')
ON CONFLICT (feature_name) DO NOTHING;

