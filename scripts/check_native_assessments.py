"""Review a receipt through native tools, retaining uncertainty and corrections."""
import uuid


def check(invoke, support):
    receipt = invoke('search', dict(query='assessment' + uuid.uuid4().hex))['receipt_id']
    assert invoke('assessments', dict(receipt_id=receipt)) == []
    review = dict(request_id=str(uuid.uuid4()), receipt_id=receipt, expected_version=0,
                  task_outcome='unknown', failure_domain='unknown', failure_kind='',
                  method='qualitative-review/1', evidence_ids=[support['evidence_id']],
                  reason='Synthetic review: café guidance suggested a check; source overlap and net benefit remain unknown.')
    result = invoke('assess', review)
    assert result == dict(receipt_id=receipt, version=1, request_id=review['request_id'],
                          witness='testimony', observer='agent:hosted-capture'), result
    assert invoke('assess', review) == result
    history = invoke('assessments', dict(receipt_id=receipt))
    assert len(history) == 1 and history[0]['reason'] == review['reason']
    assert history[0]['evidence_ids'] == review['evidence_ids']
    assert history[0]['task_outcome'] == 'unknown' and history[0]['witness'] == 'testimony'
    assert 'body' not in history[0] and history[0]['method'] == review['method']
    changed = dict(review, reason=review['reason'] + ' Later review found a competing explanation.')
    invoke('assess', changed, 'IDEMPOTENCY_CONFLICT')
    changed['request_id'] = str(uuid.uuid4())
    invoke('assess', changed, 'VERSION_CONFLICT')
    changed.update(expected_version=1, task_outcome='accepted', failure_domain='none', evidence_ids=[])
    invoke('assess', changed, 'EVIDENCE_UNAVAILABLE')
    changed.update(task_outcome='unknown', failure_domain='unknown')
    assert invoke('assess', changed)['version'] == 2
    corrected = invoke('assessments', dict(receipt_id=receipt))
    assert corrected[0] == history[0] and corrected[1]['reason'] == changed['reason']
    assert [entry['version'] for entry in corrected] == [1, 2]
    assert invoke('assess', review) == result  # Original retry survives later revisions.
    return receipt
