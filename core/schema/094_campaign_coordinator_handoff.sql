-- Campaign coordinator role addressing and ledger-driven session handoff (CAIRN-471):
-- coordinator age & compaction thresholds, stable campaign role inbox bindings
-- (role/<campaign>/<role>), and atomic coordinator handoff lineage.

UPDATE cairn.restore_admission SET paused=paused WHERE singleton;

ALTER TABLE cairn.agent_session
    ADD COLUMN started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    ADD COLUMN compaction_count integer NOT NULL DEFAULT 0 CHECK (compaction_count >= 0);

ALTER TABLE cairn.agent_team_campaign
    ADD COLUMN max_coordinator_age_seconds integer CHECK (
        max_coordinator_age_seconds IS NULL OR max_coordinator_age_seconds BETWEEN 60 AND 604800
    ),
    ADD COLUMN max_coordinator_compactions integer CHECK (
        max_coordinator_compactions IS NULL OR max_coordinator_compactions BETWEEN 1 AND 1000
    );

CREATE TABLE cairn.campaign_role_binding (
    repo text NOT NULL,
    campaign text NOT NULL,
    role_name text NOT NULL CHECK (role_name ~ '^[A-Za-z0-9_.-]{1,64}$'),
    role_address text NOT NULL CHECK (octet_length(btrim(role_address)) BETWEEN 6 AND 256 AND role_address !~ '[\x00-\x20\x7f]'),
    holder_principal text NOT NULL CHECK (octet_length(btrim(holder_principal)) BETWEEN 1 AND 256 AND holder_principal !~ '[\x00-\x20\x7f]'),
    handoff_record_id uuid,
    handoff_version integer CHECK ((handoff_record_id IS NULL) = (handoff_version IS NULL) AND (handoff_version IS NULL OR handoff_version >= 1)),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (repo, campaign, role_name),
    UNIQUE (repo, role_address),
    FOREIGN KEY (repo, campaign) REFERENCES cairn.agent_team_campaign(repo, campaign) ON DELETE CASCADE
);

INSERT INTO cairn.campaign_role_binding(repo, campaign, role_name, role_address, holder_principal)
SELECT repo, campaign, 'root', 'role/' || campaign || '/root', root_agent
FROM cairn.agent_team_campaign
ON CONFLICT (repo, campaign, role_name) DO NOTHING;

CREATE TABLE cairn.campaign_coordinator_handoff (
    handoff_id uuid PRIMARY KEY,
    request_id uuid NOT NULL,
    repo text NOT NULL,
    campaign text NOT NULL,
    role_name text NOT NULL,
    role_address text NOT NULL,
    from_principal text NOT NULL,
    from_agent_id uuid,
    from_execution_id uuid,
    to_principal text NOT NULL,
    to_agent_id uuid NOT NULL,
    to_execution_id uuid NOT NULL,
    handoff_record_id uuid NOT NULL,
    handoff_version integer NOT NULL CHECK (handoff_version >= 1),
    reason text NOT NULL CHECK (octet_length(btrim(reason)) BETWEEN 1 AND 280),
    transferred_deliveries integer NOT NULL DEFAULT 0 CHECK (transferred_deliveries >= 0),
    transferred_subscriptions integer NOT NULL DEFAULT 0 CHECK (transferred_subscriptions >= 0),
    database_generation bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (repo, campaign) REFERENCES cairn.agent_team_campaign(repo, campaign) ON DELETE CASCADE
);

CREATE INDEX campaign_coordinator_handoff_from_idx
    ON cairn.campaign_coordinator_handoff(repo, from_principal, created_at DESC);

CREATE INDEX campaign_coordinator_handoff_to_idx
    ON cairn.campaign_coordinator_handoff(to_agent_id, to_execution_id, database_generation);
