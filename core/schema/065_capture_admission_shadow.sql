-- Shadow-only candidate for a future capture admission decision. This is
-- telemetry, not an instruction to skip selection or change a capture marker.
ALTER TABLE cairn.capture_observation ADD COLUMN admission_candidate text
 CHECK (admission_candidate = 'transport_only');
