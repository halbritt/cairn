ALTER TABLE cairn.lesson_proposal
 ALTER COLUMN recovery_receipt DROP NOT NULL,
 ALTER COLUMN recovery_version DROP NOT NULL,
 ADD CONSTRAINT proposal_recovery_pair CHECK (
  (recovery_receipt IS NULL) = (recovery_version IS NULL)
 );

CREATE UNIQUE INDEX standalone_failure_source ON cairn.lesson_proposal
 (failure_receipt,failure_version) WHERE recovery_receipt IS NULL;
