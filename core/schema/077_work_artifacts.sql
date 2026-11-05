-- Migration 077: Work-Artifact Relationships (WC-02)
-- Enables registering external deliverables with immutable revisions and linking
-- them to work items with input/output/evidence roles, plus reverse impact queries.

CREATE TABLE cairn.work_artifact (
    artifact_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('git', 'report', 'ci', 'cairn_result')),
    canonical_key text NOT NULL,
    display_name text NOT NULL DEFAULT '',
    -- Kind-specific identity fields
    git_repo text,
    report_locator text,
    ci_provider text,
    ci_repo text,
    ci_run_id text,
    cairn_record_id uuid REFERENCES cairn.memory_record(record_id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT work_artifact_collection_canonical_key UNIQUE (collection, canonical_key)
);
CREATE INDEX work_artifact_collection_kind ON cairn.work_artifact(collection, kind);
CREATE INDEX work_artifact_cairn_record ON cairn.work_artifact(cairn_record_id) WHERE cairn_record_id IS NOT NULL;

CREATE TABLE cairn.work_artifact_revision (
    revision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    artifact_id uuid NOT NULL REFERENCES cairn.work_artifact(artifact_id) ON DELETE CASCADE,
    collection text NOT NULL,
    revision_label text NOT NULL,
    -- Git revision fields
    git_commit_oid text,
    git_object_format text CHECK (git_object_format IS NULL OR git_object_format IN ('sha1', 'sha256')),
    git_branch_hint text,
    -- Report revision fields
    report_digest text,
    report_provenance text NOT NULL DEFAULT 'unverified' CHECK (report_provenance IN ('unverified', 'asserted', 'verified')),
    report_mutable boolean NOT NULL DEFAULT true,
    -- CI revision fields
    ci_attempt integer NOT NULL DEFAULT 1,
    ci_status text,
    ci_reported_at timestamptz,
    -- Cairn result revision fields
    cairn_version integer CHECK (cairn_version IS NULL OR cairn_version > 0),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT work_artifact_rev_artifact_label UNIQUE (artifact_id, revision_label)
);
CREATE INDEX work_artifact_rev_artifact ON cairn.work_artifact_revision(artifact_id);
CREATE INDEX work_artifact_rev_collection ON cairn.work_artifact_revision(collection);

CREATE TABLE cairn.work_artifact_link (
    link_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    revision bigint NOT NULL DEFAULT 1,
    collection text NOT NULL,
    binding_id uuid NOT NULL REFERENCES cairn.work_project(binding_id) ON DELETE CASCADE,
    plane_item_uuid uuid NOT NULL,
    artifact_id uuid NOT NULL REFERENCES cairn.work_artifact(artifact_id) ON DELETE CASCADE,
    revision_id uuid NOT NULL REFERENCES cairn.work_artifact_revision(revision_id) ON DELETE RESTRICT,
    role text NOT NULL CHECK (role IN ('input', 'output', 'evidence')),
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'removed')),
    rationale text,
    author text NOT NULL,
    asserted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    removed_at timestamptz,
    removed_by text,
    CONSTRAINT work_artifact_link_item_rev_role UNIQUE (binding_id, plane_item_uuid, revision_id, role)
);
CREATE INDEX work_artifact_link_item ON cairn.work_artifact_link(binding_id, plane_item_uuid);
CREATE INDEX work_artifact_link_revision ON cairn.work_artifact_link(revision_id, state);
CREATE INDEX work_artifact_link_collection ON cairn.work_artifact_link(collection, asserted_at);

CREATE TABLE cairn.work_artifact_link_history (
    history_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    link_id uuid NOT NULL REFERENCES cairn.work_artifact_link(link_id) ON DELETE CASCADE,
    revision bigint NOT NULL,
    state text NOT NULL,
    role text NOT NULL,
    rationale text,
    author text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT work_artifact_link_hist_link_rev UNIQUE (link_id, revision)
);
CREATE INDEX work_artifact_link_hist_link ON cairn.work_artifact_link_history(link_id, revision);

CREATE TABLE cairn.work_artifact_purge_fence (
    collection text NOT NULL,
    artifact_id uuid NOT NULL,
    canonical_key text NOT NULL,
    purged_by text NOT NULL,
    purged_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (collection, artifact_id)
);
CREATE INDEX work_artifact_purge_fence_key ON cairn.work_artifact_purge_fence(collection, canonical_key);

INSERT INTO cairn.work_feature (feature_name, status) VALUES ('work-artifacts/1', 'disabled')
ON CONFLICT (feature_name) DO NOTHING;
