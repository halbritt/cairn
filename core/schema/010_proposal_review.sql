CREATE TABLE cairn.lesson_proposal (
 proposal_id uuid PRIMARY KEY,
 repo text NOT NULL,
 failure_receipt uuid NOT NULL,
 failure_version integer NOT NULL,
 recovery_receipt uuid NOT NULL,
 recovery_version integer NOT NULL,
 version integer NOT NULL DEFAULT 1,
 disposition text NOT NULL DEFAULT 'open' CHECK(disposition IN ('open','deferred','dismissed','converted')),
 due_at timestamptz,
 result_record uuid REFERENCES cairn.memory_record(record_id),
 detail jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 FOREIGN KEY(failure_receipt,failure_version) REFERENCES cairn.run_assessment(receipt_id,version),
 FOREIGN KEY(recovery_receipt,recovery_version) REFERENCES cairn.run_assessment(receipt_id,version),
 UNIQUE(failure_receipt,failure_version,recovery_receipt,recovery_version)
);
CREATE TABLE cairn.proposal_review (
 proposal_id uuid NOT NULL REFERENCES cairn.lesson_proposal(proposal_id),
 version integer NOT NULL,
 disposition text NOT NULL,
 reason text NOT NULL,
 observer text NOT NULL DEFAULT current_setting('cairn.caller'),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(proposal_id,version)
);
