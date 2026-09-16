-- Trusted host observations bind one queued wake to one native turn. This does
-- not advertise a stop capability or permit cancellation before cleanup exists.
ALTER TABLE cairn.agent_session_attempt
 ADD COLUMN native_turn_id text NOT NULL DEFAULT '' CHECK (octet_length(native_turn_id)<=256);
ALTER TABLE cairn.agent_session_poll
 ADD COLUMN requested_delivery_id uuid,
 ADD COLUMN native_turn_id text NOT NULL DEFAULT '' CHECK (octet_length(native_turn_id)<=256),
 ADD CONSTRAINT native_poll_turn_binding CHECK ((requested_delivery_id IS NULL)=(native_turn_id=''));
