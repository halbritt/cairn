# Parent SDK reactor source

The serialized parent lifecycle is split across three modules:

- `trial_sdk_parent_storage.py` owns reserved regions, exact codec loans and
  retained send frames.
- `trial_sdk_parent_reactor.py` supplies the protocol-2 state machine and its
  controlled storage model, `Engine`.
- `trial_sdk_owned_reactor.py` supplies `OwnedEngine`, with separate native target,
  local Engine and Node actor release phases.

This source imports no SDK and launches no process. It is not wired into the trial
runtime. The [Python Peer](sdk-python-peer.md), [Node transport](sdk-native-ipc.md),
[codec](sdk-ipc-codec.md) and [input receiver](sdk-input-receiver.md) supply separate
boundaries. The [direct-process owner](sdk-native-process-owner.md) supplies a
separate mechanism; actual launch/process/stream/actor port adapters and their
custody remain external. `Engine`'s model-only automatic release is not a fallback
for `OwnedEngine` or a production release contract.
The [Node custody owner](sdk-node-owner.md) supplies the corresponding actor-side
protocol mechanism; its ordinary fixture adapters do not wire the trial runtime.

The caller supplies the codec and receiver classes, a monotonic clock, selected
work/stop deadlines and all required ports. Construction checks method presence;
it cannot authenticate their effects. Ports implement these observations:

| Port | Required observations and operations |
| --- | --- |
| data, control | `receive_into`, `send`, `close`; credential-authenticated records, known would-block or exact whole-record transmission |
| launch | `begin`, `collect`, `commit`, `cancel`, `release`; complete launch validation before any process effect |
| process | `request_stop`, `signal`, `wait_ready`, `release`; requested signal acceptance kept separate from an actual owned native-child wait |
| native | `write`, `close`; actual native-input acceptance and known stdin closure |
| streams | `read_into`, `close`; nonblocking bytes and independently observed stdout/stderr EOF |
| validator | `validate`; the selected input-document semantic policy |
| actor, for OwnedEngine | `wait_ready`, `release`; the controller's actual owned Node OS wait and known actor-owner cleanup |

Receive/stream would-block is `-1`; stream EOF is `0`. Positive counts must fit the
actual supplied view. A send is either `-1` or the entire frame length. A positive
socket send does not establish native stdin acceptance or model delivery. Wait
ports return `None` or an exact signed 32-bit exit code, never a Boolean, payload
PID or acknowledgement substituted for a wait. Native and actor waits are distinct.
Owned release/close ports must return exactly `None` after known cleanup.

`interests()` returns generation/epoch tickets for currently active operations.
The driver supplies genuine readiness with `notify(key, ticket)` and calls
`step()`. Each step performs at most one selected action; round-robin selection
keeps independent control and wait observations available while data is parked.
Would-block retains the same frame and requires a new readiness observation. An
idle result permits the driver's bounded wait; it does not authorize synthetic
readiness or a busy retry. Stale tickets/completions, partial sends and uncertain
effects preserve failure and outstanding loans. `UNRESOLVED` requires the outer
owner to contain and collect remaining processes; it is not successful cleanup.

The external driver must bind clocks and deadlines to the original trial profile.
The generic constructor accepts stop intervals up to ten seconds; that is not an
authorization to extend the selected runtime's stop or wall budgets. Pure tests
use controlled clocks, including a two-second stop interval.

`OwnedEngine` reserves 22,386 bytes of remote quota before Node creation, without
allocating Node placeholder payloads. Its local backing layout is 1,131 bytes;
the real input receiver's decoder scratch adds 16, for an initial reservation of
23,533. The Node allocator must subdivide its genuine preassignment before actual
allocations. Actual Peer auxiliary reservations use the same aggregate ledger;
there is no separate standing `receive_aux` placeholder in the owned variant.
The original 24,576/3,480 ledgers and aggregate controls remain required. These
reservations do not prove all Python/V8/allocator/kernel memory fits the profile.

The release milestones are separate:

1. An actual native wait, both stream EOFs, stdin closure and sent completion
   notices permit `WAIT_RELEASE`. The next valid, increasing empty kind-8 request
   authorizes target cleanup; it does not itself establish release.
2. Known stream/process/launch cleanup permits one correlated kind-139 reply with
   body value 1. Over would-block, the exact frame stays retained and independent
   control remains available. Only known whole-record send permits parent endpoint
   closure and local retirement.
3. `ENGINE_LOCAL_RELEASED` means local backing, loans and transient reservations
   are zero while all 22,386 remote bytes remain charged.
4. Only the actual owned Node wait and known actor-owner release permit remote
   retirement and `RELEASED`. Promise settlement, null references, a release reply
   or the native-child wait cannot substitute for that actor wait.

Unknown send, close, wait, cleanup or refund does not earn a guessed release or
replay. Release attempts are made once. Cleanup milestones do not erase the first
failure; for example, cancellation remains cancellation after known cleanup.
Trusted constructors and callbacks must not retain borrowed frame views or aliases,
reenter serialization, or replace the supplied ports. View release and scalar
tickets are ownership discipline, not hostile-code isolation or kernel identity.

The standing Python suite runs 26 preserved pure cases: fifteen reactor cases,
five signal-contract cases and six owned-release cases. Explicit `load_tests`
adapters expose their original standalone functions to unittest discovery. The
tests use the real codec and input receiver with controlled ports/clocks; their
waits, signals, EOF and cleanup are testimony, not OS observations. They cover
backpressure/control progress, exact credits, UTF-8 input bounds, uncertain sends
and cleanup, release ordering and remote quota retention.

```sh
python3 -B -m unittest discover -s scripts -p 'test_trial_sdk*.py'
```

These checks do not establish real adapter/sandbox custody, exact SDK compatibility,
hook coverage, model context delivery, physical fit, account/provider capacity,
fresh grading or measured usefulness. Capture HOLD, original budgets and the full
CAIRN-107 acceptance criteria remain in force.
