-- A pool publication can acquire its first delivery long after publication.
-- Watch orders inbox arrival, independently of the original event position.
ALTER TABLE cairn.agent_delivery ADD COLUMN position bigint GENERATED ALWAYS AS IDENTITY;
CREATE UNIQUE INDEX agent_delivery_position ON cairn.agent_delivery(position);
CREATE INDEX agent_delivery_consumer_position ON cairn.agent_delivery(consumer,position);
