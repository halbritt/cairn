-- One native turn may handle up to three notices by atomically transferring
-- its one active session hold from a settled attempt to the next delivery.
ALTER TABLE cairn.agent_session_attempt
 ADD COLUMN batch_root uuid REFERENCES cairn.agent_session_attempt(attempt_id),
 ADD COLUMN batch_index smallint NOT NULL DEFAULT 0,
 ADD COLUMN batch_limit smallint NOT NULL DEFAULT 0,
 ADD COLUMN batch_closed boolean NOT NULL DEFAULT false,
 ADD CONSTRAINT native_notice_batch_shape CHECK (
   (batch_root IS NULL AND batch_index=0 AND batch_limit=0) OR
   (batch_root IS NOT NULL AND batch_index BETWEEN 1 AND 3 AND batch_limit=3));
CREATE UNIQUE INDEX agent_session_batch_position ON cairn.agent_session_attempt(batch_root,batch_index)
 WHERE batch_root IS NOT NULL;
