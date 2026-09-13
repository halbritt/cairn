ALTER TABLE cairn.authority_event DROP CONSTRAINT authority_event_event_type_check;
ALTER TABLE cairn.authority_event ADD CHECK (event_type IN ('bootstrap','grant','revoke_grant','promote','issue','correct','supersede','authorize_scope','policy_revise','retract','dispute','resolve','redact','forget'));

-- The shared lock orders fresh compilation/delivery against policy revisions,
-- including a repository's first revision when no per-repository row exists.
CREATE TABLE cairn.policy_generation (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 generation bigint NOT NULL DEFAULT 0 CHECK(generation>=0)
);
INSERT INTO cairn.policy_generation(singleton) VALUES(true);

CREATE TABLE cairn.policy_revision (
 revision_id uuid PRIMARY KEY,
 repo text NOT NULL CHECK(length(repo) BETWEEN 1 AND 512 AND repo <> '*'),
 version integer NOT NULL CHECK(version>0),
 engine text NOT NULL DEFAULT 'local-loop/2' CHECK(engine='local-loop/2'),
 previous_revision_id uuid REFERENCES cairn.policy_revision(revision_id),
 restores_revision_id uuid REFERENCES cairn.policy_revision(revision_id),
 rules jsonb NOT NULL CHECK(jsonb_typeof(rules)='object'),
 grant_id uuid NOT NULL REFERENCES cairn.authority_grant(grant_id),
 event_id uuid NOT NULL UNIQUE REFERENCES cairn.authority_event(event_id),
 UNIQUE(repo,version),
 CHECK((version=1)=(previous_revision_id IS NULL))
);
CREATE TRIGGER immutable_policy_revision BEFORE UPDATE OR DELETE ON cairn.policy_revision
 FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();
CREATE FUNCTION cairn.match_policy_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM cairn.authority_event e WHERE e.event_id=NEW.event_id
 AND e.event_type='policy_revise' AND e.subject_id=NEW.revision_id
 AND e.previous_version=NEW.version-1 AND e.resulting_version=NEW.version
 AND e.transaction_id=pg_current_xact_id())
 THEN RAISE EXCEPTION 'policy revision requires matching atomic authority event'; END IF;
 IF NEW.previous_revision_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM cairn.policy_revision p
 WHERE p.revision_id=NEW.previous_revision_id AND p.repo=NEW.repo AND p.version=NEW.version-1)
 THEN RAISE EXCEPTION 'previous policy revision must belong to the same repository'; END IF;
 IF NEW.restores_revision_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM cairn.policy_revision p
 WHERE p.revision_id=NEW.restores_revision_id AND p.repo=NEW.repo AND p.version<NEW.version AND p.rules=NEW.rules)
 THEN RAISE EXCEPTION 'policy rollback must restore an earlier revision from the same repository'; END IF;
 RETURN NEW;
END;
$$;
CREATE CONSTRAINT TRIGGER match_policy_revision AFTER INSERT ON cairn.policy_revision
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cairn.match_policy_revision();

CREATE TABLE cairn.retrieval_policy (
 receipt_id uuid PRIMARY KEY REFERENCES cairn.retrieval_receipt(receipt_id),
 revision_id uuid NOT NULL REFERENCES cairn.policy_revision(revision_id)
);
CREATE INDEX retrieval_policy_revision ON cairn.retrieval_policy(revision_id,receipt_id);
