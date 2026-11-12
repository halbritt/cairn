# Retained Python closure

## Accepted campaign target

The 2026-11-12 owner-directed reset, `cb71136b-cd84-4fc2-b84f-eb247b975548/v1`,
accepts the retained `x86_64-unknown-linux-gnu` Python closure qualification for
S-04R / CAIRN-355. Source landed at `58269dee`; exact candidate `adf043c4` has
Semantic review `71aa425d/v1` and Release rollback review `09efb2c8/v1`.
The accepted six direct/production-Go routes retain 27 rank, two stream and
27 embedding cases each. Both embedding routes compare 258 vectors bit exactly;
the original identities, refusal classes and 90-second timeout are preserved.
Six isolation receipts report zero process-tree connect attempts or telemetry
store mutations, with positive controls that detect both effects.

Acceptance applies to the retained immutable selection in its reviewed guest
layout and private home. The historical locale/gconv and capture-time provenance
gaps, host shell/environment dependencies and CPU/FENV limits remain disclosed.
They do not require repeated unchanged runs or a new host-roster decision for
campaign closure. Release verifies destination ABI and selection before any
installation; production activation and HC-02 remain held for the R-07 grant.

`scripts/semantic_python_closure.py` inventories and copies explicitly selected
retained Python files. Inventory/publication do not install or resolve dependencies,
execute the selected interpreter, import its packages, or select a live service.

An input selection binds every directory mode, file mode/size/SHA-256 and
symlink target, together with explicitly supplied provenance. The publisher
rechecks the complete selection before and after copying. Version strings alone
are not provenance. Symlinks must resolve inside their selected input tree;
absolute, escaping, dangling and cyclic links are rejected.
Venv control generations separately permit relative cross-generation links to
two explicitly bound payload members: the interpreter and selected site directory.

Publication requires an existing, owned `0700` stage, initially empty. It must
not overlap an input tree or contain symlink path components. Payload generations
live at `payloads/<SHA-256>`; rendered control bytes live at independently
addressed `controls/<SHA-256>`. Control bytes can reference the selected payload
without embedding their own address. The input selection and observation
manifest remain separate from those generations.

New generation files are readonly (`0444`, or `0555` when executable) and
directories are `0555`. Publication writes and verifies a temporary sibling,
syncs its files/directories, then uses Linux `renameat2(RENAME_NOREPLACE)` for
atomic publication. Reuse verifies the entire existing generation without
rewriting it. A mismatched existing generation fails; it is never repaired.
An interrupted unpublished `.pending-*` directory is not selectable. Ordinary
failure cleans the invocation's pending directory; a killed process can leave
one for explicit cleanup by its owner.

The fixed rank, stream and embedding entries accept no arguments. They clear the
inherited environment, reject loader/Python search overrides and invoke the
copied loader and interpreter. A site-disabled bootstrap verifies the complete
payload and recomputes the control generation, including the launcher bytes,
before executing the unchanged worker normally through its original venv
configuration. Only the venv `home` field changes. Helper, configuration,
symlink targets, environment and launcher bytes are all addressed and sealed.
The launcher contains hashes of its binding and helper; those do not contain
the launcher generation address, avoiding a self-hash cycle.

`ORT_DISABLE_TELEMETRY=1` is present before interpreter execution and ORT import.
That setting is a repair input, not effects evidence. No new BLAS/OMP thread cap
is added. Stream mode retains the Go supervisor's existing cache setting of `1`;
its additional cache envelope is recorded separately when comparing frozen
direct-stream captures. Scores, identities and query/document metadata still
must match. Private HOME/XDG paths remain outside the immutable stage.

The closure explicitly selects copied locale/gconv support and sets `LC_ALL`,
`LOCPATH` and `GCONV_PATH`. These support-data hashes describe the current
selected host files; their historical S-01 hashes are unknown. Qualification
masks ambient locale/gconv directories and checks actual worker mappings.
Mapping identity uses the kernel's `map_files` link, inode and a readonly data
mapping of the same open file, plus its stable-FD hash. It records both device
values: Linux [proc maps](https://github.com/torvalds/linux/blob/master/fs/proc/task_mmu.c)
uses the superblock device, while [Btrfs getattr](https://github.com/torvalds/linux/blob/master/fs/btrfs/inode.c)
reports a subvolume device. Equating that stat value with the mapping device
would incorrectly reject a valid mapping. Wrong kernel paths, inodes or
same-file mapping devices still fail qualification.

This is a complete **selected-file inventory**, not proof of a complete runtime
or upstream dependency closure. Actual mapped native images, locale/gconv data,
rendered venv/launcher/bootstrap/environment choices, original rank/embed
identities, unchanged frozen outputs, and process-tree telemetry effects still
require S-04R qualification. Planning conformity does not accept that runtime.
Installed Python files, the frozen oracle, service selection and production
configuration remain unchanged. Release owns staging and activation.

Run the real filesystem tests inside the campaign's unchanged isolated wrapper:

```sh
python3 -I -B scripts/test_semantic_python_closure.py
```

The tests use small nonmodel files, real symlinks, concurrent destination
creation and a child killed before publication. They execute the test harness
Python, never the retained candidate interpreter or model.

`scripts/check_semantic_python_closure.py` performs the separately authorized
runtime qualification. It prepares a standalone package, then runs each of the
six direct/Go mode routes through the unchanged wrapper. It uses retained S-01
capture bytes as the oracle, actual production Go supervisors and their existing
25-second interactive/90-second document deadlines, syscall observation and
positive observer controls. Results, raw outputs, mappings, failures and frozen
input/output hashes stay outside Git. A successful local check does not perform
Release acceptance or activate a service.

Frozen refusals must retain their exception and exit status through direct
entries, and the specific existing supervisor error through Go entries. A
frozen timeout requires the same deadline population; a crash or ordinary EOF
does not satisfy it. `--resume-artifact PATH --recheck-only` rechecks complete
captured receipts inside the wrapper and refuses a missing case instead of
executing a worker. This permits a comparer correction without replaying model
calls. Original failed qualification artifacts remain available.
