-- Migration 078: Quiet next-user-turn notices
-- Feature next-turn-notices/1

INSERT INTO cairn.work_feature (feature_name, status)
VALUES ('next-turn-notices/1', 'disabled')
ON CONFLICT (feature_name) DO NOTHING;

CREATE TABLE cairn.agent_native_owner_launch (
    launch_id uuid PRIMARY KEY,
    repo text NOT NULL,
    profile_principal text NOT NULL,
    binding text NOT NULL,
    public_key bytea NOT NULL,
    attestation_contract text NOT NULL DEFAULT 'cairn.owner-input-attestation/1',
    origin_contract text NOT NULL,
    origin_version int NOT NULL,
    upstream_commit text NOT NULL,
    patch_sha256 text NOT NULL,
    runtime_sha256 text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked', 'retired')),
    restore_generation bigint NOT NULL,
    registered_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX agent_native_owner_launch_lookup ON cairn.agent_native_owner_launch(repo, profile_principal, binding, status);

CREATE TABLE cairn.agent_native_owner_session_binding (
    launch_id uuid NOT NULL REFERENCES cairn.agent_native_owner_launch(launch_id) ON DELETE CASCADE,
    native_session_id text NOT NULL,
    repo text NOT NULL,
    agent_id uuid NOT NULL REFERENCES cairn.agent_session(agent_id) ON DELETE CASCADE,
    execution_id uuid NOT NULL,
    restore_generation bigint NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'active')),
    reserved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    activated_at timestamptz,
    PRIMARY KEY (launch_id, native_session_id)
);

CREATE INDEX agent_native_owner_session_lookup ON cairn.agent_native_owner_session_binding(repo, agent_id, execution_id, state);

CREATE TABLE cairn.agent_organic_notice (
    notice_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo text NOT NULL,
    event_id uuid NOT NULL REFERENCES cairn.agent_event(event_id) ON DELETE CASCADE,
    recipient_agent_id uuid NOT NULL REFERENCES cairn.agent_session(agent_id) ON DELETE CASCADE,
    recipient_inbox text NOT NULL,
    publisher_principal text NOT NULL,
    reserved_delivery_id uuid NOT NULL UNIQUE,
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'admitted', 'flushed', 'expired', 'unavailable', 'restore_fenced')),
    restore_generation bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    admitted_at timestamptz,
    admission_turn_root_id uuid,
    unavailable_reason text
);

CREATE INDEX agent_organic_notice_recipient ON cairn.agent_organic_notice(repo, recipient_agent_id, state, expires_at, created_at);
CREATE INDEX agent_organic_notice_event ON cairn.agent_organic_notice(repo, event_id);
CREATE INDEX agent_organic_notice_expiry ON cairn.agent_organic_notice(repo, state, expires_at);

CREATE TABLE cairn.agent_organic_notice_turn_root (
    root_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo text NOT NULL,
    agent_id uuid NOT NULL REFERENCES cairn.agent_session(agent_id) ON DELETE CASCADE,
    execution_id uuid NOT NULL,
    native_owner_turn_id text NOT NULL,
    owner_input_id uuid,
    origin_contract_version int NOT NULL DEFAULT 1,
    decision text NOT NULL CHECK (decision IN ('admitted', 'none', 'exhausted')),
    cutoff timestamptz NOT NULL,
    admitted_count int NOT NULL DEFAULT 0,
    admitted_limit int NOT NULL DEFAULT 3,
    restore_generation bigint NOT NULL,
    issued_at_unix_ms bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(repo, agent_id, execution_id, restore_generation, native_owner_turn_id)
);

CREATE UNIQUE INDEX agent_organic_turn_root_input ON cairn.agent_organic_notice_turn_root(repo, agent_id, execution_id, restore_generation, owner_input_id)
    WHERE owner_input_id IS NOT NULL;

ALTER TABLE cairn.agent_event
    ADD COLUMN IF NOT EXISTS notice_delivery text CHECK (notice_delivery IS NULL OR notice_delivery = 'next_organic_turn'),
    ADD COLUMN IF NOT EXISTS notice_expires_at timestamptz;

ALTER TABLE cairn.agent_session_attempt
    ADD COLUMN IF NOT EXISTS admission_kind text NOT NULL DEFAULT 'ordinary' CHECK (admission_kind IN ('ordinary', 'organic')),
    ADD COLUMN IF NOT EXISTS organic_root uuid REFERENCES cairn.agent_organic_notice_turn_root(root_id) ON DELETE SET NULL;
