# Developer Testing Guide

Cairn emphasizes rigorous local automated testing. Tests validate transactional invariants, API contract integrity, documentation freshness, and disaster recovery drills.

---

## 1. Local Testing Philosophy

- **No Remote CI Dependency**: Cairn uses local validation on Linux. The project deliberately does not use GitHub Actions workflows for testing (owner decision, 2026-09-16).
- **Disposable PostgreSQL Clusters**: Tests never touch production or operational databases. Integration suites spin up ephemeral, disposable PostgreSQL clusters on loopback sockets.
- **Strict Verification Gates**: All checks must pass cleanly before trunk integration.

---

## 2. Test Commands & Suites

### Unit Tests

Run unit tests across all packages:

```sh
go test ./...
```

Unit tests cover memory ranking, token counting, JSON Unicode integrity, command flag parsing, and MCP tool formatting.

### Static Analysis & Contract Verification (`make check`)

Run linting, code formatting, API contract checking, and design source validation:

```sh
make check
```

This target runs:
1. `go vet ./...`: Standard Go static analysis.
2. `go run ./internal/apicontract/cmd/cairn-contract -check`: Verifies parity between Go struct definitions and REST/localapi operation specifications.
3. `gofmt -l`: Checks Go formatting.
4. `python3 -B scripts/check_design_sources.py`: Ensures pinned historical design documents have not been modified.

### Integration Tests (`make test-integration`)

Run database integration tests against disposable PostgreSQL clusters:

```sh
make test-integration
```

This suite executes:
- Transactional rollback and CAS concurrency.
- Schema migration replays (001 through 074).
- MCP stdio server interaction and tool execution.
- Agent session registration, heartbeats, and durable inbox delivery.

### Lifecycle & Recovery Drills (`make test-lifecycle`)

Execute backup, checkpoint, and disaster recovery validation:

```sh
make test-lifecycle
```

Verifies restore fencing, candidate retention reclamation, and out-of-procedure restore detection.

---

## 3. Documentation Verification (`scripts/verify_docs.py`)

Cairn includes an automated four-tier documentation testing suite:

```sh
# Run all verification tiers
python3 scripts/verify_docs.py

# Run specific tier(s)
python3 scripts/verify_docs.py --tier 1,2,3
python3 scripts/verify_docs.py --tier 4
```

| Tier | Purpose | Target Scope |
|------|---------|--------------|
| **Tier 1** | Link & Anchor Integrity | Resolves all internal markdown links and heading slug anchors |
| **Tier 2** | CLI Flag Alignment | Compares documented CLI invocations against Go source flag sets |
| **Tier 3** | Code Block Syntax | Validates fenced JSON blocks with `json.loads` and shell blocks with `bash -n` |
| **Tier 4** | Document Completeness | Enforces presence and substance of all 25 architecture documents |

---

## Related Documentation

- [Architecture Guide](architecture.md): Code layout and system concepts.
- [Contributing Guide](contributing.md): Contribution guidelines and trunk integration.
- [Operator Maintenance](../operator/maintenance.md): Production backup and recovery.
