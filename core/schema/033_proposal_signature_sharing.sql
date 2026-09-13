-- Sharing a note does not publish the private failure-to-lesson association.
-- Old reviews and old writers keep associations local until explicitly reviewed.
ALTER TABLE cairn.proposal_review
 ADD COLUMN signature_shareable boolean NOT NULL DEFAULT false,
 ADD CONSTRAINT proposal_review_signature_sharing CHECK(NOT signature_shareable OR (disposition='converted' AND result_version IS NOT NULL));
