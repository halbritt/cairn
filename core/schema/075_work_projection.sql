-- Work projection schema: shared work view with external Plane project identity,
-- read-only item caching, explicit associations with typed target identity,
-- progress/blocker annotations with bounded revision history, and metadata exclusions.

CREATE TABLE cairn.work_project (
    binding_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    local_slug text NOT NULL,
    collection text NOT NULL,
    plane_origin text NOT NULL,
    workspace_uuid uuid NOT NULL,
    project_uuid uuid NOT NULL,
    config_revision text NOT NULL,
    plane_instance text NOT NULL,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'retired')),
    metadata_exclusion_revision bigint NOT NULL DEFAULT 0,
    refresh_sequence bigint NOT NULL DEFAULT 0,
    auth_rejected boolean NOT NULL DEFAULT false,
    last_auth_error text,
    last_successful_refresh_at timestamptz,
    refresh_inflight_token uuid,
    refresh_inflight_expires_at timestamptz,
    next_refresh_allowed_at timestamptz,
    last_refresh_error text,
    last_refresh_error_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT work_project_collection_origin_workspace_project UNIQUE (collection, plane_origin, workspace_uuid, project_uuid),
    CONSTRAINT work_project_collection_local_slug UNIQUE (collection, local_slug)
);

CREATE TABLE cairn.work_snapshot (
    snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    binding_id uuid NOT NULL REFERENCES cairn.work_project(binding_id) ON DELETE CASCADE,
    cache_generation bigint NOT NULL,
    item_count integer NOT NULL,
    snapshot_digest text NOT NULL,
    staged_at timestamptz NOT NULL,
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX work_snapshot_binding_generation ON cairn.work_snapshot(binding_id, cache_generation);

CREATE TABLE cairn.work_item (
    item_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    binding_id uuid NOT NULL REFERENCES cairn.work_project(binding_id) ON DELETE CASCADE,
    plane_item_uuid uuid NOT NULL,
    identifier text NOT NULL,
    title text NOT NULL,
    state_id uuid,
    state_name text,
    state_group text,
    assignee_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    assignee_names jsonb NOT NULL DEFAULT '[]'::jsonb,
    updated_at_source timestamptz,
    source_url text,
    cache_generation bigint NOT NULL DEFAULT 0,
    is_absent boolean NOT NULL DEFAULT false,
    last_refreshed_at timestamptz,
    refresh_error text,
    CONSTRAINT work_item_binding_plane_item UNIQUE (binding_id, plane_item_uuid)
);
CREATE INDEX work_item_binding_state ON cairn.work_item(binding_id, state_group);
CREATE INDEX work_item_binding_identifier ON cairn.work_item(binding_id, identifier);

CREATE TABLE cairn.work_association (
    association_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    revision bigint NOT NULL DEFAULT 1,
    binding_id uuid NOT NULL REFERENCES cairn.work_project(binding_id) ON DELETE CASCADE,
    plane_item_uuid uuid NOT NULL,
    target_type text NOT NULL CHECK (target_type IN ('session', 'event', 'response_group', 'run_receipt', 'result_record')),
    target_id text NOT NULL,
    target_agent_id uuid,
    target_execution_id uuid,
    target_event_id uuid,
    target_record_id uuid,
    target_version integer,
    target_group_id uuid,
    target_run_id text,
    rationale text,
    task_summary text,
    author text NOT NULL,
    state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'removed')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    removed_at timestamptz,
    removed_by text
);
CREATE INDEX work_assoc_binding_item ON cairn.work_association(binding_id, plane_item_uuid);
CREATE INDEX work_assoc_session ON cairn.work_association(target_agent_id, target_execution_id) WHERE target_agent_id IS NOT NULL;
CREATE INDEX work_assoc_event ON cairn.work_association(target_event_id) WHERE target_event_id IS NOT NULL;
CREATE INDEX work_assoc_record ON cairn.work_association(target_record_id, target_version) WHERE target_record_id IS NOT NULL;

CREATE TABLE cairn.work_association_history (
    history_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    association_id uuid NOT NULL REFERENCES cairn.work_association(association_id) ON DELETE CASCADE,
    revision bigint NOT NULL,
    state text NOT NULL,
    rationale text,
    task_summary text,
    author text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX work_assoc_hist_assoc ON cairn.work_association_history(association_id, revision);

CREATE TABLE cairn.work_annotation (
    annotation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    revision bigint NOT NULL DEFAULT 1,
    binding_id uuid NOT NULL REFERENCES cairn.work_project(binding_id) ON DELETE CASCADE,
    plane_item_uuid uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('progress', 'blocker')),
    body text NOT NULL,
    state text NOT NULL CHECK (state IN ('active', 'superseded', 'open', 'resolved')),
    author text NOT NULL,
    source_references jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX work_annot_binding_item ON cairn.work_annotation(binding_id, plane_item_uuid);

CREATE TABLE cairn.work_annotation_history (
    history_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    annotation_id uuid NOT NULL REFERENCES cairn.work_annotation(annotation_id) ON DELETE CASCADE,
    revision bigint NOT NULL,
    body text NOT NULL,
    state text NOT NULL,
    author text NOT NULL,
    source_references jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX work_annot_hist_annot ON cairn.work_annotation_history(annotation_id, revision);

CREATE TABLE cairn.work_metadata_exclusion (
    exclusion_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    binding_id uuid NOT NULL REFERENCES cairn.work_project(binding_id) ON DELETE CASCADE,
    field_name text NOT NULL CHECK (field_name IN ('title', 'annotation', 'all')),
    pattern text NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX work_excl_binding ON cairn.work_metadata_exclusion(binding_id);

CREATE UNIQUE INDEX work_assoc_unique_target ON cairn.work_association(binding_id, plane_item_uuid, target_type, target_id);

CREATE TABLE cairn.work_metadata_purge (
    purge_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    binding_id uuid NOT NULL REFERENCES cairn.work_project(binding_id) ON DELETE CASCADE,
    field_name text NOT NULL CHECK (field_name IN ('title', 'annotation', 'all')),
    pattern text NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX work_purge_binding ON cairn.work_metadata_purge(binding_id);

CREATE TABLE cairn.work_feature (
    feature_name text PRIMARY KEY,
    status text NOT NULL CHECK (status IN ('active', 'disabled', 'retired')),
    retired_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

INSERT INTO cairn.work_feature (feature_name, status) VALUES ('work-view/1', 'active')
ON CONFLICT (feature_name) DO NOTHING;

