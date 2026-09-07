CREATE SCHEMA cairn;

CREATE TABLE cairn.memory_record (
    record_id uuid PRIMARY KEY,
    current_version integer NOT NULL CHECK (current_version > 0),
    class text NOT NULL DEFAULT 'A' CHECK (class = 'A'),
    lifecycle text NOT NULL DEFAULT 'active' CHECK (lifecycle = 'active'),
    sensitivity text NOT NULL DEFAULT 'local' CHECK (sensitivity = 'local'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    recovered_attempt_id uuid UNIQUE
);

CREATE TABLE cairn.record_version (
    record_id uuid NOT NULL REFERENCES cairn.memory_record(record_id),
    version integer NOT NULL CHECK (version > 0),
    kind text NOT NULL CHECK (kind IN ('note','observation','claim','lesson','procedure','decision','preference')),
    body text NOT NULL CHECK (octet_length(body) BETWEEN 1 AND 65536),
    repo text NOT NULL CHECK (length(repo) BETWEEN 1 AND 512 AND repo <> '*'),
    task_id text NOT NULL CHECK (length(task_id) BETWEEN 1 AND 256),
    run_id text NOT NULL CHECK (length(run_id) BETWEEN 1 AND 256),
    observed_writer text NOT NULL,
    witness text NOT NULL CHECK (witness IN ('testimony','instrumented')),
    written_at timestamptz NOT NULL,
    transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
    attributed_producer text NOT NULL DEFAULT '',
    attempt_id uuid,
    result_ref text NOT NULL DEFAULT '',
    claim_type text NOT NULL CHECK (claim_type IN ('self','completion','partial')),
    PRIMARY KEY (record_id, version)
);

ALTER TABLE cairn.memory_record ADD CONSTRAINT current_version_exists
    FOREIGN KEY (record_id, current_version) REFERENCES cairn.record_version(record_id, version)
    DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION cairn.stamp_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.observed_writer := nullif(current_setting('cairn.caller', true), '');
    NEW.witness := nullif(current_setting('cairn.witness', true), '');
    NEW.written_at := transaction_timestamp();
    NEW.transaction_id := pg_current_xact_id();
    RETURN NEW;
END;
$$;
CREATE TRIGGER stamp_version BEFORE INSERT ON cairn.record_version
    FOR EACH ROW EXECUTE FUNCTION cairn.stamp_version();

CREATE TABLE cairn.delegation_attempt (
    attempt_id uuid PRIMARY KEY,
    observed_by text NOT NULL,
    dispatcher text NOT NULL,
    delegate text NOT NULL,
    repo text NOT NULL,
    task_id text NOT NULL,
    run_id text NOT NULL,
    spawned_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    terminal_state text CHECK (terminal_state IN ('completed','failed','timeout','killed','no_result')),
    terminal_at timestamptz,
    terminal_observer text,
    result_ref text NOT NULL DEFAULT '',
    CHECK ((terminal_state IS NULL) = (terminal_at IS NULL)),
    CHECK (terminal_state IS DISTINCT FROM 'completed' OR result_ref <> '')
);
ALTER TABLE cairn.memory_record ADD FOREIGN KEY (recovered_attempt_id)
    REFERENCES cairn.delegation_attempt(attempt_id);

-- Missing attempts must remain representable: attempt_id on a claim is testimony,
-- so it deliberately has no foreign key to a service-owned spawn.
CREATE VIEW cairn.version_attribution AS
SELECT v.*,
    CASE
      WHEN v.attributed_producer IN ('', v.observed_writer) THEN 'self'
      WHEN a.attempt_id IS NULL THEN 'unreconciled'
      WHEN v.claim_type = 'completion' AND a.terminal_state IN ('failed','timeout','killed','no_result') THEN 'contradicted'
      WHEN v.claim_type = 'completion' AND a.terminal_state = 'completed'
           AND v.result_ref <> '' AND v.result_ref = a.result_ref THEN 'reconciled'
      ELSE 'unreconciled'
    END AS attribution_state
FROM cairn.record_version v
LEFT JOIN cairn.delegation_attempt a ON a.attempt_id = v.attempt_id
    AND a.delegate = v.attributed_producer AND a.dispatcher = v.observed_writer
    AND a.repo = v.repo AND a.task_id = v.task_id AND a.run_id = v.run_id;

CREATE TABLE cairn.correction_docket (
    record_id uuid NOT NULL,
    version integer NOT NULL,
    reason text NOT NULL CHECK (reason = 'ATTRIBUTION_CONTRADICTED'),
    created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (record_id, version, reason),
    FOREIGN KEY (record_id, version) REFERENCES cairn.record_version(record_id, version)
);

CREATE TABLE cairn.mutation_request (
    caller text NOT NULL,
    operation text NOT NULL,
    request_id uuid NOT NULL,
    request_digest bytea NOT NULL,
    response jsonb NOT NULL,
    committed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (caller, operation, request_id)
);
CREATE INDEX version_attempt ON cairn.record_version(attempt_id);
CREATE INDEX version_scope ON cairn.record_version(repo, task_id, run_id);
