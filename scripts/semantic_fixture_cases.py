"""Public synthetic inputs for semantic/4; importing this module loads no model."""

import copy
import hashlib
import json
import uuid
from pathlib import Path

WORKLOAD_SHA256 = '30b9b9852c91bac38836c8d121244ee3e762445ac505d5845dc454bae90eaec0'
PYTHON_WHITESPACE_CODEPOINTS = (9, 10, 11, 12, 13, 28, 29, 30, 31, 32, 133, 160, 5760,
                              8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202,
                              8232, 8233, 8239, 8287, 12288)


def _note(identifier, body):
    return {'record_id': str(uuid.uuid5(uuid.NAMESPACE_URL, identifier)), 'version': 1,
            'body': body, 'body_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()}


def ranking_cases():
    """Return fresh dictionaries matching the frozen comparator's first 19 cases."""
    path = Path(__file__).resolve().parents[1] / 'core/testdata/retrieval-quality.json'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != WORKLOAD_SHA256:
        raise ValueError('retrieval workload differs from the frozen semantic/4 inputs')
    workload = json.loads(raw)
    notes = [_note(note['id'], note['body']) for note in workload['notes']]
    cases = [{'id': query['id'], 'query': query['query'], 'notes': copy.deepcopy(notes)}
             for query in workload['queries']]
    long_body = ('Routine service administration follows the selected runbook. ' * 240
                 + '\n\nThe zephyr service credential is stored at /run/zephyr/access-key.')
    cases.append({'id': 'long-tail', 'query': 'Where is the zephyr service credential?',
                  'notes': [_note('long-tail', long_body)]})
    cases.append({'id': '64-short', 'query': 'How do I start the local database?',
                  'notes': [_note('short-' + str(i), workload['notes'][i % len(notes)]['body']) for i in range(64)]})
    return cases


def _embedding(identifier, operation, text, expected='accepted'):
    return {'id': identifier, 'operation': operation, 'text': text, 'expected': expected}


def embedding_cases():
    """Return the nine positive queries and six positive documents from semantic/4."""
    return [
        _embedding('query-ascii', 'query', 'database storage'),
        _embedding('query-cjk', 'query', '漢' * 1365),
        _embedding('query-emoji', 'query', '🙂 ' * 600),
        _embedding('query-combining', 'query', 'e\u0301 ' * 800),
        _embedding('query-special-tokens', 'query', '[CLS] [MASK] hello [SEP]'),
        _embedding('query-exact-512', 'query', 'a ' * 502),
        _embedding('query-over-512', 'query', 'a ' * 503),
        _embedding('query-4096-bytes', 'query', 'a ' * 2048),
        _embedding('query-token-empty', 'query', '\u200b\u200d\ufeff'),
        _embedding('document-ascii', 'document', 'Use the private directory.'),
        _embedding('document-cjk', 'document', '数据库存储位置。请使用私有目录。' * 30),
        _embedding('document-mixed-unicode', 'document', "Café CAFe\u0301 ＡＢＣ\t漢字🙂  [MASK] [UNK]  -- Decision: Use Ω's PRIVATE directory.\n"),
        _embedding('document-trailing-decision', 'document', 'Unrelated installation background and old material. ' * 80 + 'DECISION: Use the private directory.'),
        _embedding('document-maximum-latin', 'document', ('abc def ghi jkl mno pqr stu vwx yz. ' * 2000)[:65536]),
        _embedding('document-maximum-cjk', 'document', '漢' * 21845 + '!'),
    ]


def embedding_failure_cases():
    """Typed-text failures; wire/decoder/process faults belong to S-01b's inventory."""
    cases = [_embedding(f'blank-{operation}-u{codepoint:04x}', operation, chr(codepoint), 'refused')
             for codepoint in PYTHON_WHITESPACE_CODEPOINTS for operation in ('query', 'document')]
    cases.extend([
        _embedding('query-empty', 'query', '', 'refused'),
        _embedding('document-empty', 'document', '', 'refused'),
        _embedding('query-byte-overflow', 'query', 'a ' * 2048 + 'a', 'refused'),
        _embedding('document-byte-overflow', 'document', '!' * 65537, 'refused'),
        _embedding('document-token-empty', 'document', '\u200b\u200d\ufeff', 'refused'),
    ])
    return cases


def consumed_punctuation_case():
    """Import-only input: the single longer oracle is consumed and unqualified."""
    # Observations, not an expected outcome under the unmeasured Go supervisor.
    return {'id': 'document-maximum-punctuation', 'operation': 'document',
            'text': '!' * 65536, 'observed_direct_supervisor_disposition': 'timeout',
            'real_go_disposition': 'unmeasured'}


def wordpiece_boundary_case(tokenizer):
    """Build input only, using an explicitly supplied prepared tokenizer.

    Freeze the returned input and tokenizer digest before comparing runtimes.
    This extra boundary case does not change the 19-case raw-output baseline.
    """
    if tokenizer is None:
        raise ValueError('an explicit prepared tokenizer is required; no fallback')
    for word in ('unaffordable', 'electroencephalography', 'characteristically', 'tokenization'):
        tokens = tokenizer.encode(word, add_special_tokens=False).tokens
        continuation = next((index for index, token in enumerate(tokens)
                             if index > 0 and token.startswith('##')), None)
        if continuation is None or continuation > 320:
            continue
        body = 'a ' * (320 - continuation) + word + ' ' + 'a ' * 100
        encoding = tokenizer.encode(body, add_special_tokens=False)
        if len(encoding.ids) > 384 and encoding.tokens[320].startswith('##'):
            identifier = 'wordpiece-continuation-at-320'
            return {'id': identifier, 'query': 'Explain the selected word.',
                    'notes': [_note(identifier, body)]}
    raise ValueError('prepared tokenizer did not produce the required ## boundary; no fallback')
