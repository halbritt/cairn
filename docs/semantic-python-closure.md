# Retained Python closure

`scripts/semantic_python_closure.py` inventories and copies explicitly selected
retained Python files. It does not install or resolve dependencies, execute the
selected interpreter, import its packages, or select a live service.

An input selection binds every directory mode, file mode/size/SHA-256 and
symlink target, together with explicitly supplied provenance. The publisher
rechecks the complete selection before and after copying. Version strings alone
are not provenance. Symlinks must resolve inside their selected input tree;
absolute, escaping, dangling and cyclic links are rejected.

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
