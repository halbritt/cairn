# Weekly fixture input bridge

The weekly fixture readers in `scripts/trial_task_eval.py` accept a keyword-only
`root` for selecting a source tree. Existing calls keep the repository's
`trials/task-eval` tree. `load_cases` and `load_corpus` still accept an optional
positional base-file path; `root` independently selects their revision overlays.

```python
cases = load_cases(label_version, root=selected_root)
notes = load_corpus(label_version, root=selected_root)
frozen = verify_frozen(label_version, root=selected_root)
workspace = workspace_dir(cases[0], root=selected_root)
```

Pass the selected root and label version explicitly throughout the new input
path. Do not rebind the module's `TRIAL` global. The existing numeric overlay
order, shallow updates by ID, workspace paths and manifest digest stay intact.
Digest ordering uses `Path` components, including revision asset directories
before their corresponding JSON files. `verify_frozen` retains its original
labels-only comparison. The separate [weekly bundle loader](weekly-input-bundle-v1.md)
checks the full selected manifest identity; retained `frozen_at` metadata is not
a new label.

The separate [weekly request/1 boundary](weekly-request-v1.md) binds the original
frozen root/version, execution configuration, exact plan/schedule and reviews
for offline check/preparation. `trial_paired_request.validate` still loads
prospective input unconditionally. Weekly source preparation retains all case
wordings/private grading identities, original corpus controls and deterministic
1900 distractor identity, the common embedding declaration and the full
21-case/three-seed/two-arm schedule. Runtime execution and store import remain
unavailable. Supplemental prospective inputs keep their own semantics. The
[task-evaluation plan](plans/task-evaluation.md) defines that instrument.

The synthetic reader tests cover root/version selection, overlay cutoff,
workspace resolution, exact manifest hashing and selected-source mutation.
Existing evaluator tests cover the default fixture path. Neither reader test
results nor a frozen bundle establish native execution, provider capacity,
independent grading or either required weekly campaign's outcome.
