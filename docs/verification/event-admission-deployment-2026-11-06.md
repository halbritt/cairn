# Event admission deployment — 2026-11-06

The owner authorized deployment after implementation and the documentation audit.
The deployed executable and lifecycle sources are revision
`a3da80e9cbed758c559796b767366fa0566a710d`. This record and the accompanying
schema-074 runbook correction are documentation changes after that release.

## Installed targets

| Host | Installed components | Verification |
| --- | --- | --- |
| Proximal | Central API, scheduler, seven wake supervisors, presence watcher, Codex, Claude, Agy, Hermes and OpenCode integrations | CLI/API revision and binary digest match; seven complete runtime bundles, 13 unchanged configuration files, three enabled recall bindings and ten native adapters verified |
| Archon | Existing HTTPS relay, presence watcher and Codex integration | CLI/API revision and digest match; one runtime bundle, one unchanged configuration file and native launcher verified |
| Talos | Existing client through the Proximal socket relay, OpenCode coordination and memory integration | CLI/API revision and digest match; two runtime bundles, two unchanged configuration files and three native adapters verified |

All three installed binaries have SHA-256
`d43ca49b01aa05766ee68351b2a197d9e45bf0fda5d89695be32cfd7c9d66504`.
Each existing profile connected successfully using protocol 2. Credentials,
collection bindings, model choices and state locations were retained. No new
machine enrollment or account was created.

The inventory found no Cairn installation on Peecee WSL. The historical GCP c4
host had been deleted. Truck-pc was unreachable and had no documented Cairn
installation; Enceladus had no documented installation. These findings do not
certify the absence of an unknown installation on an uninspected host.

## Database and service cutover

Proximal's actual store was managed PostgreSQL 18.6, schema 074. An application
checkpoint and a successful pgBackRest incremental backup preceded the upgrade.
The previous executable and installation files were retained outside the repository.
The retired private-store bootstrap and backup scripts were not used.

The scheduler, wake supervisors, presence watcher and relevant timers were stopped,
then the API was stopped. A database inspection found no remaining application
connections before migrations 075–084 ran. The read-only PostgreSQL exporter was
the sole remaining external connection. Migration succeeded and the upgraded API
and services restarted. A subsequently started `cairn-workview.service` also used
the verified current executable; future maintenance inventories must include it.

Closure reconciliation completed in the current restore generation. Five legacy
reservations were recorded. Policy, pressure, closure and refusal views responded
successfully. Admission remains at its absent-policy default: disabled, revision 0.
The running release reports enforcement readiness; no production limit values or
enforcement policy were selected during deployment.

## Integration and client verification

Before replacing bound runtime files, all three inbox-recall bindings were disabled.
There were no active Python lifecycle callbacks or unfinished native inbox attempts
at the recorded drain boundary. Complete bundles were staged, configuration bytes
restored exactly, pins regenerated and checked, and original activation states
restored. The old retained release remained intact. Permissions were tightened only
on identified installation targets that had group or other write access.

Independent installed-state checks passed on all three hosts. They checked bundle
members and aliases against the selected source, binding pins and activation,
configuration hashes, native adapters, hook targets, sidecars, binary identity and
the authenticated API response. The effective-recall checker reported complete
coverage with no issues and no disabled bindings.

All five Proximal Codex servers and the Archon Codex server accepted supported MCP
configuration reloads without losing loaded conversations. Twelve Proximal
conversations and one Archon conversation were retained. Their Cairn connections
reported the deployed revision. An ignored project configuration on Proximal had
pinned an older executable; its Cairn command was updated along with the account
configuration commands, preserving other configuration values.

Three live Claude conversations reconnected their Cairn memory and channel servers
through the supported native commands. Two Agy conversations restarted only their
Cairn server through the native MCP menu. The Hermes gateway reported no active work;
its Cairn connection was reconciled separately, with its exact configuration bytes
restored afterward. No native conversation was replaced. The installed Hermes
adapter files were unchanged from their previous contents; its lifecycle runtime
bundle and MCP executable were updated.

The final Proximal process snapshot found 31 running Cairn executables, all matching
the deployed digest. This checks actual running process images, including retained
MCP and Claude channel clients, separately from installed files. Client refresh
results and conversation continuity were recorded outside Git.

## Exact upgrade rehearsal

An isolated PostgreSQL cluster exercised the actual deployed `d2cccca` executable
at schema 074 and the new release at schema 084. Migrations and their checksums,
retained application data, migration idempotence, feature defaults and a successful
new-executable write were verified. Both disposable clusters were stopped and
removed. Production data was not used for tests.

The rehearsal found an important limit: the schema-074 executable refused an old
`migrate` command against schema 084 but still accepted an ordinary write. It predates
the compatibility checks in the previously tested schema-079 baseline. The
[rollout runbook](../operator/event-admission-rollout.md#1-upgrade-the-store) and
[maintenance guide](../operator/maintenance.md#upgrade-the-schema) now distinguish
these cases. Stopping all old direct-store processes is mandatory.

The earlier full integration, lifecycle and native restore verification remains
the implementation evidence. This rollout adds installed-state and exact deployed
upgrade evidence; it does not establish measured production usefulness or suitable
admission limits. The [documentation audit](event-admission-docs-audit-2026-11-06.md)
records the preceding source documentation checks.

Selected operational receipts, backups, checksums and installation journals are
retained outside Git. No credentials, database dumps, raw sessions or generated
binaries are included in this record.
