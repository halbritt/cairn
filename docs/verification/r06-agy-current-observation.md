# Agy current observations

`cairn.watch-agy-readback/2` is a separate filesystem/process observation format.
The historical `/1` shape, cumulative two-layer codec and preparation/retained
refusals remain. A version, timestamp, flag or live PID cannot qualify refresh.
This reader adds no public operation, Store read, lock or permission.

The current descriptor contains only locators:

| Field | Observed correspondence |
| --- | --- |
| `Client`, `ClientArgv` | Pinned image and literal current argv; checked against actual process observations. |
| `Generation` | Pin of `cairn.agy-collector-generation/1`, containing `Before` and `After` start/attach captures. Each records the existing process reference, image pin and argv. |
| `A`, `B` | Each selects generation, configuration, settings and launcher pins. Existing generation, producer, unbound-profile and launcher readers verify their actual closure. |
| `Before`, `After` | Raw trace pins, line-aligned byte `Start`/`End`, and callback references. `End` must equal the complete retained file length, bounded to 256 MiB. |
| `NativeSession`, `Workspace` | Must match both raw intervals and the actual pinned owner state. |
| `Callbacks` | Exact `PreInvocation` and `Stop` references: outer launcher `Exec`, zero-exit `Join`, and native input-write `Frame` offsets. These locate raw calls, not caller declarations of success. |

The private collector imports the existing read-only process helper. The owning
fixture calls `capture_owned_generation` at trace start/attach for its own traced
descendant. It reads `/proc` twice around image/argv collection and refuses a
generation change. `write_generation_pair` saves those captures in a private
directory. `trace_interval` pins complete raw bytes without normalizing them.
These functions never launch Agy, request a hook or synthesize refresh evidence.
Only the local tests launch an owned non-agent dummy.

The reader matches the collector's AFTER pid/start/boot tuple to the produced
owner state's process and the live process through `nativeSameProcess`. It
checks executable bytes and literal argv, records a fact, and repeats the same
observations at final readback. Missing records, PID reuse, changed boot/start,
dead processes, wrong owners and image/argv drift refuse.

The refresh boundary is the first actual B launcher exec by that generation's
descendants. Both launcher and configuration must match their pinned producer
rows. The exact callback program argv, complete stdin bytes/EOF, native input
frame and zero exit must correspond. No A launcher may execute after this
boundary anywhere in the complete retained AFTER trace. A before the boundary
is permitted only with its own exact callback correspondence. Offsets cannot
hide an earlier B execution. No B execution means refresh remains unobserved.

Both intervals must carry the same native session and workspace. A supported
orderly restart can select a new generation with the same history; the old
trace must contain its zero exit and the recorded parent's exact successful
`wait4` join. A changed start time with a reused PID refuses. The collector does
not stop or restart a real client to make this relation appear.

Current observations require exactly one active managed account/project layer.
Unknown active settings groups, foreign callback sources, extra event frames,
wrong profiles and pin drift refuse. The current `Stop` frame must contain the
actual JSON Boolean `fullyIdle: true`; strings, missing fields and false do not
prove idle. Historical `/1` keeps its cumulative multiplicity semantics.

All selected file and profile references enter the existing safe-path, alias,
owner, inode, credential-exclusion and fact readers. A's historical unbound
receipt is validated separately from the selected B association; its manifest
pin must also be present in the catalog's existing `Bindings` table. This does
not make A an additional current association or modify either profile. Raw
traces receive the existing bounded trace fingerprint and final reread. JSON
uses the existing unknown-field, duplicate-key and surrogate checks; the new
format also refuses case aliases of the same field.

Run the focused local checks through the repository tasks:

```sh
mise run test:unit -- -race ./internal/lifecycle/coordination \
  -run '^TestAgy(Current|Trace)' -count=1 -v
mise run test:unit -- scripts/test_r06_agy_current_observation.py
mise run check
mise run host:exec -- --timeout 10m --weight 1 -- \
  python3 -B scripts/verify_docs.py --tier 1,2,3,4 --json
```

Go trace/process fixtures are explicitly synthetic unit records. Python tests
exercise actual `/proc` collection and numeric cleanup of an owned dummy; they
do not import or launch a native Agy client. They cannot supply native or
authenticated D2 evidence. No native baseline is replayed and no image build is
used as a substitute for observation.

The result is a per-point, non-atomic observation. `ExternalCoverage` remains
unknown and the global `NativeRefresh` field is not promoted by one callback;
the new fact records only the bounded interval. Authenticated D2–D4, private
unbound drain association capture/recheck and the complete D6 remain separate.
This cut neither clears other blockers nor constructs public readiness.

For R-07, an idle live Agy session stays refresh-unobserved until its next
actual managed hook. No kill or forced probe is authorized. Ordinary Agy memory,
TLS/network behavior, combined native D1–D6 qualification and production remain
unestablished. A future separately admitted fixture's scripted turn must supply
the current native evidence after this code and the private association piece
have been accepted together.
