# Private instruction applicability verification

Hosted compilation previously returned `POLICY_UNENFORCEABLE` for a private
mandatory instruction pinned to revision A even when the run supplied revision B.
The destination check returned before evaluating applicability. A disposable
PostgreSQL regression reproduced this refusal before the repair.

The compiler now excludes known out-of-validity or mismatched private candidates
before testing mandatory destination requirements. It retains the existing
fail-closed behavior for applicable instructions and missing context. The repair
does not change the currentness predicate, introduce a waiver, change schemas or
alter local compilation.

Public Store API tests establish:

- Revision, workspace, task-class, binding and capability mismatches permit
  hosted compilation while preserving shareable retrieval.
- Expired and future-effective instructions do not block hosted compilation,
  including an expired instruction whose context constraint was not supplied.
- Matching, unconstrained and missing applicability still return
  `POLICY_UNENFORCEABLE`, including mandatory runtime instructions.
- Normal packages and compact indexes contain no private instruction body or ID;
  their omission counts and candidate explanations reveal no private candidate.
- Historical recompilation reproduces the successful packages' seals.

Focused tests first failed on the revision mismatch and passed after the repair.
UTC `make test-integration` with the race detector and `make check` passed. These
checks establish the declared applicability/destination boundary, not physical
workspace attestation, enforcement by a model or measured task usefulness.

## Local installation

Commit `a721e94b7355622d841d01e6106832f96a5c4795` passed
[CI](https://github.com/halbritt/cairn/actions/runs/34211283915) and was built
from a clean clone with Go 1.25.0. The installed executable and running API both
have SHA-256
`b420da5df10ff2c158fd62b5a4598d7e2e06ae79c62a10e78bd63e62a2bd4f16`.
The schema remains 022. Both services are active, an authenticated existing-record
read passed, and the record-version digest remained unchanged. The pre-install
backup and previous binary are retained locally; no operational policy was
created or changed to verify the repair.
