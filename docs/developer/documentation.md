# Writing Cairn documentation

Write for someone using or extending Cairn on their own system. Explain the
problem a feature solves, who needs it, and how to use it before describing its
implementation. A reader should not need the project's development history to
understand a current guide.

## Choose a destination

| Content | Destination |
| --- | --- |
| Project purpose and first steps | Root README and quickstart |
| A task with an expected result | `docs/guides/` |
| Client setup | `docs/mcp/` and linked integration guides |
| Deployment, configuration, recovery | `docs/operator/` |
| Architecture, contribution, validation | `docs/developer/` |
| Commands, fields, contracts, refusal behavior | `docs/reference/` and feature references |
| Current capability boundaries | `docs/implementation-status.md` |
| Remaining work and acceptance criteria | `docs/roadmap.md` and `docs/plans/` |
| A dated experiment or deployment observation | `docs/verification/` |
| Superseded chronological material | `docs/history/` |

Add discoverable links from the [documentation index](../README.md). Keep one
primary explanation of a contract and link to it from introductory guides.
Design snapshots in `docs/sources/` are pinned inputs; do not edit their wording
as part of documentation maintenance.

## Voice and examples

Address the reader directly. Use “your installation,” “the operator,” or a named
component when that is the relevant role. Avoid “the owner's host,” accounts,
private network addresses, and the story of who requested a feature in current
guides. “Owner” is appropriate when it names a real API relationship such as a
receipt's owner; do not rename domain terms merely to change tone.

Use generic, explicitly explained paths and placeholders. Shell examples can
use `$HOME` and task-specific variables; JSON and TOML do not expand shell
variables unless their consumer explicitly does so. State whether a block is
runnable shell, a request shape, or illustrative output. Do not include real
credentials, operational memory, raw model output, or private session content.

Prefer a short explanation of the behavior to repeated claims that a feature is
implemented. Describe failures where the reader needs to recover: which command
reports the error, what it means, and the appropriate next step.

## Verify claims

Check command names and flags against the parser or built CLI. Check API routes
and fields against the generated contract and implementation. Run changed setup
examples using a disposable store. Link to a canonical reference for details
that would otherwise be duplicated across clients.

Distinguish a library function that calculates a report from a caller that
enforces its result. Distinguish source capability, test coverage, an installed
configuration, and observed live behavior. A successful retrieval is not proof
of improved task outcomes, and a migration number does not prove feature
acceptance.

For documentation changes, check local links and anchors, run `make check`, and
exercise affected examples. Broaden validation when a claim depends on behavior
that those checks do not establish. Record what was actually tested.

Historical records retain their original evidence and qualifications. If an old
claim is wrong, add a dated correction or a clear link to the current contract;
do not rewrite an old experiment as if it had tested newer code.
