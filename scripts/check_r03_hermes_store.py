#!/usr/bin/env python3
"""Installed Hermes objects through selected Go and an owned PostgreSQL/API.

No native agent conversation or model account starts. The selector is a frozen
scripted external engine; lifecycle, SDK, API, authentication and Store are real.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import disposable_host
import check_r03_hermes_installed as installed
from test_r03_hermes_installer import installer

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--hermes-root', type=Path, required=True)
    parser.add_argument('--hermes-python', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    images = args.images.resolve()
    pins = json.loads((images / 'pins.json').read_bytes())
    for name in ('lifecycle', 'stager', 'cairn'):
        path = images / name
        if digest(path) != pins[name]['sha256'] or path.stat().st_mode & 0o222:
            raise RuntimeError('R03_IMAGE_INPUT: frozen image/pin mismatch: ' + name)
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    installed.SOURCE_REVISION = source
    versions = {}
    for name in ('postgres', 'initdb', 'pg_ctl', 'psql', 'createdb'):
        path = args.pg_bin / name
        if path.stat().st_mode & 0o222:
            raise RuntimeError('R03_PG_INPUT: selected PostgreSQL executable must be frozen')
        versions[name] = dict(path=str(path), sha256=digest(path),
                              version=subprocess.check_output([path, '--version'], text=True).strip())
    (output / 'inputs.json').write_text(json.dumps(dict(source=source, images=pins, postgres=versions,
        hermes_python=dict(path=str(args.hermes_python), sha256=digest(args.hermes_python))), indent=2) + '\n')
    cluster = host = None
    cleanup = {}
    try:
        cluster = disposable_host.OwnedCluster.start(output / 'cluster', pg_bin=args.pg_bin)
        host = disposable_host.DisposableHost.start(cluster, binary=images / 'cairn',
            repo='fixture:r03-hermes-store', database='cairn_r03_hermes1375', home=output / 'api',
            roles=('worker',), principals={'worker': 'agent:r03-hermes-fixture'},
            label='r03_hermes_store', log=output / 'api.log')
        host.require_fresh('memory_record')
        pg_pid = int((output / 'cluster/data/postmaster.pid').read_text().splitlines()[0])
        assert pg_pid > 1 and host.process.pid > 1
        assert Path('/proc', str(host.process.pid), 'exe').resolve() == (images / 'cairn').resolve()
        assert Path('/proc', str(pg_pid), 'exe').resolve() == (args.pg_bin / 'postgres').resolve()
        (output / 'owned-processes.json').write_text(json.dumps(dict(
            postgres=dict(pid=pg_pid, executable=os.readlink('/proc/' + str(pg_pid) + '/exe')),
            api=dict(pid=host.process.pid, executable=os.readlink('/proc/' + str(host.process.pid) + '/exe'))), indent=2) + '\n')
        seed = host.agent('worker', 'remember', 'R03 retention policy\nRetain thirty days in the synthetic fixture.',
            args=('--repo', host.repo, '--kind', 'decision', '--shareable', '--request-id', str(uuid.uuid4()), '--stdin'))
        (output / 'seed.json').write_text(json.dumps(seed, indent=2) + '\n')
        selector = output / 'selector'
        selector.write_text('#!' + str(args.hermes_python) + '\n' + '''import json,pathlib,sys
root=pathlib.Path(__file__).parent
with (root/'selector-inputs.jsonl').open('a') as log:
 log.write(json.dumps(dict(argv=sys.argv[1:],stdin=sys.stdin.read()))+'\\n')
print(json.dumps(json.loads((root/'selection.json').read_text())['result']))
''')
        selector.chmod(0o500)
        selection = dict(result=dict(structured_output=dict(checkpoint=None, workstream=None,
            memories=[dict(record_id=None, kind='decision', title='R03 selected retention',
                           body='Keep thirty days of retention in the synthetic R03 fixture.')])) )
        (output / 'selection.json').write_text(json.dumps(selection) + '\n')
        (output / 'selector-pin.json').write_text(json.dumps(dict(path=str(selector), sha256=digest(selector),
            version='scripted-r03-selector/1', provider='fixture; no model account'), indent=2) + '\n')
        f = installed.HermesInstalledFixture(output / 'project', images / 'lifecycle',
                                             stager=images / 'stager', harness='hermes', enabled=False)
        for config in (f.memory, f.coord):
            config.update(cairn=str(images / 'cairn'), socket=str(host.socket), token_file=str(host.token('worker')),
                          repo=host.repo)
        f.memory.update(claude=str(selector), model='r03-scripted-fixture', recall_mode='agent_tools',
                        semantic_fallback=False, capture_observations=True, recall_observations=True)
        f.publish_configuration(enabled=False)
        home = f.account
        (home / 'config.yaml').write_text(json.dumps(dict(memory=dict(memory_enabled=True), plugins=dict(enabled=[]))))
        skill = output / 'fixture-skill.md'
        skill.write_text('R03 synthetic fixture skill.\n')
        installer.install(home, dict(executable=str(images / 'cairn'), socket=str(host.socket),
            token_file=str(host.token('worker')), repo=host.repo), str(selector), skill,
            lifecycle_selection=f.selected, stager=str(images / 'stager'))
        session = 'Hermes R03/session:日本語'
        (home / 'r03-fixture.json').write_text(json.dumps(dict(schema='cairn.r03-hermes-fixture/1',
            root=str(output), project=str(f.root), session=session)))
        command = [str(args.hermes_python), '-I', '-B', str(ROOT / 'scripts/check_r03_hermes_objects.py'),
            '--hermes-root', str(args.hermes_root), '--home', str(home), '--output', str(output / 'native')]
        result = subprocess.run(command, cwd=f.root, env=dict(host.env, HERMES_HOME=str(home),
            TERMINAL_CWD=str(f.root)), capture_output=True, timeout=180)
        (output / 'native-process.json').write_text(json.dumps(dict(argv=command, exit=result.returncode,
            stdout=result.stdout.decode(), stderr=result.stderr.decode()), indent=2) + '\n')
        assert result.returncode == 0, result.stderr.decode()
        assert b'PRIVATE_FIXTURE' not in result.stdout + result.stderr
        state = json.loads((Path(f.memory['state_dir']) / (hashlib.sha256(session.encode()).hexdigest() + '.json')).read_bytes())
        calls = json.loads((output / 'native/calls.json').read_bytes())
        completed = next(call for call in calls if call['payload']['hook_event_name'] == 'SessionEnd')
        saved_ids = json.loads(completed['stdout'])['cairn_status']['last_capture']['records']
        assert len(saved_ids) == 1 and saved_ids[0] != seed['record_id']
        assert state['last_capture']['outcome'] == 'unchanged' and len(state['captured_digest']) == 64
        saved = host.agent('worker', 'history', dict(record_id=saved_ids[0], version=1))
        assert 'Keep thirty days of retention' in saved['versions'][0]['body']
        bodies = host.sql('SELECT body FROM cairn.record_version')
        assert 'PRIVATE_FIXTURE' not in bodies and 'Original owner prompt' not in bodies
        counts = {table: host.count(table) for table in
                  ('memory_record', 'capture_observation', 'recall_observation', 'agent_session_attempt', 'agent_delivery')}
        assert counts['memory_record'] == 2, 'completed capture was duplicated or lost'
        assert counts['capture_observation'] >= 1 and counts['recall_observation'] == 1
        assert counts['agent_session_attempt'] == counts['agent_delivery'] == 0
        report = dict(schema='cairn.r03-hermes-store/1', passed=True, source=source, counts=counts,
            seed_record=seed['record_id'], saved_record=saved_ids[0], dialogue_stored=False,
            provider='scripted external selector; no real model', checked_tools='baseline refusal; Foundation input pending',
            source_image_sha256=f.receipt['binary']['sha256'])
        (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
    finally:
        if host is not None:
            cleanup['api_exit'] = host.close()
        if cluster is not None:
            cleanup['postgres_stopped'] = cluster.stop()
        (output / 'cleanup.json').write_text(json.dumps(cleanup, indent=2) + '\n')
        if cluster is not None and not cleanup['postgres_stopped']:
            raise RuntimeError('R03_STORE_CLEANUP: owned PostgreSQL remains unresolved; retained ' + str(output))


if __name__ == '__main__':
    main()
