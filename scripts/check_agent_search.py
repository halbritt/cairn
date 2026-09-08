"""Exercise reusable agent search/pull commands against the disposable Unix API."""
import json
import shlex
import subprocess
import uuid


def check(binary, root, environment, grant, claim, support):
    def call(args, payload=None, check=True):
        p = subprocess.run([binary, *args], input=None if payload is None else json.dumps(payload),
                           env=environment, capture_output=True, text=True, timeout=15, check=check)
        return json.loads(p.stdout)

    scope = dict(repo='fixture:socket', task_id='agent-search', run_id='agent-search')
    instruction = call(['issue'], dict(request_id=str(uuid.uuid4()), grant_id=grant['grant_id'],
                       draft=dict(kind='instruction', body='Keep the fixture source provenance with the answer.',
                                  claim_type='self', sensitivity='shareable', scope=scope),
                       mandatory=True, policy_key='agent-search-fixture', category='workflow',
                       reason='Exercise mandatory context in the reusable agent search view'))['data']
    # Client-only commands must work with an unusable database address.
    client_env = dict(environment, CAIRN_DATABASE_URL='host=/absent-agent-search-db dbname=denied')
    token = root / "agent ' $(printf injected).token"
    token.write_bytes((root / 'agent.token').read_bytes())
    token.chmod(0o600)
    agent = ['agent', '--socket', str(root / 'api.sock'), '--token-file', str(token)]
    request_id = str(uuid.uuid4())
    p = subprocess.run([binary, *agent, 'search', '--repo', scope['repo'], '--task', scope['task_id'],
                        '--run', scope['run_id'], '--request-id', request_id, 'socket'], env=client_env,
                       capture_output=True, text=True, check=True, timeout=15)
    view = json.loads(p.stdout)['data']
    assert view['schema'] == 'cairn.agent-search/1' and view['scope'] == scope
    assert view['credits_remaining'] == 4 and view['bytes_remaining'] <= 24000
    assert len(p.stdout.encode()) <= view['available_tokens']
    canonical = call([*agent, 'index'], dict(request_id=request_id, scope=scope, query='socket',
                     purpose='context', available_tokens=32000, context={}))['data']
    assert view['selected'] == canonical['package']['semantic']['selected']
    assert any(s['mandatory'] and s['record']['record_id'] == instruction['record_id'] for s in view['selected'])
    assert [{k: v for k, v in e.items() if k != 'pull_command'} for e in view['index']] == canonical['package']['semantic']['index']
    entry = next(e for e in view['index'] if e['record_id'] == claim['record_id'])
    def pull():
        return json.loads(subprocess.run(entry['pull_command'], shell=True, env=client_env, capture_output=True,
                                        text=True, check=True, timeout=15).stdout)['data']
    body = pull()
    assert body['selection']['record']['record_id'] == claim['record_id'] and body['credits_remaining'] == 3
    assert pull() == body  # Repeating the displayed command keeps its request ID.
    words = shlex.split(entry['pull_command'])
    receipt, handle = words[-2:]
    evidence_args = [*agent, 'pull-evidence', '--request-id', str(uuid.uuid4()), receipt, handle,
                     support['evidence_id'], support['sha256']]
    evidence = call(evidence_args)['data']
    assert evidence['evidence']['body'] == 'explicit supporting socket evidence' and evidence['credits_remaining'] == 2
    assert call(evidence_args)['data'] == evidence
    foreign = call(['agent', '--token-file', str(root / 'observer.token'), 'pull', receipt, handle], check=False)
    assert foreign['status'] == 'AUTHORITY_DENIED'
    hosted = call(['agent', '--token-file', str(root / 'hosted.token'), 'search', '--repo', scope['repo'],
                   '--task', scope['task_id'], '--run', scope['run_id'], 'socket'])['data']
    assert hosted['destination'] == dict(name='hosted', allow_local=False)
    assert all(e['record_id'] == claim['record_id'] for e in hosted['index'])
    empty = call([*agent, 'search', '--repo', scope['repo'], '--task', scope['task_id'], '--run', scope['run_id'], 'unmatchedmarker'])['data']
    assert empty['index'] == [] and empty['selected'] == view['selected']
    for flags in [[], ['--task', '*', '--run', 'run'], ['--task', 'task']]:
        refused = call([*agent, 'search', *flags, 'socket'], check=False)
        assert refused['status'] == 'INVALID_REQUEST'
    token.unlink()
    print('Reusable agent search/pull/evidence CLI preserves mandatory context, scoped index order, exact handles, quoted paths, retry credits and hosted filtering')
