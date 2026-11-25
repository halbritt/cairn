# 0023 — The repository holds current contracts and decisions, not run records

Status: purge requested by the owner on 2026-11-25. The rule for new material
below is the recommendation that follows from it; it is in force unless the
owner changes it.

## Decision

The repository documents what Cairn does now, what is planned and why a
direction was chosen. Records of a past run, review or deployment are not kept
in the tree.

On 2026-11-25 the dated verification reports, the archived implementation and
roadmap logs, the JSON decision receipts and the plans for shipped work were
removed. Their decisions are records 0004 to 0017. The last commit that holds
the removed files is `fe5743f8`; read them with `git show fe5743f8:<path>`.

## Where material goes

| Material | Destination |
| --- | --- |
| Current behavior, setup and failure paths | Guides and references under `docs/` |
| Direction and remaining work | [Roadmap](../roadmap.md) and Plane |
| A design for open work | `docs/plans/`, removed when the work ships |
| A choice between options and its reason | A record in this directory |
| The result of a test run, review or deployment | The Plane work item and the commit message |

## Exceptions that remain

A dated record stays while tracked code reads it or an open campaign cites it.
The remaining files under `docs/verification/` are the CAIRN-301 release
evidence and the sources cited by the weekly evaluation (CAIRN-107). Remove
them when that work closes.
