# F-04 activation implementation

This source slice connects `binding.ResolveActivation` to the actual
`InboxBinding.effective_config` calls in `memory._main` and `coordination.main`.
The remaining handler runs from the checked Python closure. Grant allocation,
store calls, delivery, command journals and native-session transitions keep
their existing Python owners. The activation operation only resolves and
persists the shared enabled decision.

This describes source staging and its tests. Independent candidate review,
Release integration, production installation and lifecycle acceptance are
separate steps. The native C/CPython/provider construction remains paused.

## Executable selection

The private executable is built from `cmd/cairn-lifecycle`. It accepts the
private `lifecycle binding activation` protocol described in
[Foundation](foundation.md#child-failures-and-the-enclosing-deadline).
`cmd/cairn` keeps its public lifecycle registration disabled. No runtime flag
or environment variable opens that public command.

Release must explicitly build and select this private image in its `/3`
descriptor and compatibility receipt. Building the ordinary `cmd/cairn` image
does not supply this route. Release has confirmed that its binary staging can
select this image without a receipt or authority change. Complete hash-named
snapshot and launcher publication remains Release-owned and is not implemented
by this slice. The private image receives the checked current-config digest,
binding digest and independently selected receipt digest.

`lifecycle_compatibility.py` supplies the stdlib verifier literal, snapshot-byte
staging and launch-vector construction. Its new `/3` snapshots include all
18 canonical members plus the declared aliases. Each member starts with a
guard that refuses direct execution before adjacent imports. The externally
selected interpreter/verifier/receipt/config vector authenticates every member
before executing cached bytes. The Go child executes through the retained
checked file descriptor at `/proc/self/fd`, and its F-01 checks also verify the
executing image. There is no path-based reopen for process execution.

The approved noninteractive shell launcher contains only the quoted vector
followed by `-- "$@"`. Its bytes select the content-addressed launch directory;
the checked caller requires mode 0500 when issuing it. Installation and owner
hook selection are external trust anchors. Receipt self-description and a
launcher hash do not replace that trust. Existing installed `/2` snapshots,
aliases and launchers are not rewritten by this staging API.

## State and failure behavior

Both reached prefixes preserve their original event bytes, captured environment
and cwd. With valid code, source admission precedes binding-data access. The
parent reads its current config once; Go checks that digest and reuses the same
decoded config when it is also the memory-ledger configuration. Checked parent
objects are reused by retained validation rather than reloading the current
config. Noncurrent config checks remain part of the enabled binding contract.

`ledger.DecideActivation` uses the existing session lock inode and 1.5-second
contention policy. It retains append-open modes and symlink following where the
source allowed them. Uncontended flock is attempted before a contended deadline
is examined. The marker is durably replaced before the ledger, with file fsync,
close, same-directory rename and directory fsync. A marker without its ledger
refuses a reset. An error after publication remains an error even if a later
reader can observe the committed decision.

Codex activation checks identity and activation decisions without imposing the
stricter turn-accounting reader. Claude activation retains `load_grants` checks.
Untouched parsed values, object ordering, unknown fields, unlimited integers,
floating-point values and escaped surrogate strings use the lossless value
reader/writer. Formatting whitespace is not preserved. Activation does not
compact 64/65 decision or grant entries, or the 4096 folded-token boundary;
those operations remain with their grant consumers.

Cached decisions win over a newly selected manifest. Publishing disabled B
does not change enabled A, remove grants, or override a cached true decision.
A cached true decision still requires final config checks under B. Cached false
retains the source's disabled final-pin exception. No automatic session refresh,
drain or retirement is claimed here; unknown refresh still blocks production
transition under the accepted contract.

Private success is exactly one compact enabled JSON line, empty stderr and
exit 0. Private failure is exit 1, empty stdout and one bounded error frame.
The caller preserves actual OS exception subclasses and the existing required
restoration/native-veto policies. Panic, signal, invalid protocol and empty or
oversized output become transport failures, without echoing child output,
returning false, promoting the child's status 2, or starting a Python fallback.
The parent kills and reaps only its owned activation child on failure or handled
interruption. The private Go image also sets a parent-death signal.
An inherited nondefault SIGCHLD disposition refuses before spawn: an unavailable
child wait status cannot establish a successful activation exit.

The verifier captures the enclosing monotonic deadline before receipt and
member checks. Trusted vectors select the installed 13/150-second memory or
2/15-second coordination budgets. Listed rows are checked; event fields cannot
choose or extend a deadline. Go IPC, retained API calls, recall and ledger waits
use the remaining same deadline and retain tighter source limits. Issued inbox
commands keep their own command and intent-lock deadlines.

## Issued commands and output

New completion, acknowledgement, response, finish-reply, drain and compact
read/inspect/settle/advance paths use the selected short launcher and original
tail. Fresh issued processes revalidate their selection without re-entering
activation. Compact lookup root, resolved binding/config and command context
are checked before derived config loading or existing effects.
Compatibility compact commands use the config selected by the checked launcher;
its immutable generation path is independent of the mutable state directory.
Legacy `/2` compact commands retain the conventional `root/bindings` lookup.

`shell_commands` records shell-quoted presentation alongside the retained argv
fields. Inner store CLI argv and direct history reads keep their machine forms.
Each rendered command is bounded at 512 UTF-8 bytes and each control/presentation
set at 2048. Normal compact cues, source-unavailable and oversized-source
fallbacks, next-item cues and turn-end reminders use the same launcher. A source
whose JSON escaping would exceed the inline allowance uses the exact whole-read
workflow. The new Codex hook output is also checked against 12000 encoded bytes;
the retained bridge continues to enforce the smaller configured recall budget.
No source is truncated and read/inspect/advance do not acknowledge a neighbor.

## Verification surface

Run the focused process corpus with the shared namespace wrapper:

```sh
python3 -I -B scripts/test_hybrid_runtime_operations.py --timeout 420 -- \
  env CC=gcc python3 -I -B -m unittest discover -s scripts -p 'test_f04_*.py' -v
```

Run disposable PostgreSQL integration with the namespace-local compiler alias,
OpenSSL configuration, short temporary paths and explicit timezone:

```sh
python3 -I -B scripts/test_hybrid_runtime_operations.py --timeout 3600 -- sh -c 'mkdir -p /tmp/bin && ln -sf /usr/bin/gcc /tmp/bin/cc && printf "[req]\ndistinguished_name=dn\n[dn]\n" > /tmp/openssl.cnf && exec env PATH=/tmp/bin:$PATH CC=gcc TZ=America/Los_Angeles TMPDIR=/tmp OPENSSL_CONF=/tmp/openssl.cnf make test-integration'
```

The short `TMPDIR` keeps test Unix socket paths within their length limit. The
compiler alias supplies `cc` for nested builds, and the minimal OpenSSL
configuration permits disposable certificate generation. These files are local
to the wrapper's temporary filesystem. With the wrapper's empty environment, five
Core timestamp-replay equality tests fail on unchanged main as well as this
slice. Setting the timezone makes those five checks pass without changing Core,
the tests or the wrapper. These environmental results do not qualify production
installation or waive other integration failures.

The fixtures build the actual private image and stage disposable receipts,
snapshots and launchers. Only the Store boundary is scripted. State, flock,
checked-FD process execution, emitted shell text and local atomic writes are
real. Fault fixtures also use separately pinned test images that invoke the
real activation operation before failing or delaying; these are not successful
activation stand-ins. Syscall fault cases use `strace` against owned children.

The corpus covers source-valued state comparisons, cross-language contention,
process death and real write/fsync/rename failures; complete enabled/disabled
frontends and `/2` coexistence; byte decoding and Claude ownership; all issued
wrappers, compact and reminder variants; exact required-context wire limits;
config replacement, code/alias/image tampering and direct-member refusal; the
failure table, interruption and enclosing-deadline behavior. Host-row timings
separate interpreter/receipt/hash/spawn, activation and retained continuation.
Empty capture inputs intentionally exercise retained source refusals rather than
model/provider construction; absent host rows are not qualified.

These are selected synthetic/source-writer fixtures, not a claim of all-input
CPython parity or a replay of every original whole-hook recording. Malformed
Class B inputs require stable safe refusal; interpreter/resource limits remain
Class C exclusions. Broad lifecycle checks and disposable PostgreSQL integration
remain required for an integration candidate. Test success does not establish
production refresh, all-host deployment, measured performance gains, full
HC-03 grant migration, HC-04 native policy or final runtime retirement.
