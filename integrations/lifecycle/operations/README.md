# Inactive runtime operations

This directory publishes the reviewed source candidates for authenticated
topology collection (F1 v6) and fixed-action effect sequencing (F3/F6 v4).
F1 keeps separate three-file collector and helper closures; its tests live
outside those closures. F3 includes its seven runtime modules and isolated tests.

Run the 168 F1 and 53 F3 tests from the repository root:

```sh
make test-hybrid-runtime-operations
```

The runner requires Linux, Bubblewrap at `/usr/bin/bwrap` and Python at
`/usr/bin/python3`. It copies the source to disposable directories with explicit
0700 directory and 0600 file permissions, then mounts each suite read-only at
`/work` in a separate namespace. The namespace has an empty home, private temporary
storage, no host proc mount, no network, and no installed profiles or sockets.
Tests use synthetic observations, fake operations and fixed disposable children.

Runtime source bytes are unchanged from the reviewed candidates. The sole fixture
adaptation makes F1's temporary code-copy directories 0700 and files 0600, independent
of the caller's umask. F3 retains the reviewed bounded deadline-pause assertion.
The write-capable mutation driver and operational evidence packets are omitted.

F1 remains `ENACTED=False`. F3's entry refuses `REFUSED_UNBOUND`; launchers,
authenticated evidence, host selection, clock qualification and journal collection
remain unbound. This is inspectable inactive code, not deployment or adoption
acceptance. Complete integration, exact enabled bindings and a separate live grant
remain required.

The journal projection can still accept cursor revisits and ambiguous end-cursor
inclusion; it never qualifies evidence. A stop-receipt write failure can still
propagate `OSError`, while the runner remains stopped in memory. Those limits remain
open for separately scoped work.
