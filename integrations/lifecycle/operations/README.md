# Inactive runtime operations

This directory publishes the reviewed source candidates for authenticated
topology collection (F1 v6), fixed-action effect sequencing (F3/F6 v4), and
fixed SSH/systemctl adapters (F3 backend v2).
F1 keeps separate three-file collector and helper closures; its tests live
outside those closures. F3 includes fifteen runtime modules and isolated tests.

Run the 168 F1 and 87 F3 tests from the repository root:

```sh
make test-hybrid-runtime-operations
```

The runner requires Linux, Bubblewrap at `/usr/bin/bwrap` and Python at
`/usr/bin/python3`. It copies the source to disposable directories with explicit
0700 directory and 0600 file permissions, then mounts each suite read-only at
`/work` in a separate namespace. The namespace has an empty home, private temporary
storage, no host proc mount, no network, and no installed profiles or sockets.
Tests use synthetic observations, fake operations and fixed disposable children.
The 34 backend tests cover fixed requests, intent ordering, child custody,
invalid-file refusal and the separate physical-join and claim-failure paths.
One harmless child exits with status 3 under ignored `SIGCHLD`; the executor must
refuse a positive outcome when its wait status is unavailable.

Runtime source bytes are unchanged from the reviewed candidates. The sole fixture
adaptation makes F1's temporary code-copy directories 0700 and files 0600, independent
of the caller's umask. F3 retains the reviewed bounded deadline-pause assertion.
The write-capable mutation driver and operational evidence packets are omitted.

F1 remains `ENACTED=False`. Both F3 entries refuse `REFUSED_UNBOUND`; the concrete
adapters have no operational configuration. Authenticated evidence, host selection,
clock qualification and journal collection remain unbound. Exact enabled bindings,
complete integration and a separate live grant remain required.

The Root launcher must be the sole caller of the remote entry. After an unknown
result, the composed run relies on Root's stopped state and uncertainty receipt.
A visible remote completion file alone does not prove durable success or authorize
reopening. Child status also requires stable default `SIGCHLD` disposition and no
competing waiter; the guards observe disposition at selected boundaries, not
continuously. They never reset handlers or retry an uncertain effect.

This repository directory includes tests and is not an installed bootstrap
closure. A future bootstrap must validate the exact source closure before local
imports. The pinned SSH version, configuration and authentication dependencies,
remote shell compatibility, and launch/start budget composition still need review.

The journal projection can still accept cursor revisits and ambiguous end-cursor
inclusion; it never qualifies evidence. A stop-receipt write failure can still
propagate `OSError`, while the runner remains stopped in memory. Those limits remain
open for separately scoped work.
