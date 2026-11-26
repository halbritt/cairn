# Trunk integration and development evidence

`git-land` rebases the current branch onto the repository's trunk, runs the
supplied check, and pushes the candidate. It takes the shared repository lock,
fetches and rebases again, and repeats the check if that rebase changed the
candidate. It then brings local trunk up to the landed commit where git can do
that safely (see [Local trunk after a landing](#local-trunk-after-a-landing)).
A landing from a secondary worktree then removes that worktree and its branch
unless `--keep-worktree` was requested.
The implementation is [git_trunk_tools.py](../scripts/git_trunk_tools.py).

The tree must be clean, including untracked files even when Git's display
configuration hides them. Checks must leave both tracked/untracked state and
HEAD unchanged. If a check creates source changes or a commit, landing stops
before push and preserves the worktree. Inspect the changes and start a new
landing attempt on the intended candidate. Ignored build outputs remain governed
by the project's checks; declare material generated files in exchanged evidence.

## Local trunk after a landing

Agents share checkouts, so the checkout that has trunk checked out can hold
another agent's uncommitted work when a landing finishes. git-land never moves
a branch from underneath a checkout. Moving only the ref leaves that checkout's
index and files at the old commit: `git status` there shows the landed changes
as staged reversals and the landed files as deleted, and the next commit from
it reverts the landed work without an error.

Local trunk only moves forward. The JSON result reports what happened in
`trunk_status`, with the worktree that has trunk checked out in
`trunk_worktree` and one explanatory sentence in `trunk_detail`. Stderr prints
the same sentence.

| `trunk_status` | Meaning |
| --- | --- |
| `fast-forwarded` | Git fast-forwarded the worktree that has trunk checked out: ref, index and files together. Uncommitted work there that does not overlap the landed commit is kept. git-land finds that worktree in `git worktree list`; it is usually the primary checkout and can be a secondary worktree. |
| `ref-updated` | No worktree has trunk checked out, and the branch was moved to the landed commit. |
| `current` | Local trunk already contained the landed commit, for example after landing from the trunk checkout itself. |
| `left-behind` | Local trunk was not moved. |

Trunk is left behind when it cannot be moved without loss:

- Git refused the fast-forward. It refuses when the landed commit would
  overwrite uncommitted, untracked or ignored files in the trunk checkout.
  `trunk_detail` carries git's message, which names the files.
- The fast-forward would write back a file that was deleted or renamed in the
  trunk checkout without a commit. Git does not refuse that, so git-land checks
  for it first.
- The trunk checkout is not in an ordinary state. A merge, cherry-pick, revert,
  rebase or `git am` is unfinished there; it uses sparse-checkout, where git
  overwrites untracked files instead of refusing; trunk is checked out in more
  than one worktree; or the registered directory is no longer that checkout.
- Local trunk has commits that the landed commit does not contain. Moving it
  would drop them.
- A worktree is rebasing or bisecting trunk. `git worktree list` shows that
  worktree as detached, and git refuses to move the branch.

Any other failure of the git command that moves trunk is reported the same way.
git-land reads the outcome back from the repository instead of trusting git's
exit status. It also puts back `ORIG_HEAD` in the trunk checkout, which git
sets for every merge it attempts, so a refusal leaves the checkout as it was.
If a fast-forward fails after git has started to write, for example on a file
it cannot replace, `trunk_detail` says that the checkout was changed and needs
inspection.

After a push, a left-behind trunk does not undo the landing: `origin` has the
commit, and the landed branch and worktree are removed as usual. Local trunk
lacks the commit, which git reports in the ordinary way: a push from that
checkout is rejected until it has merged the landed commit, and `git status`
shows the branch as behind when it tracks `origin`. To catch up, whoever owns
the work in that checkout commits it and runs `git pull --rebase origin main`
there. An untracked or ignored file that `trunk_detail` names has to be moved
aside first: `git pull` stops at the untracked file and overwrites the ignored
one. Do not move trunk from outside its checkout with `git update-ref`,
`git branch -f`, `git reset` or `git checkout -B`, and do not stash or discard
another agent's files.

The Stop hook and `git-wt gc` count a commit as landed when trunk on `origin`
has it, so a left-behind local trunk does not make landed work look unmerged.

git-land deletes the landed branch and worktree only when another ref holds the
landed commit: trunk on `origin`, or local trunk. With `--no-push` or without
an `origin` remote, a left-behind trunk leaves the commit on neither, so nothing
was landed. git-land then exits with an error and keeps the branch and
worktree. Run it again once local trunk can fast-forward.

Landing a branch from the primary checkout follows the same rules. The checkout
returns to trunk and the branch is deleted only when no other worktree has
trunk checked out and trunk was not left behind. Otherwise the checkout stays on
the landed branch.

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
after the final check under the integration lock. For the trunk checkout they
cover uncommitted work that does and does not overlap the landed commit, a
commit and push from a left-behind checkout, ignored files, uncommitted
deletions and renames, the autostash and merge-option settings, sparse-checkout,
an unfinished revert, a fast-forward that fails part-way, trunk held by a
secondary worktree or by two worktrees, a stale or commit-less trunk checkout,
trunk checked out nowhere or being rebased, unpushed trunk commits, a refused
fast-forward with no push, and the hygiene and gc view of a left-behind trunk.
Comparator protocol tests use
a small executable at that external boundary; the evidence tool's own tests own
record validation and fingerprint semantics.
