# Inactive runtime operations

This directory publishes the reviewed source candidates for authenticated
topology collection (F1 v6), fixed-action effect sequencing (F3/F6 v4), and
fixed SSH/systemctl adapters (F3 backend v2). The unbound F1 executor module
contains installation, verification and external-completion mechanisms from
the reviewed v3 candidate, with its root-child intake correction.
F1 keeps separate three-file collector and helper closures; its tests live
outside those closures. F3 includes fifteen runtime modules and isolated tests.

Run the 168 F1 collector/helper, 97 F1 executor and 87 F3 tests from the
repository root:

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

Collector/helper and F3 source bytes retain the reviewed candidates. The F1
executor shares one core instead of five role copies, omits machine-bound
selections and uses synthetic fixture bytes. Its root child has 40 seconds for
30 seconds of work, 5 seconds of final publication and 5 seconds of startup.
The install wrapper envelope is 105 seconds; its supervisor gives it 110 seconds
and has a nominal 175-second envelope, with a proposed 180-second external
observer allowance. Observation intake remains 35 seconds, with a 100-second
wrapper, 105-second supervisor intake and 170-second supervisor envelope.
These sampled allowances are not hard bounds on blocking I/O or cancellation.
F1's code-copy fixture permissions remain independent of the caller's umask.
F3 retains the reviewed deadline-pause assertion. Mutation drivers, private
bindings, completion captures and operational evidence packets are omitted.

F1 retains false enactment switches. Its executor entry always refuses
`REFUSED_UNBOUND`; selection, owner home, root program, layout and completion
captures remain unbound. Fixed uid/gid 0/1000 and tool-route assumptions remain
source assumptions requiring exact enabled-source review. Both F3 entries refuse
`REFUSED_UNBOUND`; the concrete adapters have no operational configuration. Authenticated evidence, host selection,
clock qualification and journal collection remain unbound. Exact enabled bindings,
complete integration and a separate live grant remain required. Publication
supplies no installation, privilege or observation grant, F1 acceptance, or
same-Store claim.

The Root launcher must be the sole caller of the remote entry. After an unknown
result, the composed run relies on Root's stopped state and uncertainty receipt.
A visible remote completion file alone does not prove durable success or authorize
reopening. Child status also requires stable default `SIGCHLD` disposition and no
competing waiter; the guards observe disposition at selected boundaries, not
continuously. F1 child intake has the same no-competing-reaper premise. The
guards never reset handlers or retry an uncertain effect.

This repository directory includes tests and is not an installed bootstrap
closure. A future bootstrap must validate the exact source closure before local
imports. The pinned SSH version, configuration and authentication dependencies,
remote shell compatibility, and launch/start budget composition still need review.

The journal projection can still accept cursor revisits and ambiguous end-cursor
inclusion; it never qualifies evidence. A stop-receipt write failure can still
propagate `OSError`, while the runner remains stopped in memory. Those limits remain
open for separately scoped work.
