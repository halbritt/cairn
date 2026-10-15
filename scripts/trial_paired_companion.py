"""Weekly declaration review and projection boundary; no runtime admission."""
import datetime as dt
import hashlib
import json

import trial_paired_request as paired
import trial_provider_spend as spend
from trial_provider_mapping import project_provider_mapping


def validate_companion(request, companion_reference, review_reference, *, now, native_model):
    """Called only after request validation; retain exact checked file bytes.

    The operator selects the two pins independently. File pins and declared
    reviewer IDs establish correspondence, not authorship or review authority.
    The validated request entry point supplies its native model; grader models
    come only from the request. No schema fallback. Inputs remain quiescent.
    """
    retained, inputs = {}, {}

    def capture(name, reference, *, opaque=False):
        reference = json.loads(json.dumps(reference, allow_nan=False))
        content = paired.read_pin_bytes(reference)
        filename = 'provider-'+name+('.bin' if opaque else '.json')
        retained[filename] = content
        paired.need(sum(map(len, retained.values())) <= paired.FILE_LIMIT, 'companion_retention_bound')
        inputs[name] = dict(source=reference, file=filename)
        return None if opaque else paired.decode_json(content)

    companion = capture('companion', companion_reference)
    review = capture('review', review_reference)
    paired.shape(companion, 'schema request_sha256 spend_request mapping observed_at valid_until', 'companion_shape')
    paired.need(companion['schema'] == 'cairn.paired-companion/1', 'companion_schema')
    paired.need(companion['request_sha256'] == paired.canonical_sha(request), 'companion_request_mismatch')
    paired.shape(review, 'schema subject_sha256 artifact reviewer_id state observed_at valid_until', 'companion_review_shape')
    paired.need(review['schema'] == 'cairn.paired-companion-review/1', 'companion_review_schema')
    paired.need(review['state'] == 'accepted', 'companion_review_unavailable')
    paired.need(review['subject_sha256'] == paired.canonical_sha(companion), 'companion_review_subject')
    paired.identifier(review['reviewer_id'])
    paired.need(review['reviewer_id'] not in {request['grading'][role]['agent_id']
                for role in ('primary', 'secondary')}, 'companion_reviewer_is_grader')
    capture('review-artifact', review['artifact'], opaque=True)

    spend_request = capture('spend-request', companion['spend_request'])
    start, end = paired.utc(request['scheduled_at']), paired.utc(spend_request['valid_until'])
    paired.need(paired.utc(request['expires_at']) <= end, 'companion_spend_interval')

    def covers(document):
        paired.need(paired.utc(document['observed_at']) <= start <= now < end
                    <= paired.utc(document['valid_until']), 'companion_interval')

    covers(companion)
    covers(review)
    for disposition in request['dispositions'].values():
        paired.need(end <= paired.utc(disposition['valid_until']), 'companion_disposition_interval')
    mapping = capture('mapping', companion['mapping'])
    capacity = capture('capacity', request['capacity'])
    capture('workload', request['plan']['workload'])
    for name, field in (('accounts', 'account_binding'), ('routes', 'route_binding'),
                        ('reservation', 'reservation')):
        capture(name, capacity[field])
    binding = capture('spend-binding', mapping['spend_binding'])
    prices = capture('prices', mapping['prices'])
    models = dict(CURRENT=native_model, CAND=native_model,
                  **{role: request['grading'][role]['model'] for role in ('primary', 'secondary')})
    try:
        projection = project_provider_mapping(request['plan'], models, request['capacity'],
            companion['spend_request'], companion['mapping'],
            now=now.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    except spend.SpendRefusal:
        # One fixed public code regardless of pricing failure or module entry.
        raise paired.Refusal('provider_spend_refused') from None
    for document in (capacity, mapping, binding, *prices['quotes'].values()):
        covers(document)
    content = paired.selected_bytes(projection)
    retained['provider-projection.json'] = content
    paired.need(sum(map(len, retained.values())) <= paired.FILE_LIMIT, 'companion_retention_bound')
    evidence = dict(schema='cairn.paired-companion-evidence/1', inputs=inputs,
        subject_sha256=review['subject_sha256'], reviewer_id=review['reviewer_id'],
        projection_sha256=paired.canonical_sha(projection),
        projection_file=dict(file='provider-projection.json', sha256=hashlib.sha256(content).hexdigest()),
        runtime_admission=False)
    return evidence, retained
