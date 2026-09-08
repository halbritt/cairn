# Extended work-budget calibration — 2026-09-08

Trial I finished normally after 640.966 seconds and still produced no patch.
The held-out cache-lifetime test failed. Giving the hosted binding more time
and steps did not establish a successful repair baseline.

This was a fresh repository-only run of the same historical source, task,
OpenCode binary, DeepSeek V4 Flash binding, tool policy, context and gate as H.
The explicit extended profile raised the process limit from 300 to 900 seconds,
steps from 20 to 60, requests from 24 to 64, and response elapsed limit from
120 to 300 seconds. It changed the work budget as a group, not one isolated
causal variable. Source and relay copies were frozen before execution.

All 40 forwarded requests completed with HTTP 200, and none was refused by the
relay. Reported usage totalled $0.060410; that is provider telemetry, not an
independently verified invoice. The trace recorded 21 successful reads, eight
grep calls, nine successful shell calls and 14 shell errors, with no edit event.
There were 37 tool-call finishes, one output-length finish and one stop finish.
Normal process exit therefore did not establish completion of the repair.
The output-length event remains a limit on interpretation even though the
aggregate process/request budget was not exhausted.

Both historical preflights behaved correctly. The final necessary-condition
failure was attached to the actual receipt as operator testimony, with no memory
selection or citation claim. The [metadata report](opencode-extended-calibration-2026-09-08.json)
retains identities, exact settings, counts, digests, partial native observations
and private artifact locations. The store and relay stopped; native session
homes and derived caches were removed. No raw native session is committed.

This closes the proposed aggregate-budget calibration with a negative result.
It does not qualify this binding/task pair for a memory-benefit comparison,
diagnose general model capability or justify another unchanged run. Further
work needs a different binding or a specific new explanation for the failure.
