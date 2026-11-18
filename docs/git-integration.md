# Trunk integration and development evidence

`git-land` rebases the current branch onto the repository's trunk, runs the
supplied check, and pushes the candidate. It takes the shared repository lock,
fetches and rebases again, and repeats the check if that rebase changed the
candidate. A successful secondary-worktree landing updates the primary checkout
and removes the owned branch/worktree unless `--keep-worktree` was requested.
The implementation is [git_trunk_tools.py](../scripts/git_trunk_tools.py).

The tree must be clean, including untracked files even when Git's display
configuration hides them. Checks must leave both tracked/untracked state and
HEAD unchanged. If a check creates source changes or a commit, landing stops
before push and preserves the worktree. Inspect the changes and start a new
landing attempt on the intended candidate. Ignored build outputs remain governed
by the project's checks; declare material generated files in exchanged evidence.

## Advisory evidence comparison

Pass an existing development-evidence receipt when a review or verification result
will be reused at integration:

```sh
git-land --check 'mise run test' --evidence /absolute/path/to/review.json
```

`--evidence` is repeatable. Relative paths are resolved from the target repository
root, including with `--cwd`. Keep receipts and their logs outside disposable
worktrees when they must survive cleanup. With no `--evidence`, no comparator is
invoked and no evidence requirement is added.

After the final rebase and checks, under the existing integration lock and before
push, git-land calls the installed `development-evidence compare` with the
actual repository, resolved trunk base, and `--json`. It then checks HEAD and
cleanliness again. The comparator is a bounded, read-only deterministic command;
git-land never launches a reviewer or executes commands from a receipt.

The shared tool lives in `infra/shared/development`; its README defines the
record/inspect/compare CLI and versioned evidence format. The `verify-behavior`
skill owns the behavioral interpretation. Neither a content hash nor a
successful comparison establishes that a check covered the requested behavior.

The final JSON result includes an `evidence` array, and stderr displays a brief
advisory for each receipt. Freshness is `current`, `stale`, `missing`, or `unknown`;
the recorded review/check outcome remains separate. A current result can have
open findings. A previously clear result can be stale after a rebase or material
input change.

Stale, missing, malformed, unavailable, or timed-out evidence does not block this
advisory integration. A missing comparator, an unsupported response schema or an
inconsistent exit/status is reported as unknown, never current. Existing required
checks and source hygiene still block on their own failures. This command provides
no enforcement mode and does not grant permission to ignore project requirements.

The lock serializes cooperating git-land callers. It cannot prevent an unrelated
process that ignores the lock from editing files. The checks before and after
comparison detect observed changes; they are not a filesystem transaction.

## Verification

```sh
mise run test:unit -- test_git_trunk_tools -v
```

Disposable Git repositories cover rebasing, push/cleanup, checks that change HEAD
or leave files behind, a concurrent trunk update during checking, and comparison
after the final check under the integration lock. Comparator protocol tests use
a small executable at that external boundary; the evidence tool's own tests own
record validation and fingerprint semantics. A deployed-tool demonstration checks
the combined path separately.
