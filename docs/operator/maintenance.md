# Maintenance & Disaster Recovery Guide

This guide covers routine operational maintenance, schema migrations, candidate retention reclamation, and disaster recovery procedures for Cairn.

---

## 1. Schema Migrations

Cairn maintains strict database versioning through sequential SQL migrations in `core/schema/`.

### Applying Migrations

To apply pending migrations to the operational store:

```sh
cairn migrate
```

Migrations run within serial transactions. If any migration step fails, the entire transaction rolls back, preventing database corruption.

---

## 2. Routine Database Maintenance

For sustained database health, schedule periodic PostgreSQL vacuuming and query planning analysis:

```sh
# Vacuum and re-index Cairn database
sudo -u postgres psql -d cairn -c "VACUUM ANALYZE;"
```

PostgreSQL connection pooling (via PgBouncer) is recommended for installations with dozens of concurrent agents.

---

## 3. Checkpoints & Audit Verification

Cairn provides cryptographic audit checkpoints to detect tampering or accidental record loss:

### Generating a Checkpoint

```sh
cairn checkpoint < request.json
```

Where `request.json` contains:

```json
{
  "request_id": "NEW_REQUEST_UUID",
  "repo": "/home/halbritt/git/cairn"
}
```

### Verifying a Checkpoint

Verify the current audit state against an external checkpoint catalog:

```sh
cairn verify-checkpoint < expectation.json
```

Where `expectation.json` contains:

```json
{
  "repo": "/home/halbritt/git/cairn",
  "expected_digest": "SHA256_HEX_DIGEST"
}
```

---

## 4. Disaster Recovery Lifecycle

When restoring a database from backups, Cairn enforces a strict, step-by-step disaster recovery lifecycle to prevent the resurrecting of forgotten data or replay of stale credentials:

### Step 1: Export Current State

Before restoring an older snapshot, export surviving audit records:

```sh
cairn recovery-export < request.json
```

### Step 2: Restore PostgreSQL Backup

Restore your database dump using native PostgreSQL utilities:

```sh
pg_restore -d cairn backup.dump
```

### Step 3: Enter Restore Fence (`begin-restore`)

Immediately place the database into the `local-restore/1` fence:

```sh
cairn begin-restore < request.json
```

The restore fence invalidates all active index handles and pauses ordinary agent mutations.

### Step 4: Reapply Newer Revocations & Deletions

Reapply all newer retractions, deletions, and forgotten records from the recovery export:

```sh
cairn recovery-reapply < request.json
```

### Step 5: Resume Normal Operations (`resume-restore`)

Once state reconciliation is verified, exit the restore fence:

```sh
cairn resume-restore < request.json
```

Normal agent transactions and retrievals resume immediately.

---

## 5. Candidate Retention & Reclamation

During high-volume memory evaluations, diagnostic evaluated-set rows accumulate. Operators reclaim storage using candidate retention utilities:

### Inspect Candidate Retention

```sh
cairn candidate-retention < request.json
```

### Reclaim Expired Candidates

Purge expired diagnostic evaluation candidates beyond the retention window:

```sh
cairn candidate-reclaim < request.json
```

---

## Related Documentation

- [Deployment Guide](deployment.md): Systemd units and service setup.
- [Architecture Guide](../developer/architecture.md): Restore fences and receipts.
- [CLI Reference](../reference/cli.md): CLI maintenance commands.
