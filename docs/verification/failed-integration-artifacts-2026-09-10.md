# Keep failed integration artifacts

`make test-integration` now retains its temporary directory when a check fails
and prints that directory's location. The exit trap still stops its owned
PostgreSQL cluster and preserves the failing check's exit status. Successful runs
still remove their files. If the database cannot be stopped, the command fails
and keeps its directory for inspection.

This repairs an obstacle encountered while investigating the
[native OpenCode timeouts](harness-configuration-text-2026-09-10.md): the ordinary
test script deleted the fixture logs and state needed for diagnosis. Earlier
investigation required a separate copy of the runner to preserve them. This change
does not repair the native startup stall or change any test assertion, deadline,
permission, adapter, installed binary or production service.

## Cleanup verification

A controlled Go-job failure exercised the actual shell runner and a newly
initialized PostgreSQL cluster. Before the change, exit 19 was preserved and the
cluster stopped, but the diagnostic directory disappeared. After the change, the
same exit status and shutdown were preserved, the directory remained, and the
failure message named it.

A success-path probe substituted successful Go and Python jobs while still
creating, starting and stopping its own real PostgreSQL cluster. It verified exit
zero and removal of the temporary directory. These job stubs isolate the shell
lifecycle contract; they provide no application-test coverage. Shell syntax and
diff checks also passed. The database-stop-failure branch was reviewed against
the script but was not exercised by these probes.

## Native investigation and limits

All five isolated recent-file cases passed with startup logging enabled. A full
authenticated API/native-tools run then timed out after 60 seconds at its first
`opencode debug agent` search. That path needs no answering-model request. Its
retained global dependency-install lock and unfinished package metadata matched
the condition found in the earlier failed recent-file baseline; the project-local
installation had completed in both cases.

Pinned OpenCode 1.18.21 source explains why unfinished installation can delay
tools: the [tool registry](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/tool/registry.ts)
waits for [configuration dependencies](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/config/config.ts),
which include background plugin installation in each configuration directory.
The [installer](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/core/src/npm.ts)
holds a directory-specific lock during its package operation. The surviving lock
maps exactly to the fixture's global configuration directory. This narrows the
investigation; it does not identify the underlying network, cache or runtime cause.

Launching under `strace` worked despite the earlier refused attach attempt. A
standalone traced startup reached the Cairn executable, then exited on deliberately
absent connection setup. The complete authenticated API/native-tools check also
passed under tracing: 226 native debug invocations, all five recent-file sessions,
the maximum-note session and permission/refusal checks. Its Cairn binary was clean
`5df9fa8`; the actual OpenCode executable was unchanged at 1.18.21. No answering
model was invoked, and both disposable full-run clusters stopped.

Tracing changes timing. This pass does not establish that untraced startup is
reliable or that the original failure is fixed. No timeout increase, automatic
retry or disabled snapshot was selected. Further native diagnosis needs a
discriminating observation of unfinished dependency work, rather than repeated
unchanged suites. Successful cleanup and transport checks add no task-value claim.

The [manifest](failed-integration-artifacts-2026-09-10.json) identifies the actual
binary separately from the diagnostic wrappers, whose hashes appear in fixture
reports. It retains source hashes, logs and the bounded decision receipt. Scratch
artifacts under `/tmp/cairn-native-stall-20260910` and its named disposable roots
are temporary evidence, not a durable archive.

Pincite packet `pkt-18af6ff47fa75d92` supported preserving behavior and requiring
evidence before intervention. Its decision receipt validates and consumption is
closed. The 29 remaining obligations concern unrelated surfaces or stronger
operational claims; they are nonmaterial to this shell cleanup repair. The native
root cause remains an explicit unresolved question.
