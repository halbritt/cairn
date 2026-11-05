# Cairn Work Coordination Test Runner & Acceptance Verification

This document provides the authoritative guide for executing, interpreting, and
verifying the Cairn test suites, covering the Work Coordination subsystem
(acceptance checks AC-01 through AC-45 across slices WC-01 through WC-06)
and standing documentation verification.

---

## 1. Test Runner Invocations

### Full Integration Acceptance Run
To execute all test suites against a freshly provisioned, disposable PostgreSQL cluster:
```sh
# Full integration standard: must run WITHOUT CAIRN_TEST_ONLY
make test-integration
```
This executes all Go packages and Python fixtures in disposable PostgreSQL databases.
Tests execute against the newly built candidate binary.

### Work Coordination E2E Suite Invocations
To run the Work Coordination E2E test suite specifically:
```sh
# Run all 45 acceptance checks across all 4 tiers:
go test -v ./tests -run TestWorkCoordination

# Run a specific tier:
go test -v ./tests -run TestWorkCoordination/Tier1_FeatureCoverage
go test -v ./tests -run TestWorkCoordination/Tier2_BoundariesAndCorners
go test -v ./tests -run TestWorkCoordination/Tier3_CrossFeatureInteractions
go test -v ./tests -run TestWorkCoordination/Tier4_RealWorldWorkloadScenarios

# Run a specific acceptance check:
go test -v ./tests -run TestAC01_ProjectIdentityDistinctByUUIDTuple
```

### Development Milestone Subsets
During active milestone development before all slices are landed on main, workers
may target an isolated milestone using `CAIRN_E2E_SUBSET`:
```sh
# Run only WC-01 tests (skipping unselected milestones):
CAIRN_E2E_SUBSET=WC-01 go test -v ./tests -run TestWorkCoordination

# Run WC-01 and WC-02 tests:
CAIRN_E2E_SUBSET=WC-01,WC-02 go test -v ./tests -run TestWorkCoordination
```
In full acceptance runs (when `CAIRN_E2E_SUBSET` is unset), missing or regressed
subcommands **fail the test suite immediately** with `RELEASE/ACCEPTANCE FAILURE`.
Omitted milestones are never counted as passed.

### Running with External CLI Scripted Fixtures
To verify failure modes deterministically:
```sh
# Plane CLI modes: normal, too_large, output_overflow, timeout, auth_failure, config_mismatch
CAIRN_PLANE_BIN=./tests/fixtures/mock_plane.sh MOCK_PLANE_MODE=too_large go test -v ./tests -run TestAC06

# Claude CLI modes: normal, empty, invalid, arbitrary_url, unknown_action, quota_exhausted, auth_failure, timeout
CAIRN_CLAUDE_BIN=./tests/fixtures/mock_claude.sh MOCK_CLAUDE_MODE=quota_exhausted go test -v ./tests -run TestAC41
```

---

## 2. Acceptance Checks Coverage Matrix

The 45 acceptance checks from planning commit `11d06bb35fa1b5af5529cb950939eab523f44426`
are organized into four tiers:

| Check | Slice | Tier | Test Function | Description |
|---|---|---|---|---|
| AC-01 | WC-01 | Tier 1 | `TestAC01_ProjectIdentityDistinctByUUIDTuple` | Two projects distinct by `(origin, ws, proj, config_rev)` tuple |
| AC-02 | WC-01 | Tier 2 | `TestAC02_RepointingPlaneInstanceRefusesRefresh` | Repointed Plane profile refuses refresh (`CONFIG_MISMATCH`) |
| AC-03 | WC-01 | Tier 1 | `TestAC03_HistoricalExecutionIsolation` | Multiple execution links; historical isolation on resume |
| AC-04 | WC-01 | Tier 2 | `TestAC04_OperatorSeparationLocalProfileDenied` | Local agent profiles denied operator work mutations (`AUTHORITY_DENIED`) |
| AC-05 | WC-01 | Tier 1 | `TestAC05_LinkedRequestsDisplayStatusSeparately` | Requests display pending/leased/handled separate from Plane |
| AC-06 | WC-01 | Tier 2 | `TestAC06_FullRefreshReplacesCacheGeneration` | Cache swap; >10k items rejected (`PROJECT_TOO_LARGE`) |
| AC-07 | WC-01 | Tier 2 | `TestAC07_DuplicateMutationsIdempotencyAndConflicts` | Idempotent duplicate retry; conflict on changed intent |
| AC-08 | WC-01 | Tier 2 | `TestAC08_RefreshFixturesRejectMutatingCommands` | Read-only connector rejects mutating commands (`MUTATION_FORBIDDEN`) |
| AC-09 | WC-01 | Tier 2 | `TestAC09_PurgeRemovesTextAndExclusionPersists` | Metadata purge; exclusion rule persists across restore |
| AC-10 | WC-01 | Tier 2 | `TestAC10_ConcurrentPurgeWinsOverStagedMetadata` | Concurrent purge/exclusion revision barrier |
| AC-11 | WC-01 | Tier 2 | `TestAC11_BrowserUIPresentationAndSecurity` | `work serve` loopback security, CSRF, and launch token |
| AC-12 | WC-02 | Tier 1 | `TestAC12_ManyArtifactLinksAndRolesIdempotent` | Many-to-many artifact links with independent roles |
| AC-13 | WC-02 | Tier 2 | `TestAC13_RevisionImmutabilityAndUnverifiedLocators` | Revision immutability; unverified locators labelled |
| AC-14 | WC-02 | Tier 1 | `TestAC14_ReverseImpactPaginationAndIdentitySeparation` | Paged reverse lookup of work items by artifact |
| AC-15 | WC-02 | Tier 2 | `TestAC15_SourceMaskingAndUnlinkPurgeDistinction` | Unavailable source masked; unlink vs purge distinction |
| AC-16 | WC-02 | Tier 2 | `TestAC16_MaliciousSchemesAndCredentialsRefused` | Malicious schemes (`javascript:`, `file:`, credentials) refused |
| AC-17 | WC-02 | Tier 1 | `TestAC17_CLIAndBrowserArtifactNavigation` | CLI and browser artifact navigation |
| AC-18 | WC-03 | Tier 1 | `TestAC18_ResponseBeforeRegistrationSatisfies` | Response arrival before wait registration satisfies |
| AC-19 | WC-03 | Tier 2 | `TestAC19_FailureReportSatisfiesWithoutTaskSuccess` | Failure report satisfies wait without task success |
| AC-20 | WC-03 | Tier 2 | `TestAC20_TimestampOrderingDeterminism` | Database timestamp ordering for races |
| AC-21 | WC-03 | Tier 2 | `TestAC21_CrashRecoveryToSingleNotice` | Crash between resolution and outbox drain publishes 1 notice |
| AC-22 | WC-03 | Tier 1 | `TestAC22_NativeRecipientDeliveryAndAck` | Native recipient delivery and explicit acknowledgment |
| AC-23 | WC-03 | Tier 2 | `TestAC23_ResumedConversationInspectCancel` | Resumed conversation can inspect/cancel active wait |
| AC-24 | WC-03 | Tier 2 | `TestAC24_RestoreFencingOfWaits` | Restore fences waiting and unacknowledged notices |
| AC-25 | WC-03 | Tier 3 | `TestAC25_PersistentAdmissionFenceOnRestore` | Persistent admission fence blocks published unclaimed notice |
| AC-26 | WC-03 | Tier 2 | `TestAC26_DestinationCeilingRevalidation` | Revoked binding / widened profile cannot breach ceiling |
| AC-27 | WC-03 | Tier 2 | `TestAC27_MaintenanceContinuityAndOverdueState` | Safe disable continues maintenance; get reports overdue |
| AC-28 | WC-04 | Tier 1 | `TestAC28_ZeroIdleWakesIsolatedQueue` | Queued quiet notices never appear as ready work |
| AC-29 | WC-04 | Tier 2 | `TestAC29_OriginGatingSupportedTurnAdmits` | Native user turn admits; background/wake prompt cannot |
| AC-30 | WC-04 | Tier 2 | `TestAC30_RootStabilityAndBurstLimit` | Root admission stable; maximum 3 quiet notices per turn |
| AC-31 | WC-04 | Tier 2 | `TestAC31_ContextBudgetAndNonAcknowledgment` | Context budget respected; rendering is not acknowledgment |
| AC-32 | WC-04 | Tier 2 | `TestAC32_RaceResolutionAndQueueCap` | Claim/flush/expiry races create 1 delivery; queue cap enforced |
| AC-33 | WC-04 | Tier 2 | `TestAC33_HoldPreservationOnCrash` | Crash preserves native attempt hold; does not requeue |
| AC-34 | WC-04 | Tier 2 | `TestAC34_ExecutionBindingAndRestoreFencing` | New execution admits; stale attempts cannot; restore fence |
| AC-35 | WC-04 | Tier 2 | `TestAC35_UnsupportedBindingsRefuseAndSafeDisable` | Unsupported bindings (Agy) refuse (`UNSUPPORTED_DELIVERY`) |
| AC-36 | WC-05 | Tier 1 | `TestAC36_ReviewApplyUndoLoop` | Full advice review, atomic apply, and undo loop |
| AC-37 | WC-05 | Tier 2 | `TestAC37_ModelOutputRejectionAndZeroMutations` | Invalid model output rejected with zero mutations (`INVALID_PROPOSAL`) |
| AC-38 | WC-05 | Tier 2 | `TestAC38_HostedEligibilityAndSourcePrivacy` | Ineligible sources refuse before model invocation (`SOURCE_INELIGIBLE`) |
| AC-39 | WC-05 | Tier 2 | `TestAC39_AtomicSubsetApplyAndConflictCheck` | Atomic subset apply; duplicate retry returns receipt |
| AC-40 | WC-05 | Tier 2 | `TestAC40_BlockerCreationAndResolutionUndoLogic` | Undo blocker creation/resolution; conflict check (`UNDO_CONFLICT`) |
| AC-41 | WC-05 | Tier 2 | `TestAC41_QuotaAuthFailStopZeroFallback` | Quota/auth failure stops immediately (`QUOTA_EXHAUSTED`, zero fallback) |
| AC-42 | WC-05 | Tier 2 | `TestAC42_ProcessGroupCleanupOnTimeout` | Model timeout terminates process group; restart reconciles |
| AC-43 | WC-05 | Tier 2 | `TestAC43_RetentionLimitsAndCleanupContinuity` | Retention limits and purge cleanup work with generation disabled |
| AC-44 | WC-06 | Tier 4 | `TestAC44_EndToEndMultiFeatureUserJourney` | Multi-agent journey across two sessions, card, waits, quiet notices |
| AC-45 | WC-06 | Tier 4 | `TestAC45_FailureInjectionAndReverseRetirement` | Fault injection, reverse retirement order, purge reapplication |

---

## 3. Database Isolation Safety Guarantees

All integration tests strictly satisfy the repository's database safety invariants:
- **Disposable Clusters Only**: Integration runs use `scripts/test-postgres.sh` which executes `initdb` and `pg_ctl` to spin up a single-use cluster under `/tmp/cairn-postgres.*`.
- **Zero Production Database Access**: Tests NEVER point to or connect to production databases or `~/.config/cairn/`. If `CAIRN_TEST_DATABASE_URL` is unset, database-dependent tests skip rather than touching default local databases.
- **Per-Package Database Isolation**: `scripts/test-postgres.sh` provisions separate databases (`cairn_package_N`) per Go package to prevent serializable retry exhaustion and crosstalk.

---

## 4. Interpreting Documentation Checks

A passing documentation check means the selected mechanical checks found no
violations in that checkout. It does not establish that every example runs,
that the documented behavior is implemented, or that a deployment is ready.

### Obtain a Current Result

```sh
python3 scripts/verify_docs.py
python3 scripts/verify_docs.py --json
```

Record the revision, command, and exit status when reporting validation. Document
counts and failure counts change as the repository changes; use the report from
the checkout being reviewed instead of a copied baseline.

### Resolve a Failure

For a broken link, check the target and heading before changing either. For a
CLI mismatch, inspect the actual parser and command help: the checker can miss
invalid combinations and can misread explanatory syntax. For JSON or shell
syntax failures, distinguish an executable example from a labelled fragment.
For missing guides, add useful reader-facing content rather than filling a
length threshold.

Do not weaken the checker to make inaccurate documentation pass. If a heuristic
is wrong, fix or document that limitation separately and still verify the example
against its implementation.

[Documentation validation](TEST_INFRA.md) describes the four tiers and their
limits. [Developer testing](docs/developer/testing.md) covers executable tests;
[historical verification](docs/verification/README.md) explains the evidence
retained from earlier runs.
