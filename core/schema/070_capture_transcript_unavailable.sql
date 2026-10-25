ALTER TABLE cairn.capture_observation
 DROP CONSTRAINT capture_observation_outcome_check;

ALTER TABLE cairn.capture_observation
 ADD CONSTRAINT capture_observation_outcome_check
 CHECK (outcome IN ('empty','unchanged','courtesy','too_little_dialogue','transcript_unavailable',
                    'saved','nothing_selected','timeout','error','write_error','state_commit_error'));
