# Interpreting documentation checks

A passing documentation check means the selected mechanical checks found no
violations in that checkout. It does not establish that every example runs,
that the documented behavior is implemented, or that a deployment is ready.

## Obtain a current result

```sh
python3 scripts/verify_docs.py
python3 scripts/verify_docs.py --json
```

Record the revision, command, and exit status when reporting validation. Document
counts and failure counts change as the repository changes; use the report from
the checkout being reviewed instead of a copied baseline.

## Resolve a failure

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
