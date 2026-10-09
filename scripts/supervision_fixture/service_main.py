"""Fixed owned-service bootstrap. Actor remains gated until the outer owner pins it."""
import dataclasses
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trial_external_supervision import FixtureRequest, run_fixture

unit, retained, workspace, raw_request, loss_stage, external_deadline = sys.argv[1:]
fields = Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()
print(json.dumps(dict(kind='SERVICE_READY', unit=unit, pid=os.getpid(), start_ticks=int(fields[19]),
    invocation_id=os.environ['INVOCATION_ID'], membership=Path('/proc/self/cgroup').read_text().strip())),flush=True)
if sys.stdin.buffer.read(1) != b'G':
    sys.exit(31)
request = FixtureRequest(**json.loads(raw_request))
remaining = float(external_deadline)-time.monotonic()
request = dataclasses.replace(request, wall_seconds=remaining)
result = run_fixture(request, Path(retained), Path(workspace), controller_loss_stage=None if loss_stage=='none' else loss_stage)
print(json.dumps(dict(kind='RESULT', result=dataclasses.asdict(result)),sort_keys=True),flush=True)
