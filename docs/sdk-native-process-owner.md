# Direct native-process owner source

`scripts/trial_sdk_native_process_owner.py` supplies `NativeProcessOwner`, a
direct-child mechanism for a trusted fixed builder. It is not wired into the trial
runtime. Structural launch validation is not authority, executable authentication,
account admission or exact SDK tuple compatibility. The builder must independently
bind the selected executable, arguments, environment, cwd, containment, namespaces,
limits, deadline and subscription/account route before calling `start`.

Construction is inert. `start(argv, cwd, env)` consumes one launch attempt, including
a refused plan. It creates three distinct unnamed Unix stream pairs and passes
only the selected child endpoints as stdio through Popen's close-fds exec path.
Parent endpoints are nonblocking. The returned direct Popen is retained before
pidfd setup; no reported PID or outer launcher wait substitutes for that child.
Only successful `NativeInput` construction transfers sole custody of the original
stdin endpoint. Empty argument strings survive plan validation.

Popen return and an ordinary child running are the present exec observations,
not the full native executable/mount/exec-barrier binding. Failure after child
creation retains the object and acquired handles. Missing-pidfd setup requires the
already-selected outer containment owner: this class has no numeric native-PID
signal fallback. Popen failure remains conservatively launch-unknown. Cleanup of
an unused owner permanently closes its launch gate; prelaunch stdin close refuses
without leaving a flag that could falsely release a later descriptor.

`write` borrows stable readonly storage of at most 512 bytes and delegates to
[NativeInput](sdk-input-custody.md). Counts mean kernel acceptance into owned stdin,
not application consumption or model delivery. `read_into` borrows a writable byte
memoryview of at most 512 bytes and returns an actual count, `-1` for known
would-block/interrupt, or `0` for observed endpoint EOF. EOF closes that endpoint
once and is independent of process wait. Callers reserve storage and keep it valid
through the operation; raw filenos are readiness-only borrows, not permission to
duplicate, replace, transfer or close them.

`signal(operation, number)` accepts increasing exact-int operations and normalized
exact-int SIGINT/TERM/KILL values. Kernel signal acceptance is separate from death,
descendant delivery, cancellation success or wait. `observe_wait()` owns waitpid
for the direct child and retains its actual PID, raw status and signed returncode.
No foreign waiter or Popen poll/wait may reap it. Wait errors remain unresolved;
they never become a fabricated zero exit code.

`release()` requires actual wait, known stdin closure and both observed EOFs.
`cleanup_after_wait()` can detach remaining handles while preserving missing EOF
or uncertain-close facts; `handles_detached` does not prove an uncertain kernel
close completed. Uncertain raw descriptor numbers are detached before one close
attempt and never retried. Cleanup does not kill a live child or invent its wait.
Primary failure and cleanup uncertainty remain separate from resource release.

All calls require trusted serialization without reentry, foreign descriptor/wait
mutation or retained raw aliases. Allocation failure and asynchronous interpreter
interruption are not proven atomic. Internal exception tracebacks are not a
publication protocol. Popen/interpreter/socket/kernel resources and cross-process
backing are not measured here against the original ledgers or aggregate controls.
No native sandbox, protected status channel, descendant policy or SDK lifecycle
admission follows from these source names and types.

The [parent reactor](sdk-parent-reactor.md) still needs explicit port adapters.
For example, its signal port uses names and accepted/rejected results, while this
mechanism takes normalized numbers and returns a Boolean. Its native wait port
needs an exit code derived from this owner's actual wait fact; actor wait remains
a different process. No implicit interface mapping or automatic release is supplied.

The optional host check runs eleven preserved cases with nine fixed ordinary
Python children. It covers raw wait/EOF ordering, empty argv, receiver-to-input
echo and actual counts, pidfd TERM, blocked-input/KILL, bounded FD scanning and
prelaunch gates. One injected exception occurs after an actual stdin close; that
case must retain `released:false`. It tests uncertainty preservation, not a
naturally observed OS close failure. Local receiver reply completion is testimony,
not authenticated transport or model delivery.

```sh
make test-sdk-native-process-owner SDK_OWNER_TEST_OUTPUT=/absolute/fresh/output-directory
```

The output parent must exist; existing paths and dangling symlinks refuse reuse.
The checker guards and hashes nine authored Python inputs, copies them to a
read-only snapshot and records an intent before launching its single fixed test
command. Host cases and the argv-dependent helper are outside default unittest
discovery. The checker retains at most 64 KiB of output, with ten seconds for work
and two seconds reserved for cleanup; scheduling and syscall latency are not hard
real-time guarantees or physical memory quotas.

The test leader starts a fresh session/group and stays unreaped during pidfd
WNOWAIT observation. The checker is a verified subreaper. On every terminal/error
path it attempts one owned group kill before reaping, then retains direct/adopted
actual waits until no group children remain. The selected helpers do not escape
that inherited group. A group signal alone does not prove cleanup; missing waits,
close uncertainty or a deadline overrun fail the check. An already exited test
leader can retain returncode zero after the containment signal attempt. This
supervision fixes the old poll-before-containment gap without inventing successful
NativeProcessOwner release on a failed setup path.

The checker saves outer wait/cleanup observations before post-run source and log
I/O. Source revalidation errors mark the final result refused while preserving
collected facts. File-write errors are named failures; a partial receipt is never
reused. The final stdout summary also retains outer waits and pipe-close facts.
A default Python unit case injects post-run source refusal into controlled result
data, with process/kernel effects forbidden. It checks refused receipt preservation;
it supplies no actual wait, containment or host-case evidence.

Source snapshots, after-run hashes and retained observations assume trusted
serialized source custody, not hostile same-UID isolation. Toolchain/kernel/ABI
and protected runtime bindings remain external. Capture HOLD, original budgets,
provider/account capacity, hook/model delivery and full CAIRN-107 criteria remain.
