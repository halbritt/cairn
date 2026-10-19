-- High-overlap retrieval candidates omitted or left unexpanded on a
-- UserPromptSubmit hook invocation. Keyed by (repo, prompt_sha256, record_id,
-- version) so repeated hook retries or re-observations of the same prompt
-- digest stay idempotent and never multiply demand docket proposals.
CREATE TABLE cairn.recall_miss_candidate (
 candidate_id uuid PRIMARY KEY,
 repo text NOT NULL CHECK (octet_length(repo) BETWEEN 1 AND 1024),
 caller text NOT NULL DEFAULT current_setting('cairn.caller'),
 observation_id uuid NOT NULL REFERENCES cairn.recall_observation(observation_id),
 receipt_id uuid NOT NULL REFERENCES cairn.retrieval_receipt(receipt_id),
 prompt_sha256 text NOT NULL CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$'),
 record_id uuid NOT NULL,
 version integer NOT NULL,
 lexical_matches integer NOT NULL CHECK (lexical_matches >= 1),
 idf_score bigint NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY (record_id, version) REFERENCES cairn.record_version(record_id, version),
 UNIQUE (repo, prompt_sha256, record_id, version)
);
CREATE INDEX recall_miss_candidate_record ON cairn.recall_miss_candidate(repo, record_id, version);
CREATE INDEX recall_miss_candidate_observation ON cairn.recall_miss_candidate(observation_id);
