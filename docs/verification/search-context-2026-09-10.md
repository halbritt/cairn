# Changing context through native search — 2026-09-10

Native tools could capture a phase-restricted note but could not search using
context supplied for the current call. MCP rejected the additional argument;
the installed OpenCode adapter rejected it too. MCP context was fixed at startup,
and native OpenCode required a settings-file edit to change it.

The [context argument](../search-context.md) now fills fields left unset by the
host configuration. An agent can search with its current phase or revision
without restarting the facade. Host-fixed fields remain fixed, and declarations
do not persist into another call. This is explicit caller context, not automatic
workspace observation or evidence of an executed task.

## Verification

The MCP regression failed before implementation and then retrieved and pulled
the phase-specific note through the authenticated store. The public stdio check
also exercises all six context fields, phase changes within one session, exact
retries, conflicting retries, fixed-host conflicts, missing/mismatched context,
invalid values and mandatory instructions. A refused host conflict does not
reserve the request identity; a corrected call can reuse it.

Disposable PostgreSQL/race, authenticated CLI/API, MCP, Go tests, 40 Python tests
and static checks pass. Native OpenCode 1.18.21 checks exercise declared phase
selection, exact pull, missing and different phase, fixed-host conflict and
invalid arguments alongside the existing capture, edit, destination, permission
and normal-session checks. These use scripted tooling without an answering model.
The final run also verifies all six canonical field names and OpenCode's
configured `binding`/`capability` mapping, including each fixed-field conflict.

The first native run timed out on its first existing capture call, before the
new search case. Logs showed startup initialization; the cause was not established.
A later run passed through the same route without a startup change. The installed
adapter independently reproduced the original argument rejection. The failed
run remains in the artifact manifest rather than being classified as a feature
test failure or a passing native check.

## Boundaries

The API and compiler already accept the underlying context fields. No database
migration, ranking profile, authority transition or source applicability change
is introduced. Calls without a per-call declaration preserve the configured
behavior. Search receipts retain effective context; pulls retain their original
receipt meaning. A caller cannot use this argument to change repository/session
scope, destination, source pins or host-fixed context.

The change removes a native interface limitation for phase-specific guidance.
It does not establish improved task outcomes, accurate caller declarations or
automatic currentness. E1 and the broader task-value requirements remain partial.
The [manifest](search-context-2026-09-10.json) records checks, failures and decision
provenance without operational notes or raw model output.

Pincite final packet `pkt-5bad129a87ea04a6` has validated typed evidence and decision
receipt with closed citation traces. Seven remaining interface/method-set and
typed-nil obligations are retained as nonmaterial: no Go interface changes or
typed-nil absence semantics are introduced.

## Installation

Clean `9f43b00` is installed in the CLI and API; the native OpenCode adapter matches
source. Schema 033 and the PostgreSQL process are unchanged. Protected connection,
identity and semantic-worker files retain their previous hashes.

A fresh installed MCP facade exposes the new field, retrieves the existing
phase procedure with declared validation context, and omits that declaration on
the next call. Installed native OpenCode retrieves the same note using its actual
session scope. The retained procedure was subsequently updated from version 2 to
3 with this interface and source pointers; a fresh pull verifies its exact body
and preserved metadata. No production fixture or answering-model trial was added.
Existing MCP processes need restart to load the new tool schema.

[Feature CI 34459553174](https://github.com/halbritt/cairn/actions/runs/34459553174)
passed every job and step for installed `9f43b00`, including PostgreSQL/race,
Python, static/build and authenticated CLI checks. Local verification additionally
exercised actual native OpenCode tools and scripted sessions.
