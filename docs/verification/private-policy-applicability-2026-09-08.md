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
