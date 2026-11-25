# Dated records that remain

The repository no longer keeps records of past runs, reviews and deployments;
see [decision 0023](../decisions/0023-repository-holds-current-contracts.md).
The files here stay for one of three reasons:

- Tracked code names the file: the CAIRN-301 release checks under `scripts/`
  list their record, and the weekly evaluation (CAIRN-107) cites its sources.
- The file is release evidence for the open hybrid runtime campaign (the
  `r0N-`, `f0N-` and November 2026 records).
- An open plan under [plans](../plans/README.md) cites it.

Each file describes what one run established when it was written. It is not a
live status page. Paths under `/tmp` and digests of logs identify artifacts on
the original host; they do not make those bytes available.

Remove these files when the work that cites them closes. Earlier records are
in Git history: `git show fe5743f8:docs/verification/<name>`.
