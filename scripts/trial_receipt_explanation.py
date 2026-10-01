"""Bounded original-ranking metadata from a wrapper-owned disposable database.

No retrieval, replay, caller impersonation, source bodies or production routing.
This observation is separate from task grades and the model-facing context.
"""
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

SCHEMA = 'cairn.trial-receipt-explanations/1'
MAX_RECEIPTS, MAX_CANDIDATES = 16, 256
MAX_OBSERVATION_BYTES, MAX_OUTPUT_BYTES = 8*1024*1024, 1024*1024
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
DIGEST = re.compile(r'[0-9a-f]{64}')
REASONS = ('SELECTED', 'INDEXED', 'CONTEXT_MISSING', 'CURRENTNESS_MISMATCH', 'OUTSIDE_VALIDITY',
           'CLASS_NOT_CONSEQUENTIAL', 'AUTHORITY_INACTIVE', 'SCOPE_AUTHORITY_INACTIVE',
           'POLICY_UNENFORCEABLE', 'OPEN_CONFLICT', 'ATTRIBUTION_UNRECONCILED', 'EVIDENCE_UNAVAILABLE',
           'NO_LEXICAL_MATCH', 'NO_ENTITY_MATCH', 'NO_FAILURE_MATCH', 'NO_RETRIEVAL_MATCH',
           'KIND_FILTERED', 'REDUNDANT', 'OPTIONAL_BUDGET', 'TOTAL_BUDGET', 'PAGE_OFFSET',
           'BROWSE_OFFSET', 'EVALUATION_INCOMPLETE')


def collect_receipts(path):
    """Read existing hook metadata only. Native-tool receipt coverage is unknown."""
    found, seen, unknown = [], {}, set()
    try:
        with Path(path).open('rb') as source:
            raw = source.read(MAX_OBSERVATION_BYTES+1)
        if len(raw) > MAX_OBSERVATION_BYTES:
            return dict(receipts=[], complete=False, unknown=['hook_observation_byte_limit'])
        for line in raw.splitlines():
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or row.get('schema') != 'cairn.task-hook-observation/1':
                    raise ValueError()
                entries = row.get('search_receipts')
                if not isinstance(entries, list):
                    unknown.add('search_observation_missing')
                    continue
                if type(row.get('search_receipts_omitted')) is not int or row['search_receipts_omitted'] != 0:
                    unknown.add('search_observation_incomplete')
                for entry in entries:
                    if (not isinstance(entry, dict) or entry.get('state') != 'observed'
                            or not isinstance(entry.get('receipt_id'), str) or not UUID.fullmatch(entry['receipt_id'])
                            or not isinstance(entry.get('query_sha256'), str) or not DIGEST.fullmatch(entry['query_sha256'])):
                        unknown.add('unobserved_search_receipt')
                        continue
                    ident, digest = entry['receipt_id'], entry['query_sha256']
                    if ident in seen:
                        if seen[ident] != digest:
                            unknown.add('receipt_digest_conflict')
                            found = [r for r in found if r['receipt_id'] != ident]
                        continue
                    seen[ident] = digest
                    if len(found) == MAX_RECEIPTS:
                        unknown.add('receipt_limit')
                        continue
                    found.append(dict(receipt_id=ident, query_sha256=digest))
            except (ValueError, TypeError, UnicodeError, RecursionError):
                unknown.add('malformed_hook_observation')
    except OSError:
        unknown.add('hook_observation_unavailable')
    return dict(receipts=found, complete=not unknown, unknown=sorted(unknown))


def _integer(path, signed=False):
    # Project in SQL: untrusted detail strings never cross the process boundary.
    value = "detail #> '{" + path + "}'"
    text = "detail #>> '{" + path + "}'"
    pattern = '-?[0-9]{1,15}' if signed else '[0-9]{1,15}'
    return f"CASE WHEN jsonb_typeof({value})='number' AND {text} ~ '^{pattern}$' THEN ({text})::bigint END"


def _boolean(name, absent_false=False):
    default = " WHEN NOT detail ? '"+name+"' THEN false" if absent_false else ''
    return f"CASE WHEN jsonb_typeof(detail->'{name}')='boolean' THEN (detail->>'{name}')::boolean{default} END"


def explanation_sql(receipt, principal, repo):
    if (not UUID.fullmatch(receipt) or not re.fullmatch(r'agent:task-eval-[A-Za-z0-9_-]{1,128}', principal)
            or repo != 'trial:task-eval'):
        raise ValueError('unsupported owned receipt identity')
    allowed = ','.join("'"+reason+"'" for reason in REASONS)
    fields = {
        'record_id': 'c.record_id', 'version': 'CASE WHEN c.version>0 THEN c.version END',
        'class': "CASE WHEN detail->>'class' IN ('A','B','C') THEN detail->>'class' END",
        'reason': f"CASE WHEN c.reason IN ({allowed}) THEN c.reason ELSE 'UNKNOWN' END",
        'escalation_blocked': 'c.escalation_blocked', 'rank': _integer('rank'),
        'cost_bytes': _integer('cost_bytes'), 'lexical_matches': _integer('lexical_matches'),
        'scope_specificity': _integer('scope_specificity'), 'mandatory': _boolean('mandatory'),
        'entity_match': _boolean('entity_match', True), 'exact_text_match': _boolean('exact_text_match', True),
        'idf_score': _integer('idf_score', True), 'semantic_score': _integer('semantic_score', True),
        'passage_score': _integer('passage_hit,score', True),
        'passage_offset': _integer('passage_hit,span,offset'), 'passage_length': _integer('passage_hit,span,length'),
    }
    optional = ('idf_score', 'semantic_score')
    valid = [f"(NOT detail ? '{key}' OR ({fields[key]}) IS NOT NULL)" for key in optional]
    valid += ["(NOT detail ? 'passage_hit' OR (jsonb_typeof(detail->'passage_hit')='object' AND " +
              " AND ".join(f"({fields[key]}) IS NOT NULL" for key in ('passage_score','passage_offset','passage_length')) + "))"]
    fields['metadata_valid'] = ' AND '.join(valid)
    projection = ','.join("'"+key+"',"+value for key,value in fields.items())
    # Exact receipt owner/repository join. No local operator CLI or alternate token.
    return f"""BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='4000ms';
WITH owned AS (
 SELECT receipt_id,explanation_version FROM cairn.retrieval_receipt
 WHERE receipt_id='{receipt}' AND caller='{principal}' AND scope->>'repo'='{repo}'
), candidates AS (
 SELECT jsonb_build_object({projection}) AS value, {_integer('rank')} AS rank,
 c.record_id,c.version FROM cairn.retrieval_candidate c JOIN owned USING(receipt_id)
), limited AS (
 SELECT * FROM candidates ORDER BY CASE WHEN rank>0 THEN 0 ELSE 1 END,rank,record_id,version
 LIMIT {MAX_CANDIDATES}
)
SELECT jsonb_build_object('owned',EXISTS(SELECT 1 FROM owned),
 'explanation_version',(SELECT explanation_version FROM owned),
 'candidate_count',(SELECT count(*) FROM candidates),
 'candidates',coalesce((SELECT jsonb_agg(value ORDER BY CASE WHEN rank>0 THEN 0 ELSE 1 END,rank,record_id,version) FROM limited),'[]'::jsonb));
COMMIT;
"""


def export_receipts(psql, socket, database, env, run, principal, repo, observed):
    report = dict(schema=SCHEMA, coverage='hook_observed_receipts_only', native_tool_coverage='unknown',
                  complete=observed['complete'], unknown=list(observed['unknown']), receipts=[],
                  query_digest_source='hook_observation_not_receipt_verified',
                  observed_receipts=len(observed['receipts']), exported_receipts=0,
                  limits=dict(receipts=MAX_RECEIPTS,candidates_per_receipt=MAX_CANDIDATES,output_bytes=MAX_OUTPUT_BYTES))
    deadline = time.monotonic()+10
    for receipt in observed['receipts']:
        row = dict(receipt, status='unknown', candidates=[])
        if time.monotonic() >= deadline:
            report['unknown'].append('export_deadline')
            break
        try:
            sql = explanation_sql(receipt['receipt_id'], principal, repo)
            raw = run([psql,'-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-h',socket,'-d',database,'-c',sql],
                      env=env,timeout=min(5,deadline-time.monotonic())).stdout
            if len(raw)>MAX_OUTPUT_BYTES:
                raise ValueError('projection too large')
            value=json.loads(raw)
            if value['owned'] is not True:
                row['reason']='missing_or_not_owned'
            elif value['explanation_version'] not in (1,2):
                row['reason']='unsupported_or_legacy_explanation'
            else:
                row.update(status='observed', explanation_version=value['explanation_version'],
                           candidate_count=value['candidate_count'], candidates=value['candidates'],
                           truncated=value['candidate_count']>len(value['candidates']))
                required=('version','class','rank','cost_bytes','lexical_matches','scope_specificity','mandatory','entity_match','exact_text_match')
                if row['truncated']:
                    row['status']='unknown';row['reason']='candidate_limit'
                if any(c['metadata_valid'] is not True or c['reason']=='UNKNOWN' or any(c[k] is None for k in required) for c in row['candidates']):
                    row['status']='unknown';row['reason']='candidate_metadata_unknown'
        except (OSError,ValueError,TypeError,KeyError,RuntimeError,subprocess.TimeoutExpired):
            row=dict(receipt,status='unknown',reason='export_failed',candidates=[])
        # Whole-record admission; don't silently trim candidate metadata.
        if len(json.dumps(report|{'receipts':report['receipts']+[row]},separators=(',',':')).encode())>MAX_OUTPUT_BYTES-256:
            report['unknown'].append('output_byte_limit');break
        report['receipts'].append(row)
        if row['status']!='observed':report['unknown'].append(row.get('reason','receipt_unknown'))
    report['unknown']=sorted(set(report['unknown']))
    report['complete']=report['complete'] and not report['unknown']
    report['observed_receipts']=len(observed['receipts'])
    report['exported_receipts']=len(report['receipts'])
    return report


def receipt_export_metadata(base):
    """Link the separate sidecar, including when subsequent grading raises."""
    try:
        path=base/'receipt-explanations.json'
        with path.open('rb') as source:raw=source.read(MAX_OUTPUT_BYTES+1)
        if len(raw)>MAX_OUTPUT_BYTES:raise ValueError()
        value=json.loads(raw)
        if value.get('schema')!=SCHEMA or type(value.get('complete')) is not bool:raise ValueError()
        return dict(artifact=path.name,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),complete=value['complete'])
    except (OSError,ValueError,TypeError,AttributeError,RuntimeError):
        return dict(artifact=None,complete=False,reason='sidecar_unavailable')


def retain_hook_explanations(store, base):
    """Best-effort evidence before owned-store cleanup; never changes task grades."""
    try:
        observed=collect_receipts(base/'hook-state/observations.jsonl')
        report=store.receipt_explanations(observed)
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,subprocess.TimeoutExpired):
        report=dict(schema=SCHEMA,complete=False,unknown=['export_failed'],receipts=[],
                    coverage='hook_observed_receipts_only',native_tool_coverage='unknown')
    try:
        encoded=json.dumps(report,separators=(',',':')).encode()+b'\n'
        if len(encoded)>MAX_OUTPUT_BYTES:
            raise ValueError('bounded export invariant failed')
        with (base/'receipt-explanations.json').open('xb') as target:target.write(encoded)
        return receipt_export_metadata(base)
    except (OSError,ValueError,TypeError,KeyError,RuntimeError):
        return dict(artifact=None,complete=False,reason='sidecar_retention_failed')
