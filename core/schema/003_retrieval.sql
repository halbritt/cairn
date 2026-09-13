CREATE TABLE cairn.conflict_group (
    conflict_id uuid PRIMARY KEY,
    repo text NOT NULL,
    opened_by text NOT NULL DEFAULT current_setting('cairn.caller'),
    reason text NOT NULL,
    version integer NOT NULL DEFAULT 1,
    opened_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    resolved_event uuid REFERENCES cairn.authority_event(event_id)
);
CREATE TABLE cairn.conflict_member (
    conflict_id uuid NOT NULL REFERENCES cairn.conflict_group(conflict_id),
    record_id uuid NOT NULL,
    version integer NOT NULL,
    PRIMARY KEY(conflict_id,record_id,version),
    FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version)
);
CREATE INDEX conflict_record ON cairn.conflict_member(record_id,version);

CREATE TABLE cairn.retrieval_receipt (
    receipt_id uuid PRIMARY KEY,
    caller text NOT NULL DEFAULT current_setting('cairn.caller'),
    request_id uuid NOT NULL,
    request_digest bytea NOT NULL,
    scope jsonb NOT NULL,
    purpose text NOT NULL,
    destination text NOT NULL,
    semantic_body bytea NOT NULL,
    seal text NOT NULL,
    status text NOT NULL,
    nonce uuid NOT NULL,
	launch_claimed boolean NOT NULL DEFAULT false,
    snapshot text NOT NULL DEFAULT pg_current_snapshot()::text,
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    UNIQUE(caller,request_id)
);
CREATE TABLE cairn.record_use (
    receipt_id uuid NOT NULL REFERENCES cairn.retrieval_receipt(receipt_id),
    record_id uuid NOT NULL,
    version integer NOT NULL,
    purpose text NOT NULL,
    used_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY(receipt_id,record_id,version),
    FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version)
);
CREATE INDEX use_reverse ON cairn.record_use(record_id,version,used_at);
CREATE TABLE cairn.delivery_receipt (
    delivery_id uuid PRIMARY KEY,
    receipt_id uuid NOT NULL REFERENCES cairn.retrieval_receipt(receipt_id),
    observer text NOT NULL DEFAULT current_setting('cairn.caller'),
    adapter text NOT NULL,
    carrier text NOT NULL CHECK (carrier IN ('stdin','argv','file')),
    assurance text NOT NULL CHECK (assurance IN ('available','delivered','failed','unknown')),
    rendered_sha256 text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    blind_spots text NOT NULL
);
CREATE TABLE cairn.run_outcome (
    outcome_id uuid PRIMARY KEY,
    receipt_id uuid NOT NULL REFERENCES cairn.retrieval_receipt(receipt_id),
    observer text NOT NULL DEFAULT current_setting('cairn.caller'),
    exit_code integer,
    duration_ms bigint NOT NULL CHECK (duration_ms>=0),
    process_state text NOT NULL CHECK (process_state IN ('exited','launch_failed','timeout','cancelled','unknown')),
    task_outcome text NOT NULL CHECK (task_outcome IN ('unknown','not_attempted')),
    stdout_sha256 text NOT NULL,
    stderr_sha256 text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    UNIQUE(receipt_id)
);
CREATE TABLE cairn.usage_observation (
    observation_id uuid PRIMARY KEY,
    receipt_id uuid NOT NULL REFERENCES cairn.retrieval_receipt(receipt_id),
    record_id uuid NOT NULL,
    version integer NOT NULL,
    signal text NOT NULL CHECK (signal IN ('cited','expanded')),
    observer text NOT NULL DEFAULT current_setting('cairn.caller'),
    observed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    FOREIGN KEY(receipt_id,record_id,version) REFERENCES cairn.record_use(receipt_id,record_id,version)
);
