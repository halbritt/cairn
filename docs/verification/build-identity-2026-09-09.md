# Executable build diagnosis, 2026-09-09

Cairn now reports which CLI, API and MCP facade binaries are running. This
addresses a recurring distinction in the installation history: source checkout,
installed CLI and resident API can differ. It helps investigate remembered
commands that do not match the installed interface. It does not determine whether
a note is stale or whether a feature is supported. [Commands and limits](../build-identity.md).

`cairn version` needs no store or configuration. Authenticated `agent version`
returns separate client and server build objects without a stdin request.
MCP initialization identifies its own executable instead of reporting constant
`1`; its protocol and five tools are unchanged. Output includes Go/module versions
and available VCS stamps, with unknown modification state represented by `null`.
Build flags, module paths, dependencies, environment and credentials are excluded.

## Checks

Initial tests found that local version inspection attempted database access, the
API route returned 404, the agent operation was unknown, and MCP still reported
`1`. Final tests verify unknown stamps, field exclusion, authentication, method
and unknown-field refusal, database-independent local diagnosis, and the MCP
implementation identity.

Static checks, Go and 40 Python tests, and full disposable PostgreSQL/race
integration passed. The real Unix API/CLI check compares separate identities
through all four profile types with an invalid client database setting. Actual
stdio MCP initialization matches the executable identity while existing tool
workflows pass. No native OpenCode adapter code changed or answering-model task
was launched.

The candidate client was also tried against the older installed API: `NOT_FOUND`
remained an error and no server identity was invented. Equal source stamps do not
prove equal executable bytes or capabilities; missing stamps do not imply clean
source. No retrieval seal, memory schema or stored record changes from diagnosis.

[Metadata](build-identity-2026-09-09.json) retains source, check and decision
identities. Doctrine packet `pkt-793ce08ff845c498` supported repository precedence,
test-first feedback, compatibility and placement outside memory authority code.
Typed evidence and the decision receipt passed schema validation; both consumption
observations closed. Four remaining interface obligations are nonmaterial because
no Go interface was introduced. Task value remains unassessed.


## Local installation

Installed clean `605be1a` as CLI/API after an operational backup with its catalog.
The running API PID 736082 matches the installed executable's hash. PostgreSQL
PID 163669, migrations 001–030, native adapter, semantic worker and connection
settings are preserved. No database migration was required.

The installed CLI and API report the same stamped build. A separate client built
with `-buildvcs=false` reports unknown local VCS state while returning the actual
stamped server identity. Fresh MCP initialization reports the facade revision.
These checks distinguish the executables; they do not claim feature negotiation
or cryptographic attestation. [Exact-source CI](https://github.com/halbritt/cairn/actions/runs/34431391693)
passed for the installed implementation.

The existing phase procedure was corrected from v1 to v2: ranked pages use schema
11, while unpaged phase requests use schema 10. It now includes the explicit
build-diagnosis commands. Exact retry and a fresh body pull matched the revised
hash, with metadata preserved. The note remains ordinary testimony. Operational
bodies and runtime probes stay under `/tmp/cairn-build-identity-deployment/`;
selected hashes and version metadata are retained in the companion JSON.
