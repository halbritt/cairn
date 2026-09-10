"""Check native process-argument integrity against the disposable API."""
import hashlib
import json
import uuid

from check_opencode_defaults import check as check_session


def check(invoke, opencode, output, settings_path, settings):
    request = str(uuid.uuid4())
    for malformed in ('\ud800', '\udfff', '\ud800x', '\udfff\ud800'):
        invoke('search', dict(request_id=request, query='unicode-context-review',
                              context={'binding_id': malformed}), 'INVALID_REQUEST')
        invoke('search', dict(request_id=request, query='unicode-query-' + malformed), 'INVALID_REQUEST')
    settings_path.write_text(json.dumps(dict(settings, context={'binding': '\ud800'})))
    try:
        invoke('search', dict(request_id=request, query='unicode-settings-review'), 'INVALID_REQUEST')
    finally:
        settings_path.write_text(json.dumps(settings))

    # Supplementary characters, literal escapes and intentional U+FFFD stay exact.
    valid = '日本語 😀 � literal \\ud800'
    recovered = invoke('search', dict(request_id=request, query=valid,
                                      context={'binding_id': valid}))
    assert recovered['context']['binding_id'] == valid
    assert recovered['query'] == 'sha256:' + hashlib.sha256(valid.encode()).hexdigest()
    assert recovered['request_id'] == request

    request = str(uuid.uuid4())
    cases = [
        ('unicode-invalid', 'cairn_search', dict(request_id=request, query='unicode-session',
                                               context={'binding_id': '\ud800'})),
        ('unicode-recovered', 'cairn_search', dict(request_id=request, query=valid,
                                                 context={'binding_id': valid})),
    ]
    report = check_session(opencode, output, connection=settings, extra_cases=cases)
    assert 'INVALID_REQUEST: CLI arguments require well-formed Unicode' in report['results']['unicode-invalid']
    recovered = json.loads(report['results']['unicode-recovered'])
    assert recovered['context']['binding_id'] == valid and recovered['request_id'] == request
    assert recovered['query'] == 'sha256:' + hashlib.sha256(valid.encode()).hexdigest()
    print('Native debug and scripted normal session refuse lossy Unicode, preserve valid text and reuse refused search IDs')
