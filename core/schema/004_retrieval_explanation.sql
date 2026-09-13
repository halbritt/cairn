-- Protected review data, never included in the model-facing semantic package.
ALTER TABLE cairn.retrieval_receipt ADD COLUMN explanation_version integer NOT NULL DEFAULT 0;
CREATE TABLE cairn.retrieval_candidate (
    receipt_id uuid NOT NULL REFERENCES cairn.retrieval_receipt(receipt_id),
    record_id uuid NOT NULL,
    version integer NOT NULL,
    reason text NOT NULL,
    escalation_blocked boolean NOT NULL,
    detail jsonb NOT NULL,
    PRIMARY KEY(receipt_id,record_id,version),
    FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version)
);
CREATE INDEX blocked_demand ON cairn.retrieval_candidate(record_id,version) WHERE escalation_blocked;
