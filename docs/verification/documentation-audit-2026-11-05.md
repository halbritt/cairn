# Public documentation audit, 2026-11-05

This audit rewrote Cairn's public documentation around project purpose and reader
tasks, then checked setup examples and technical claims against the source.
The starting revision was `b7a7cf3`. No application code, operational data,
credentials, or running service configuration changed.

## Scope and organization

The review covered the root documentation, introductory and operator guides,
client setup, developer references, and top-level feature documentation. Existing
experiment guides were checked for wording that presented a frozen trial as
current behavior. Trial workspace READMEs remain evaluation inputs.

The README now explains persistent agent memory and coordination before
implementation details. The [documentation index](../README.md) provides paths
for users, operators, and developers and links every top-level feature reference.
The [writing guide](../developer/documentation.md) records the public-facing voice
and verification expectations.

Chronological status, roadmap, Striatum integration, and transport measurement
material moved to [history](../history/README.md), with relative links preserved.
Current status and roadmap pages describe capabilities and remaining acceptance
work. Pinned design source bytes and dated experimental results remain intact;
historical records retain their original scope instead of becoming installation
instructions.

## Material corrections

| Earlier documentation problem | Correction and implementation source |
| --- | --- |
| Readers were assumed to use an existing personal installation | Added complete local setup, profile provisioning, authenticated capture/search/pull, and generic collection/path examples; checked against `cmd/cairn/agent.go`, `cmd/cairn/serve.go`, and `localapi/server.go` |
| Go 1.22 and PostgreSQL 15 were presented as prerequisites | Build requirement follows [go.mod](../../go.mod); the quickstart targets PostgreSQL 17, used by the local lifecycle workflow |
| Identity examples used nonexistent roles or configuration shapes | Documented the identity array and `agent`/`observer` roles from [authentication](../../localapi/server.go); administrative operations remain a separate local store interface |
| REST routes and refusal statuses were invented or incorrect | Replaced them with routes from the [operation catalog](../../localapi/operations.go) and statuses from [refusal classification](../../internal/refusal/classification.go) |
| CLI edits and MCP edits were described as interchangeable | Documented their distinct request shapes and linked operation help |
| Inspection allocation was described as automatically pulling sources | Explained reservation and explicit pulls; checked MCP and core inspection behavior |
| The default search room was described as the maximum | Distinguished the default from the configured explicit-request maximum in [search room](../search-room.md) |
| Lifecycle helpers were described as universal enforcement | Replaced the invented slice catalog with a [capability map](../reference/lifecycle-slices.md) based on the Go methods and their caller obligations |
| A local-store command was shown as directly executable | Use `bash scripts/local-store.sh`; the script is not executable in the checkout |
| Current guides embedded installation inventories and milestone narratives | Replaced them with task instructions, current contracts, or labeled historical links |

## Validation and limits

Validation covers documentation, source consistency, and the exercised examples.
It does not accept the full design, establish general task benefit, or certify
all integrations on every client version. Historical provider experiments and
production deployments were not repeated for this documentation change.

The checked-in documentation verifier checks links, command/flag forms, fenced
sample syntax, and expected documentation surfaces. These are mechanical checks;
they cannot establish that a command's explanation or an enforcement claim is
correct. Source review and the disposable quickstart smoke test supplement them.

Pinned design snapshots are checked by `make check`. Archive links retain legacy
anchors where heading punctuation produces different slugs across renderers.

Validation completed:

- `make build` and `make check` passed.
- `python3 scripts/verify_docs.py --tier 1,2,3,4` passed: local links, command
  forms, code-block syntax, and required guide presence.
- `git diff --check` passed; the four pinned design snapshots retain their hashes.
- A disposable `CAIRN_HOME` smoke test used the profile script extracted from the
  quickstart, started PostgreSQL and applied migrations, ran the documented
  authenticated remember/search commands, and executed the returned pull command.
  CLI/API version queries also succeeded. The API and cluster were stopped and
  scratch state removed.
- Generated Codex TOML and Claude/OpenCode JSON parsed successfully. The native
  OpenCode installer also completed against a disposable configuration.

No live model requests or production-store validation were needed for these
checks. Runtime behavior outside the exercised workflow was checked by reading
its source and contracts, not by rerunning every historical integration trial.
