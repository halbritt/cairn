CREATE TABLE cairn.run_binding (
 receipt_id uuid PRIMARY KEY REFERENCES cairn.retrieval_receipt(receipt_id),
 task_class text NOT NULL,
 binding_id text NOT NULL,
 capability_id text NOT NULL,
 command_sha256 text NOT NULL CHECK(command_sha256 ~ '^[0-9a-f]{64}$'),
 revision text NOT NULL,
 workspace_sha256 text NOT NULL,
 observer text NOT NULL DEFAULT current_setting('cairn.caller'),
 observed_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
CREATE TABLE cairn.run_assessment (
 receipt_id uuid NOT NULL REFERENCES cairn.retrieval_receipt(receipt_id),
 version integer NOT NULL CHECK(version>0),
 task_outcome text NOT NULL CHECK(task_outcome IN ('accepted','rejected','not_attempted','unknown')),
 failure_domain text NOT NULL CHECK(failure_domain IN ('none','binding','capability','task','unknown')),
 failure_kind text NOT NULL,
 witness text NOT NULL DEFAULT current_setting('cairn.witness') CHECK(witness IN ('testimony','instrumented')),
 detail jsonb NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(receipt_id,version),
 CHECK(failure_domain<>'binding' OR task_outcome IN ('not_attempted','unknown')),
 CHECK(failure_domain<>'binding' OR failure_kind IN ('quota','credentials','transport','adapter','unknown')),
 CHECK(task_outcome<>'accepted' OR failure_domain='none'),
 CHECK(failure_domain NOT IN ('task','capability') OR task_outcome='rejected')
);
CREATE TABLE cairn.assessment_evidence (
 receipt_id uuid NOT NULL,
 version integer NOT NULL,
 evidence_id uuid NOT NULL REFERENCES cairn.evidence(evidence_id),
 PRIMARY KEY(receipt_id,version,evidence_id),
 FOREIGN KEY(receipt_id,version) REFERENCES cairn.run_assessment(receipt_id,version)
);
