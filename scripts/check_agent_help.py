"""Use ordinary CLI help offline, then execute its examples against the real API."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

OPERATIONS = ('edit', 'revise', 'append', 'replace', 'cite', 'history',
              'assessments', 'assess-run', 'recompile')
FLAG_OPERATIONS = ('search', 'remember', 'pull', 'pull-evidence')


def example(binary, operation, env):
    output = subprocess.check_output([binary, 'agent', operation, '--help'],
                                     env=env, text=True, timeout=5)
    return json.loads(output.split('Example JSON:\n', 1)[1])


def check_offline(binary):
    binary = str(Path(binary).resolve())
    with tempfile.TemporaryDirectory(prefix='cairn-agent-help-') as directory:
        env = dict(os.environ, CAIRN_DATABASE_URL='host=/absent-help-db dbname=denied')
        env.pop('HOME', None)
        env.pop('CAIRN_HOME', None)
        for operation in ('', *OPERATIONS, *FLAG_OPERATIONS):
            for option in ('--help', '-h'):
                args = [binary, 'agent', *([operation] if operation else []), option]
                # Keep stdin open: help must finish without asking for JSON input.
                process = subprocess.Popen(args, cwd=directory, env=env, stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    assert process.wait(timeout=5) == 0, args
                    output, error = process.communicate(timeout=5)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)
                assert output.startswith('Usage: cairn agent') and not error, (args, output, error)
        for args in (['replace', '--help', 'extra'],
                     *([operation, '--help', 'extra'] for operation in FLAG_OPERATIONS),
                     ['unknown', '--help'], ['--bad', '--help']):
            result = subprocess.run([binary, 'agent', *args], env=env, text=True,
                                    capture_output=True, timeout=5)
            assert result.returncode == 2 and json.loads(result.stdout)['status'] == 'INVALID_REQUEST', result
        assert not list(Path(directory).iterdir()), 'Help wrote local state'
    print('Ordinary agent help is readable offline and does not wait for stdin; invalid calls remain errors')


def check_examples(binary, root, environment, evidence_call):
    support = evidence_call(['capture-evidence'], dict(request_id=str(uuid.uuid4()),
                            repo='fixture:socket', body='Selected CLI help example evidence',
                            source='synthetic help fixture', sensitivity='shareable'))
    env = dict(environment, CAIRN_DATABASE_URL='host=/absent-help-client-db dbname=denied')
    base = [binary, 'agent', '--socket', str(root / 'api.sock'),
            '--token-file', str(root / 'hosted-agent.token')]

    def call(operation, request, error=None):
        result = subprocess.run([*base, operation], input=json.dumps(request), env=env,
                                text=True, capture_output=True, timeout=5)
        envelope = json.loads(result.stdout)
        if error:
            assert result.returncode and envelope['status'] == error, envelope
            return
        assert result.returncode == 0 and envelope['ok'], envelope
        return envelope['data']

    values = {'REPOSITORY': 'fixture:socket', 'NEW_UUID': str(uuid.uuid4()),
              'EVIDENCE_UUID': support['evidence_id'], 'SOURCE_SHA256': support['sha256']}

    def request(operation, version=None):
        text = json.dumps(example(binary, operation, env))
        for placeholder, value in values.items():
            text = text.replace(placeholder, value)
        result = json.loads(text)
        if 'request_id' in result:
            result['request_id'] = str(uuid.uuid4())
        if version is not None:
            result['expected_version'] = version
        return result

    draft = request('edit')['draft']
    draft['body'] = 'Earlier guidance'
    note = call('create', dict(request_id=str(uuid.uuid4()), draft=draft))
    values['RECORD_UUID'] = note['record_id']
    replacement = request('replace', 1)
    revised = call('replace', replacement)
    assert revised['version'] == 2 and call('replace', replacement) == revised
    call('replace', dict(replacement, request_id=str(uuid.uuid4())), 'VERSION_CONFLICT')
    assert call('get', dict(record_id=note['record_id']))['body'] == 'Corrected guidance'
    suffix = request('append', 2)
    assert call('append', suffix)['version'] == 3
    assert call('get', dict(record_id=note['record_id']))['body'] == 'Corrected guidance' + suffix['body']
    assert call('revise', request('revise', 3))['version'] == 4
    assert call('edit', request('edit', 4))['version'] == 5
    assert call('cite', request('cite', 5))['version'] == 6
    history = call('history', request('history'))
    assert [v['version'] for v in history['versions']] == [6, 5, 4, 3, 2, 1]
    assert call('history', dict(record_id=note['record_id'], version=1))['versions'][0]['body'] == draft['body']
    assert call('get', dict(record_id=note['record_id']))['body'] == request('edit')['draft']['body']
    query = request('recompile')['query']
    package = call('compile', dict(request_id=str(uuid.uuid4()), query=query, purpose='context',
                   scope=dict(repo='fixture:socket', task_id='help-examples', run_id='requests'), available_tokens=32000))
    values['RECEIPT_UUID'] = package['receipt_id']
    assert call('assessments', request('assessments')) == []
    review = request('assess-run')
    written = call('assess-run', review)
    assert written['version'] == 1 and written['witness'] == 'testimony'
    assert call('assess-run', review) == written
    reviews = call('assessments', request('assessments'))
    assert reviews[0]['reason'] == review['reason'] and reviews[0]['task_outcome'] == 'unknown'
    reconstructed = call('recompile', request('recompile'))
    assert reconstructed['historical'] and reconstructed['package'] == package
    print('Help examples execute real hosted note corrections, citations, history, qualitative review and reconstruction')
