ALTER TABLE cairn.evidence ADD COLUMN check_generation integer NOT NULL DEFAULT 0;
ALTER TABLE cairn.evidence ADD COLUMN checked_at timestamptz;
CREATE TABLE cairn.evidence_check (
 evidence_id uuid NOT NULL REFERENCES cairn.evidence(evidence_id),
 generation integer NOT NULL,
 detail jsonb NOT NULL,
 PRIMARY KEY(evidence_id,generation)
);
