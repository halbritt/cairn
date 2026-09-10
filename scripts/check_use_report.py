"""Exercise CLI access to a use history that exceeds its default page."""
import json
import os
import subprocess
import sys
import uuid


def check(binary):
    def call(args, payload=None, expected='OK'):
        result = subprocess.run([binary, *args], input=None if payload is None else json.dumps(payload),
                                env=os.environ, capture_output=True, text=True, timeout=15)
        response = json.loads(result.stdout)
        assert response['status'] == expected, (args, response)
        assert (result.returncode == 0) == (expected == 'OK'), (args, result.returncode)
        return response.get('data')

    repo = 'fixture:use-history:' + uuid.uuid4().hex
    records = [call(['remember', '--repo', repo, 'historyfixture selected note ' + str(i)])['record_id']
               for i in range(2)]
    receipts = set()
    for _ in range(51):
        package = call(['compile'], dict(request_id=str(uuid.uuid4()),
                       scope=dict(repo=repo, task_id='history-review', run_id='fixture'),
                       query='historyfixture', purpose='context', available_tokens=32000))
        receipts.add(package['receipt_id'])
    first = call(['use-report', repo])
    assert len(first['rows']) == 100 and first['more'] and first['next_offset'] == 100
    print('Default CLI history exposes 100 of 102 retained exposure rows', flush=True)
    second = call(['use-report', '--offset', str(first['next_offset']), repo])
    assert len(second['rows']) == 2 and not second['more'] and second['next_offset'] == 102
    complete = call(['use-report', '--limit', '200', repo])
    assert first['rows'] + second['rows'] == complete['rows'] and not complete['more']
    assert {(row['receipt_id'], row['record_id']) for row in complete['rows']} == {
        (receipt, record) for receipt in receipts for record in records}
    offset, filtered = 0, []
    for _ in range(4):
        page = call(['use-report', '--record', records[0], '--limit', '17', '--offset', str(offset), repo])
        filtered.extend(page['rows'])
        if not page['more']:
            break
        assert page['next_offset'] > offset
        offset = page['next_offset']
    assert len(filtered) == 51 and {row['receipt_id'] for row in filtered} == receipts
    assert all(row['record_id'] == records[0] and row['version'] == 1 for row in filtered)
    assert all(row['usage'] == 'unknown' and row['task_outcome'] == 'unknown' for row in filtered)
    assert call(['use-report', '--offset', '102', repo])['rows'] == []
    for args in [['--limit', '0', repo], ['--limit', '201', repo], ['--offset', '-1', repo],
                 ['--record', 'invalid', repo], [], [repo, 'extra']]:
        call(['use-report', *args], expected='INVALID_REQUEST')
    print('CLI pages reach every exposure and record-filtered history without inventing usage or outcomes')

    selected = None
    for task in ['unrelated task', 'review task']:
        result = subprocess.run([binary, 'run', '--repo', repo, '--task', task,
                                 '--run', 'attempt', '--', '/bin/true'],
                                env=os.environ, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        if task == 'review task':
            selected = json.loads(result.stderr)['data']['receipt_id']
    for operation in ['use-report', 'run-report', 'runs']:
        report = call([operation, '--task', 'review task', '--run', 'attempt', repo])
        assert {row['receipt_id'] for row in report['rows']} == {selected}, report
        assert len(report['rows']) == (2 if operation == 'use-report' else 1)
        assert all(row['task_outcome'] == 'unknown' for row in report['rows'])
        assert not report['more']
        assert call([operation, '--task', 'review task', '--run', 'absent', repo])['rows'] == []
    matching = call(['use-report', '--task', 'history-review', '--run', 'fixture',
                     '--record', records[0], '--limit', '200', repo])
    assert {row['receipt_id'] for row in matching['rows']} == receipts
    print('Task/run filters select one observed execution and its exposures among unrelated history')


if __name__ == '__main__':
    check(sys.argv[1])
