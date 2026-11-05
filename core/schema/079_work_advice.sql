-- Work advice schema: model-assisted coordination jobs, proposals,
-- actions, application receipts, and feature control.
-- Dependencies: 075_work_projection.sql, 077_work_artifacts.sql.

INSERT INTO cairn.work_feature (feature_name, status)
VALUES ('work-advice/1', 'disabled')
ON CONFLICT (feature_name) DO NOTHING;

CREATE TABLE cairn.work_advice_policy (
    binding_id uuid PRIMARY KEY REFERENCES cairn.work_project(binding_id) ON DELETE CASCADE,
    allowed_fields jsonb NOT NULL DEFAULT '[]'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE cairn.work_advice_job (
    job_uuid uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    binding_id uuid NOT NULL REFERENCES cairn.work_project(binding_id) ON DELETE CASCADE,
    plane_item_uuid uuid NOT NULL,
    caller text NOT NULL DEFAULT '',
    profile_name text NOT NULL,
    model_route text NOT NULL,
    template_version integer NOT NULL DEFAULT 1,
    request_uuid uuid NOT NULL,
    input_manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    input_manifest_digest text NOT NULL,
    input_payload_digest text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'timed_out', 'expired', 'cleanup_unconfirmed', 'uncertain')),
    deadline_seconds integer NOT NULL DEFAULT 90,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    error_code text,
    process_identity jsonb NOT NULL DEFAULT '{}'::jsonb,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT work_advice_job_caller_request_unique UNIQUE (caller, request_uuid)
);

CREATE INDEX work_advice_job_binding_item ON cairn.work_advice_job(binding_id, plane_item_uuid);
CREATE INDEX work_advice_job_request ON cairn.work_advice_job(request_uuid);
CREATE INDEX work_advice_job_status_expires ON cairn.work_advice_job(status, expires_at);
CREATE INDEX work_advice_job_inflight ON cairn.work_advice_job(binding_id, plane_item_uuid) WHERE status IN ('pending', 'running');
CREATE UNIQUE INDEX work_advice_job_active_per_item ON cairn.work_advice_job(binding_id, plane_item_uuid) WHERE status IN ('pending', 'running', 'cleanup_unconfirmed', 'uncertain');

CREATE TABLE cairn.work_advice_proposal (
    proposal_uuid uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_uuid uuid NOT NULL REFERENCES cairn.work_advice_job(job_uuid) ON DELETE CASCADE,
    binding_id uuid NOT NULL REFERENCES cairn.work_project(binding_id) ON DELETE CASCADE,
    plane_item_uuid uuid NOT NULL,
    review_version bigint NOT NULL DEFAULT 1,
    status text NOT NULL CHECK (status IN ('pending', 'partially_applied', 'applied', 'rejected', 'expired')),
    raw_result text NOT NULL DEFAULT '',
    normalized_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    reviewed_at timestamptz
);

CREATE INDEX work_advice_proposal_job ON cairn.work_advice_proposal(job_uuid);
CREATE INDEX work_advice_proposal_binding_item ON cairn.work_advice_proposal(binding_id, plane_item_uuid);
CREATE INDEX work_advice_proposal_status_expires ON cairn.work_advice_proposal(status, expires_at);

CREATE TABLE cairn.work_advice_action (
    action_uuid uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_uuid uuid NOT NULL REFERENCES cairn.work_advice_proposal(proposal_uuid) ON DELETE CASCADE,
    action_index integer NOT NULL,
    action_type text NOT NULL CHECK (action_type IN ('annotate_progress', 'annotate_blocker', 'link_artifact')),
    target_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    rationale text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'rejected', 'undone')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX work_advice_action_proposal ON cairn.work_advice_action(proposal_uuid, action_index);

CREATE TABLE cairn.work_advice_application_receipt (
    receipt_uuid uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_uuid uuid NOT NULL UNIQUE,
    proposal_uuid uuid NOT NULL REFERENCES cairn.work_advice_proposal(proposal_uuid) ON DELETE CASCADE,
    applied_action_uuids jsonb NOT NULL DEFAULT '[]'::jsonb,
    applied_by_channel text NOT NULL,
    output_revisions jsonb NOT NULL DEFAULT '{}'::jsonb,
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    undone boolean NOT NULL DEFAULT false,
    undone_at timestamptz,
    undone_by_channel text,
    compensating_revisions jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX work_advice_receipt_proposal ON cairn.work_advice_application_receipt(proposal_uuid);
