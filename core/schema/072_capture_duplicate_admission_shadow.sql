-- Preserve the transport-only shadow classification and add confirmed
-- duplicate snapshots. Neither value instructs the hook to skip capture.
ALTER TABLE cairn.capture_observation
 DROP CONSTRAINT capture_observation_admission_candidate_check;

ALTER TABLE cairn.capture_observation
 ADD CONSTRAINT capture_observation_admission_candidate_check
 CHECK (admission_candidate IN ('transport_only','confirmed_duplicate'));
