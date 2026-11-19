-- Hosted campaign ledger and operation idempotency journal (CAIRN-468):
-- typed campaign state (assignments, decisions, blockers, holds), host-journaled
-- idempotency for publish/send/complete/ack, and ledger-derived session summaries.

UPDATE cairn.restore_admission SET paused=paused WHERE singleton;

CREATE TABLE cairn.campaign_ledger_item (
    repo text NOT NULL,
    campaign text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('assignment', 'decision', 'blocker', 'hold')),
    item_key text NOT NULL CHECK (item_key ~ '^[A-Za-z0-9_.:/-]{1,128}$'),
    owner text NOT NULL CHECK (octet_length(btrim(owner)) BETWEEN 1 AND 256 AND owner !~ '[\x00-\x20\x7f]'),
    status text NOT NULL CHECK (
        (kind = 'assignment' AND status IN ('planned', 'dispatched', 'in_progress', 'review', 'completed', 'cancelled')) OR
        (kind = 'decision' AND status IN ('open', 'settled', 'superseded')) OR
        (kind = 'blocker' AND status IN ('open', 'escalated', 'resolved')) OR
        (kind = 'hold' AND status IN ('active', 'released'))
    ),
    summary text NOT NULL CHECK (octet_length(btrim(summary)) BETWEEN 1 AND 280 AND summary !~ '[\x00-\x1f\x7f]'),
    detail text NOT NULL DEFAULT '' CHECK (octet_length(detail) <= 2048),
    source_record_id uuid,
    source_version integer CHECK ((source_record_id IS NULL) = (source_version IS NULL) AND (source_version IS NULL OR source_version >= 1)),
    event_id uuid,
    delivery_id uuid,
    result_record_id uuid,
    result_version integer CHECK ((result_record_id IS NULL) = (result_version IS NULL) AND (result_version IS NULL OR result_version >= 1)),
    due_at timestamptz,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_by text NOT NULL CHECK (octet_length(btrim(updated_by)) BETWEEN 1 AND 256),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (repo, campaign, kind, item_key),
    FOREIGN KEY (repo, campaign) REFERENCES cairn.agent_team_campaign(repo, campaign) ON DELETE CASCADE
);

CREATE INDEX campaign_ledger_item_owner_idx
    ON cairn.campaign_ledger_item(repo, campaign, owner);

CREATE TABLE cairn.campaign_operation_journal (
    repo text NOT NULL,
    campaign text NOT NULL,
    caller text NOT NULL CHECK (octet_length(btrim(caller)) BETWEEN 1 AND 256 AND caller !~ '[\x00-\x20\x7f]'),
    op_key text NOT NULL CHECK (op_key ~ '^[A-Za-z0-9_.:/-]{1,128}$'),
    operation text NOT NULL CHECK (operation IN ('publish', 'send', 'complete', 'ack')),
    sensitivity text NOT NULL DEFAULT 'shareable' CHECK (sensitivity IN ('local', 'shareable')),
    intent_sha256 text NOT NULL CHECK (intent_sha256 ~ '^[0-9a-f]{64}$'),
    source_request_id uuid,
    action_request_id uuid NOT NULL,
    reply_request_id uuid,
    lease_id uuid,
    source_record_id uuid,
    source_version integer CHECK ((source_record_id IS NULL) = (source_version IS NULL) AND (source_version IS NULL OR source_version >= 1)),
    delivery_id uuid,
    event_id uuid,
    reply_event_id uuid,
    state text NOT NULL CHECK (state IN ('reserved', 'source_saved', 'completed_reply_pending', 'committed')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (repo, campaign, caller, op_key),
    FOREIGN KEY (repo, campaign) REFERENCES cairn.agent_team_campaign(repo, campaign) ON DELETE CASCADE
);

CREATE INDEX campaign_operation_journal_updated_idx
    ON cairn.campaign_operation_journal(repo, campaign, updated_at DESC, op_key);
