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

This reader change does not dispatch paired requests to a weekly loader.
`trial_paired_request.validate` still loads prospective input unconditionally.
The next integration must explicitly bind the original frozen root/version,
execution configuration and reviews, preserve all case wordings and private
grading assets, and select only the task wording for actor attempts. It must
retain the original corpus controls and deterministic 1900 distractors, the
same embedding route in both stores, and all 21 cases, three selected seeds and
two arms. Supplemental prospective inputs keep their own semantics. The
[task-evaluation plan](plans/task-evaluation.md) defines that instrument.

The synthetic reader tests cover root/version selection, overlay cutoff,
workspace resolution, exact manifest hashing and selected-source mutation.
Existing evaluator tests cover the default fixture path. Neither reader test
results nor a frozen bundle establish native execution, provider capacity,
independent grading or either required weekly campaign's outcome.
