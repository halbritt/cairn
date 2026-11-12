# R05-062 journal comparison

This slice adds Go coverage for the existing
`InboxRecovery.test_committed_command_replays_through_the_api_and_a_different_body_is_refused`
method. It preserves the Python method and its fixtures. The other proposed R05
cores and all original process and installed tests remain retained.

The sequence completes twice with the same request, observes a lost reply and
a stale-session refusal, then attempts a changed body and an acknowledgement.
The committed result survives the replay refusals. Changed body and operation
refuse with the same request identity. Four scripted API sends occur; the two
conflicting operations do not send another command.

The comparison found that the Go conflict diagnostic omitted the persisted slot
status and the Source explanation of immutable requests and host reconciliation.
The repair adds that text to the existing conflict branch. It preserves exit 4,
the status, journal contents, refusal order and API effects. Missing status keeps
its existing error boundary. No Source assertion or oracle is rewritten.

## Focused checks

From the repository root, use the shared host workflow:

```sh
mise tasks ls
mise tasks info test:unit
mise run test:unit -- ./internal/lifecycle/journal -run '^TestR05' -count=1
mise run test:unit -- scripts/test_r05_journal_core_062.py
```

The Python test reuses the Source fixture and invokes its unchanged method
against Source and the Go journal component. Its test-binary adapter calls the
real Go journal and the same scripted CLI subprocess. It does not replace the
installed frontend's context-admission checks. The independent Go checker
preserves the original eight assertion expressions and separately checks exits,
diagnostics and the final API/journal effects.

The fault controls mutate captured observables and execute both checkers.
They cover a wrong refusal, an incorrect exit, swapped reply steps, reuse of
the response request ID for a completion replay, a missing persisted replay
field and a missing final API send. They establish checker sensitivity, not
injection of faults into a production runtime. Swapping reply steps does not
prove ordering among identical API requests. The old checker does not inspect
exit codes or the final API-send count; those surviving controls remain explicit
coverage gaps. No old assertion is retired.

## Bounded measurement

Default discovery runs paired assertions and fault controls. Select the timing
method explicitly after the actors and paired inputs are fixed:

```sh
CAIRN_R05_062_EVIDENCE=/absolute/owned/evidence \
  mise run test:unit -- test_r05_journal_core_062.PairedCore062Tests.test_three_matched_samples
```

The directory retains raw traces, the compiled test adapter, build command and
duration, actor hashes, tool identities and three alternating Source/Go pairs.
`CAIRN_R05_062_BINARY` can reuse a retained adapter only when its binary and actor
hashes match its adjacent `driver-build.json`.

Only scratch-path roots and the journal's `recorded_at` timestamp are normalized
in behavioral comparisons. JSON object-key ordering is immaterial; array order,
identities, inputs, status, exits, diagnostics and journal fields remain exact.
Raw streams and timing fields remain in the retained evidence.

Each core duration includes the shared Python fixture/context/assertion work and
the Source coordinator or Go test-adapter process boundaries. Six command
dispatch durations are recorded separately. Go also records its inner journal
execution time; the equivalent inner Source time is unobserved. Build and fixture
setup are separate. Per-executor peak footprint is unobserved, admission occurs
outside the samples, and other host activity is uncontrolled. This comparison
does not establish a suite speedup while the original checks remain, full R05
acceptance, installed qualification or production authority.
