"""Real operator conversion/history, including a genuinely older review writer."""
import json
import os
import subprocess
import uuid

from check_note_transport import operator


def check(binary, environment, proposal):
    uid = lambda: str(uuid.uuid4())
    repo = proposal['repo']
    note = operator(binary, environment, 'create', dict(request_id=uid(), draft=dict(
        kind='lesson', body='Original selected failure lesson', claim_type='self',
        scope=dict(repo=repo, task_id='*', run_id='*'))))
    request = dict(request_id=uid(), proposal_id=proposal['proposal_id'],
                   expected_version=proposal['version'], disposition='converted',
                   result_record=note['record_id'], result_version=1,
                   reason='Select the inspected lesson version')
    converted = operator(binary, environment, 'review-proposal', request)
    assert converted['result_version'] == 1
    operator(binary, environment, 'revise', dict(request_id=uid(), record_id=note['record_id'],
             expected_version=1, repo=repo, body='Later revised failure guidance'))
    current = operator(binary, environment, 'proposal', record_id=proposal['proposal_id'])
    assert current['result_version'] == 1
    original = operator(binary, environment, 'history', dict(record_id=note['record_id'], version=current['result_version']))
    assert original['versions'][0]['body'] == note['body']
    assert operator(binary, environment, 'review-proposal', request) == converted
    opened = operator(binary, environment, 'review-proposal', dict(request_id=uid(),
                      proposal_id=proposal['proposal_id'], expected_version=converted['version'],
                      disposition='open', reason='Reconsider the retained review'))
    history = operator(binary, environment, 'proposal-history', dict(proposal_id=proposal['proposal_id'], limit=1))
    assert history['more'] and history['reviews'][0]['disposition'] == 'open'
    earlier = operator(binary, environment, 'proposal-history', dict(proposal_id=proposal['proposal_id'], before_version=history['next_before_version']))
    assert any(r.get('result_record') == note['record_id'] and r['result_version'] == 1 for r in earlier['reviews'])
    print('Operator CLI pins selected lesson versions, reads exact original bodies and pages retained conversions after reopening')

    previous = environment.get('CAIRN_PREVIOUS_BINARY')
    if previous:
        legacy_request = dict(request_id=uid(), proposal_id=proposal['proposal_id'],
                              expected_version=opened['version'], disposition='converted',
                              result_record=note['record_id'], reason='Older writer has no version field')
        legacy = operator(previous, environment, 'review-proposal', legacy_request)
        assert 'result_version' not in legacy
        assert operator(binary, environment, 'review-proposal', legacy_request) == legacy
        current = operator(binary, environment, 'proposal', record_id=proposal['proposal_id'])
        assert current['result_record'] == note['record_id'] and 'result_version' not in current
        history = operator(binary, environment, 'proposal-history', dict(proposal_id=proposal['proposal_id']))
        assert history['reviews'][0]['disposition'] == 'converted' and 'result_version' not in history['reviews'][0]
        assert 'result_record' not in history['reviews'][0]
        assert any(r.get('result_version') == 1 for r in history['reviews'])
        print('Actual older writer leaves new conversions explicitly unpinned; historical pins and legacy mutation retries remain exact')
