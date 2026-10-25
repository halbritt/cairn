"""Run the SIWC CairnTools adapter against one disposable TrialStore only."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from trial_task_eval import TRIAL_REPO, TrialStore  # noqa: E402


def main():
    binary = Path(sys.argv[1]).resolve()
    if not binary.is_file():
        raise SystemExit("provide the built Cairn CLI binary")
    run_id = "siwc-adapter-" + uuid.uuid4().hex[:12]
    query = "SIWC adapter fixture needle " + uuid.uuid4().hex
    source = "Synthetic source-fit fixture: " + query + "."
    fixture_root = Path(tempfile.mkdtemp(prefix="cairn-siwc-adapter-"))
    store = None
    try:
        store = TrialStore(fixture_root / "store", binary, "siwc_adapter_" + uuid.uuid4().hex[:8])
        store.remember(dict(id="siwc_adapter_source", kind="lesson", body=source, shareable=True))
        store.start()
        env = dict(os.environ,
                   CAIRN_SIWC_TRIAL_ROOT=str(store.root),
                   CAIRN_SIWC_SOCKET_DIR=str(store.sockdir),
                   CAIRN_SIWC_SOCKET=str(store.socket),
                   CAIRN_SIWC_TOKEN_FILE=str(store.token_file),
                   CAIRN_SIWC_BINARY=str(store.binary),
                   CAIRN_SIWC_BINARY_SHA256=hashlib.sha256(store.binary.read_bytes()).hexdigest(),
                   CAIRN_SIWC_REPO=TRIAL_REPO,
                   CAIRN_SIWC_TASK="source-fit/siwc-adapter-smoke",
                   CAIRN_SIWC_RUN=run_id,
                   CAIRN_SIWC_QUERY=query)
        result = subprocess.run(["go", "test", "-count=1", "-run",
                                 "^TestTrialStoreToolsAgainstDisposableTrialStore$",
                                 "./trials/siwc"], cwd=ROOT, env=env, check=False)
        if result.returncode:
            raise SystemExit(result.returncode)
    finally:
        try:
            if store is not None:
                store.stop()
        finally:
            shutil.rmtree(fixture_root, ignore_errors=True)


if __name__ == "__main__":
    main()
