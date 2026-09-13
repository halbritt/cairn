# Standalone task-failure review

The reviewed repo-only recurrence M was rejected after its candidate failed an
inherited-environment check. Cairn previously generated proposals only when a
matching later acceptance existed, leaving this failure without a review item.
This change extends D1 through the existing operator review workflow.

## Implementation and checks

Migration 019 permits a proposal without a recovery source. The failure receipt,
assessment version and selected evidence remain attached. A partial unique index
prevents repeated or concurrent generation from duplicating that source.
`TASK_FAILURE` docket items support the existing versioned review dispositions.
A current richer pair takes precedence without altering standalone history;
assessment corrections can restore the still-current standalone demand.

The lifecycle test fails against the prior implementation with “standalone
failure missing” and passes against this change. `make test-integration check`
passes with disposable PostgreSQL and the race detector. Checks cover concurrent
generation, defer/reopen/dismiss/convert, correction handling, pair precedence,
legacy pair JSON, and the existing authority/evidence protections. Conversion
links an ordinary A record without promoting it.

## Actual trial replay

The [machine-readable result](standalone-demand-2026-09-08.json) records a
disposable restore of M's reviewed dump. Its original assessment version 2 lacked
an error signature and correctly produced no proposal. In the restored copy,
the operator selected a digest of the verified inherited-environment failure
description and retained the same evidence in assessment version 3.

Generation produced one current standalone proposal. A second request reused
its ID. The attached evidence was readable, the item appeared on the docket,
and dismissal removed it. The run still had zero memory-use rows. Original
trial reports, reviewed assessments and dump retained their hashes. The
disposable cluster was stopped and its verification dump retained locally.

This verifies a review path for an observed failure. It does not establish
novelty, reduced review burden, a reusable fix, or broader task acceptance.
Cross-task signature clustering and measured next-run improvement remain open.
No automatic grooming, promotion or ranking change is introduced.

## Local installation

Clean commit `7bc9165b0b58dc46b0bc5021e62ea276c3c0affb` passed
[CI](https://github.com/halbritt/cairn/actions/runs/34187744968) and the local
backup/restore lifecycle drill. After a private operational backup, migration
019 and the clean binary were installed. The API process uses the same binary,
authenticated record retrieval succeeded, and the retained record-version
digest is unchanged. Installation metadata is included in the JSON result.
