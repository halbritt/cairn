"""Synthetic CLI capture contract. Called only by the disposable-store suite."""
import json
import os
from pathlib import Path
import subprocess
import sys

binary, artifact_home = sys.argv[1:]
env = dict(os.environ, CAIRN_HOME=artifact_home)
canary = 'CAIRN_DEFAULT_PROMPT_CAPTURE_CANARY'
run = subprocess.run(
    [binary, 'run', '--repo', 'fixture:capture', '--prompt', canary, '--', '/bin/cat'],
    env=env, text=True, capture_output=True, check=True,
)
assert canary in run.stdout, 'task must reach the wrapped process'
result = json.loads(run.stderr)['data']
receipt = result['receipt_id']
for command in ['replay', 'explain']:
    response = subprocess.run([binary, command, receipt], env=env,
                              text=True, capture_output=True, check=True)
    assert canary not in response.stdout, f'raw task retained in {command}'
    assert canary not in response.stderr, f'raw task retained in {command} stderr'
artifact_root = Path(result['artifacts'])
assert artifact_root.is_dir(), 'run did not retain the reported artifact directory'
assert (artifact_root / 'outcome.json').is_file(), 'run did not retain an outcome artifact'
for artifact in artifact_root.rglob('*'):
    if artifact.is_file():
        assert canary.encode() not in artifact.read_bytes(), f'raw task in {artifact.name}'
print('Default task prompt reaches process and is absent from retained replay, explanation and run artifacts')
