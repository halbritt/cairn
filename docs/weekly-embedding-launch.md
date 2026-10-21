# Original weekly embedding launcher preparation

`scripts/trial_embedding_launch.py` implements the accepted config-to-launcher
slice. It has one integration entry point:

```python
receipt = prepare(config_pin, {'CURRENT': current_worker_pin,
                               'CAND': candidate_worker_pin}, fresh_output_dir)
```

Pins have exactly `path` and `sha256`. The caller supplies a pinned UTF-8 JSON
document with exactly these fields:

```json
{
  "schema": "cairn.weekly-embedding-launch/1",
  "python": "/explicit/semantic/venv/bin/python",
  "worker": {"path": "/explicit/semantic/embedding_worker.py", "sha256": "f8b134f92523eb53ab21f7d4878283f5a6dc401ecb605c7e8d7e2df2aa673ae8"},
  "scorer": {"path": "/explicit/semantic/semantic_rank.py", "sha256": "4f70d19c1442bebe812cf0fab667a214992575d68737b981189783211916de95"},
  "model_dir": "/explicit/prepared/model-directory"
}
```

This child schema supports only the displayed reviewed source pair and the
`cairn.install-semantic-embedding-launcher/1` renderer. The pair is from commit
5149b03cb65707875041726254412f365177c9d0. The helper verifies the actual module
bytes, exact basenames, and adjacent scorer location without importing them.
Supporting another source pair or launcher representation requires an explicit
compatibility review and version decision. No caller capability flag or
arbitrary argv/environment extension enables support.

The renderer is exactly `install-semantic.sh`'s embedding template: shebang,
`exec `, `shlex.join([python, worker.path, '--model-dir', model_dir])`, and LF.
Both declared executable pins must be equal, and both files must equal the
rendered bytes. A changed config cannot silently select a different launcher.
An opaque historical config remains unsupported; selecting this child schema
changes the config/preparation pins but does not reinterpret `weekly-execution/1`.

`python` preserves the lexical absolute invocation path, including a venv
symlink. The helper resolves it to check a regular executable and records the
resolution, device, inode and byte length. It does not rewrite the invocation to
the system interpreter. These observations are not an interpreter content pin
or proof of package/runtime identity. Other input/output paths reject symlinks;
the general paired pin policy is unchanged. The model directory must exist; no
model file is read, prepared, downloaded or loaded.

Config and launcher reads are limited to 16,384 bytes each; paths to 4,096 UTF-8
bytes; source reads to the reviewed sizes (6,493 and 10,831 bytes). Reads open
nonblocking and validate regular files on the opened descriptor, so a FIFO
cannot block before validation. Duplicate keys, nonfinite JSON, unknown fields,
unsupported sources and pin mismatches refuse before output creation.

After validation, preparation exclusively creates `fresh_output_dir` with mode
0700, retaining the original config bytes as `embedding-config.json` (0600) and
the rendered executable as `worker-embeddings` (0700). Existing output is never
reused or removed. File writes are flushed/fsynced. Failed writes unwind only
the files/directory created by that call; cleanup errors propagate. This is not
a crash-atomic journal. The returned preparation receipt includes both selected
input pins, retained pins/byte counts, source pins, interpreter observation,
renderer and configured model directory. A receipt is source evidence, not an
execution or admission certificate.

The caller owns successful retention, revalidation and cleanup. Inputs and the
output parent must remain trusted and quiescent. This helper does not establish
concurrent filesystem custody, Python import/cache integrity, transitive
dependency identity, or a physical memory bound. It leaves the original 21 cases,
126 attempts, 1900 distractors, 131072 raw bytes, 512 MiB combined working limit,
4 MiB traffic limit and original deadlines unchanged. The control-document bounds
are not new runtime reservations or a proof that the full run fits.

## Integration remaining in 501's lane

Preparation alone starts no process. The original execution adapter must consume
the returned retained launcher path for both `TrialStore.embedding_worker`
arguments and preserve the selected common worker pin separately. Use the
existing Go embedding endpoint/process owner. Capture the actual successful
full-coverage backend identity for each arm, and compare it to the independently
selected `embedding_model_identity` before attempts. Do not derive the expected
identity from that run. No identity protocol is duplicated here.

Recall binding remains separate: compare the declared recall commit's raw fixed
hook/bridge blobs, with Git replacements disabled, to the declared pins and actual
retained/mounted source. The branch route currently selects the same arm/recall
commit. A label or self-reported revision does not supply that binding. This
helper neither implements recall binding nor changes hook configuration.

## Synthetic verification

Run `python3 -B -m unittest discover -s scripts -p test_trial_embedding_launch.py`.
Tests cover exact config retention, known installed-template digest, both-arm
matching, source/config drift, strict schema/bounds, FIFO refusal, scorer location,
symlink policy, exclusive outputs and write-failure cleanup. A real inert child
records quoted argv through a lexical interpreter symlink; it never imports the
worker or loads a model. These tests do not admit a weekly campaign.
