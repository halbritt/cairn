#!/usr/bin/env bash
# Optional CPU dependency/model preparation. Does not change running services.
set -euo pipefail
semantic_root="${CAIRN_HOME:-$HOME/.local/share/cairn}/semantic"
case "$semantic_root" in /*) ;; *) printf '%s\n' 'CAIRN_HOME must be absolute' >&2; exit 2;; esac
source_root="$(cd "$(dirname "$0")/.." && pwd)"
umask 077
mkdir -p "$semantic_root"
if [[ ! -x "$semantic_root/venv/bin/python" ]]; then
    uv venv --python 3.11 "$semantic_root/venv"
fi
uv pip install --python "$semantic_root/venv/bin/python" fastembed==0.8.0
"$semantic_root/venv/bin/python" - "$semantic_root" "$source_root/scripts/semantic_rank.py" <<'PY'
import hashlib
import json
import shlex
import shutil
import sys
from pathlib import Path
from fastembed import TextEmbedding

root, source = map(Path, sys.argv[1:])
model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=str(root / "model-cache"),
                      threads=2, providers=["CPUExecutionProvider"], cuda=False)
model_dir = Path(model.model._model_dir)
manifest = {}
for name in ("model_optimized.onnx", "config.json", "tokenizer.json",
             "special_tokens_map.json", "tokenizer_config.json"):
    with (model_dir / name).open("rb") as stream:
        manifest[name] = hashlib.file_digest(stream, "sha256").hexdigest()
worker = root / "semantic_rank.py"
pending = root / "semantic_rank.py.pending"
shutil.copyfile(source, pending)
pending.replace(worker)
command = [str(root / "venv/bin/python"), str(worker), "--model-dir", str(model_dir)]
launcher = root / "worker"
pending = root / "worker.pending"
pending.write_text("#!/bin/sh\nexec " + shlex.join(command) + "\n")
pending.chmod(0o700)
pending.replace(launcher)
(root / "model-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("Prepared local CPU worker. Enable explicitly with:")
print("cairn serve --semantic-command " + shlex.quote(str(launcher)))
PY
