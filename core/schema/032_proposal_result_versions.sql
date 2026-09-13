-- Historical reviews retain unknown result versions; never infer them from the
-- current lesson. Old writers also leave new review rows explicitly unpinned.
ALTER TABLE cairn.proposal_review
 ADD COLUMN result_record uuid,
 ADD COLUMN result_version integer CHECK(result_version > 0),
 ADD COLUMN due_at timestamptz,
 ADD CONSTRAINT proposal_review_result_disposition CHECK(result_record IS NULL OR disposition='converted'),
 ADD CONSTRAINT proposal_review_due_disposition CHECK(due_at IS NULL OR disposition='deferred'),
 ADD CONSTRAINT proposal_review_result_pair CHECK((result_record IS NULL) = (result_version IS NULL)),
 ADD CONSTRAINT proposal_review_result_version FOREIGN KEY(result_record,result_version)
 REFERENCES cairn.record_version(record_id,version);
