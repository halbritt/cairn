"""Corpus identity, separate from authorized read bookkeeping.

The caller supplies its explicitly owned socket/database and checked command
runner. This module starts no services, seeds nothing, and returns no note text.
Stamps compare one database with itself; fresh imports have different identities.
"""
import json

SCHEMA = 'cairn.corpus-fingerprint/2'
TABLES = ('memory_record', 'record_version', 'record_applicability', 'record_entities', 'record_relation')
# Deliberately explicit: a future memory_record column requires review instead
# of silently disappearing from the corpus-integrity contract.
MEMORY_FIELDS = ('record_id', 'current_version', 'class', 'lifecycle', 'sensitivity',
                 'created_at', 'recovered_attempt_id')


def _digest(query):
    return "(SELECT jsonb_build_object('rows',count(*),'sha256',encode(sha256(convert_to(" \
           "coalesce(string_agg(row_to_json(t)::text,E'\\n' ORDER BY row_to_json(t)::text),'')," \
           "'UTF8')),'hex')) FROM (" + query + ") t)"


def fingerprint_sql():
    parts = []
    for table in TABLES:
        fields = ','.join(MEMORY_FIELDS) if table == 'memory_record' else '*'
        parts.extend(("'" + table + "'", _digest('SELECT ' + fields + ' FROM cairn.' + table)))
    # One read-only snapshot covers all tables and both domains. Locale/timezone
    # are fixed for stable textual timestamps/order across separate invocations.
    return """BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL TIME ZONE 'UTC';
SET LOCAL DateStyle TO 'ISO, YMD';
SELECT jsonb_build_object(
 'schema','""" + SCHEMA + """',
 'memory_record_columns',(SELECT jsonb_agg(column_name ORDER BY column_name)
   FROM information_schema.columns WHERE table_schema='cairn' AND table_name='memory_record'),
 'corpus',jsonb_build_object(""" + ','.join(parts) + """),
 'operational',jsonb_build_object('memory_record',""" + _digest('SELECT * FROM cairn.memory_record') + """));
COMMIT;
"""


def corpus_stamp(psql, socket, database, env, run):
    """Read the explicitly supplied owned socket/database; never discover a profile."""
    raw = run([psql, '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1',
               '-h', socket, '-d', database, '-c', fingerprint_sql()], env=env).stdout
    value = json.loads(raw)
    if value.get('memory_record_columns') != sorted((*MEMORY_FIELDS, 'use_generation')):
        raise ValueError('unsupported memory_record schema; review corpus projection')
    if value.get('schema') != SCHEMA:
        raise ValueError('unsupported corpus fingerprint')
    return value


def same_corpus(before, after):
    """Operational stamps remain retained evidence but are not corpus equality."""
    expected = sorted((*MEMORY_FIELDS, 'use_generation'))
    for stamp in (before, after):
        if (stamp.get('schema') != SCHEMA or stamp.get('memory_record_columns') != expected
                or set(stamp.get('corpus', {})) != set(TABLES)):
            raise ValueError('unsupported or incomplete corpus fingerprint')
    return before['corpus'] == after['corpus']
