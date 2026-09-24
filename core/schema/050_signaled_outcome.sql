-- A worker killed by a signal the runner did not send is recorded as
-- 'signaled' with the signal number, instead of 'exited' with a synthetic -1.
-- Additive: existing rows keep their state and a NULL signal.
ALTER TABLE cairn.run_outcome
    DROP CONSTRAINT run_outcome_process_state_check,
    ADD CONSTRAINT run_outcome_process_state_check
        CHECK (process_state IN ('exited','launch_failed','timeout','cancelled','unknown','signaled')),
    ADD COLUMN signal smallint,
    ADD CONSTRAINT run_outcome_signal_check
        CHECK ((process_state = 'signaled') = (signal IS NOT NULL)
               AND (signal IS NULL OR signal > 0)
               AND (process_state <> 'signaled' OR exit_code IS NULL));
