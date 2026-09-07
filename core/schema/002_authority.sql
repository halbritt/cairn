ALTER TABLE cairn.memory_record DROP CONSTRAINT memory_record_class_check;
ALTER TABLE cairn.memory_record ADD CHECK (class IN ('A','B','C'));
ALTER TABLE cairn.memory_record DROP CONSTRAINT memory_record_lifecycle_check;
ALTER TABLE cairn.memory_record ADD CHECK (lifecycle IN ('active','retracted','tombstoned'));
ALTER TABLE cairn.memory_record DROP CONSTRAINT memory_record_sensitivity_check;
ALTER TABLE cairn.memory_record ADD CHECK (sensitivity IN ('local','shareable'));
ALTER TABLE cairn.record_version ADD COLUMN version_class text NOT NULL DEFAULT 'A' CHECK (version_class IN ('A','B','C'));
ALTER TABLE cairn.record_version DROP CONSTRAINT record_version_kind_check;
ALTER TABLE cairn.record_version ADD CHECK (kind IN ('note','observation','claim','lesson','procedure','decision','preference','instruction','policy'));

CREATE TABLE cairn.authority_event (
    event_id uuid PRIMARY KEY,
    event_type text NOT NULL CHECK (event_type IN ('bootstrap','grant','revoke_grant','promote','issue','correct','retract','dispute','resolve','redact')),
    subject_id uuid NOT NULL,
    previous_version integer NOT NULL,
    resulting_version integer NOT NULL,
    actor text NOT NULL DEFAULT current_setting('cairn.caller'),
    basis jsonb NOT NULL,
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 8 AND 4000),
    transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
    occurred_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
CREATE FUNCTION cairn.immutable_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'authority audit is append-only'; END;
$$;
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON cairn.authority_event
    FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();

CREATE TABLE cairn.authority_grant (
    grant_id uuid PRIMARY KEY,
    principal text NOT NULL,
    repo text NOT NULL,
    capabilities text[] NOT NULL CHECK (cardinality(capabilities) > 0),
    parent_id uuid REFERENCES cairn.authority_grant(grant_id),
    depth integer NOT NULL CHECK (depth BETWEEN 0 AND 2),
    version integer NOT NULL DEFAULT 1,
    expires_at timestamptz,
    revoked boolean NOT NULL DEFAULT false,
    event_id uuid NOT NULL REFERENCES cairn.authority_event(event_id),
    CHECK ((parent_id IS NULL) = (depth = 0))
);
CREATE UNIQUE INDEX one_root ON cairn.authority_grant((true)) WHERE parent_id IS NULL;
CREATE INDEX grant_principal ON cairn.authority_grant(principal);

CREATE TABLE cairn.evidence (
    evidence_id uuid PRIMARY KEY,
    repo text NOT NULL,
    body bytea NOT NULL CHECK (octet_length(body) BETWEEN 1 AND 1048576),
    digest bytea NOT NULL CHECK (octet_length(digest)=32),
    source text NOT NULL,
    witness text NOT NULL CHECK (witness IN ('testimony','instrumented')),
    captured_by text NOT NULL DEFAULT current_setting('cairn.caller'),
    captured_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    sensitivity text NOT NULL CHECK (sensitivity IN ('local','shareable')),
    state text NOT NULL DEFAULT 'resolvable' CHECK (state IN ('resolvable','divergent','dangling'))
);
CREATE TABLE cairn.evidence_ref (
    record_id uuid NOT NULL,
    version integer NOT NULL,
    evidence_id uuid NOT NULL REFERENCES cairn.evidence(evidence_id),
    PRIMARY KEY (record_id,version,evidence_id),
    FOREIGN KEY (record_id,version) REFERENCES cairn.record_version(record_id,version)
);
CREATE INDEX evidence_reverse ON cairn.evidence_ref(evidence_id);

CREATE TABLE cairn.record_authority (
    record_id uuid NOT NULL,
    version integer NOT NULL,
    event_id uuid NOT NULL REFERENCES cairn.authority_event(event_id),
    grant_id uuid NOT NULL REFERENCES cairn.authority_grant(grant_id),
    mandatory boolean NOT NULL DEFAULT false,
    requires_runtime boolean NOT NULL DEFAULT false,
    policy_key text NOT NULL DEFAULT '',
    PRIMARY KEY(record_id,version),
    FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version)
);
CREATE FUNCTION cairn.match_authority() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cairn.authority_event e
        JOIN cairn.record_version v ON v.record_id=NEW.record_id AND v.version=NEW.version
        WHERE e.event_id=NEW.event_id
        AND e.subject_id=NEW.record_id AND e.resulting_version=NEW.version
        AND e.transaction_id=pg_current_xact_id()
        AND ((e.event_type IN ('promote','correct') AND v.version_class='B')
          OR (e.event_type='issue' AND v.version_class='C')
          OR e.event_type IN ('retract','redact')))
    THEN RAISE EXCEPTION 'authority event does not match record transition'; END IF;
    RETURN NEW;
END;
$$;
CREATE CONSTRAINT TRIGGER match_authority AFTER INSERT ON cairn.record_authority
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cairn.match_authority();
CREATE FUNCTION cairn.require_authority() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.class IN ('B','C') AND NOT EXISTS (
        SELECT 1 FROM cairn.record_authority a WHERE a.record_id=NEW.record_id AND a.version=NEW.current_version)
    THEN RAISE EXCEPTION 'privileged record requires exact authority reference'; END IF;
    IF NEW.class='B' AND NEW.lifecycle='active' AND NOT EXISTS (
        SELECT 1 FROM cairn.evidence_ref e WHERE e.record_id=NEW.record_id AND e.version=NEW.current_version)
    THEN RAISE EXCEPTION 'active B requires evidence links'; END IF;
    RETURN NEW;
END;
$$;
CREATE CONSTRAINT TRIGGER require_authority AFTER INSERT OR UPDATE ON cairn.memory_record
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cairn.require_authority();

CREATE TABLE cairn.record_correction (
    record_id uuid NOT NULL,
    old_version integer NOT NULL,
    new_version integer NOT NULL,
    event_id uuid NOT NULL REFERENCES cairn.authority_event(event_id),
    PRIMARY KEY(record_id,new_version),
    FOREIGN KEY(record_id,old_version) REFERENCES cairn.record_version(record_id,version),
    FOREIGN KEY(record_id,new_version) REFERENCES cairn.record_version(record_id,version),
    CHECK(new_version>old_version)
);
