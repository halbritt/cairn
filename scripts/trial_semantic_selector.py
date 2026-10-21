"""Typed semantic-fallback selector identity (cairn.semantic-selector-identity/1).

An ambient arm with ``semantic_fallback: true`` makes the pinned hook call a
tool-free selector: ``memory.select_json`` runs ``config["claude"]`` with
``--model selector_model(config, stage)`` for the ``preview`` and ``recall``
stages. The original ``cairn.weekly-execution/1`` contract declares neither
that binary nor that model, so this module only defines the proposed closed
declaration and its comparison. Root must select a declaration before any
caller may lift ``original_execution_unsupported:semantic_fallback_selector``.

Declared values:
- ``binary``: a ``{path, sha256}`` pin of the selector executable;
- ``model``: the single ``--model`` argument for both selector stages, written
  to the hook configuration's ``model`` key (the key the legacy harness sets).

Observed values come only from evidence the harness already has:
- the SHA-256 of the file it binds as the selector (``observed_binary``);
- ``requested_model`` in each retained ``preview_process`` / ``model_process``
  hook observation (``observed_calls``). That is the argument the hook passed,
  not the model the provider served.

Never observed here, and never compared: the served model and the provider
account. Declared, observed and their comparison stay separate fields.
"""
import trial_native_copy as native_copy

SCHEMA = 'cairn.semantic-selector-identity/1'
STAGES = (('preview_process', 'preview'), ('model_process', 'recall'))
NOT_OBSERVED = ('served_model', 'provider_account')


def _model_name(value):
    # The hook's own selector_model rule: nonempty, at most 256, no whitespace or controls.
    return (type(value) is str and 0 < len(value) <= 256 and value == value.strip()
            and not any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in value))


def _sha256(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def typed(document):
    """Return the typed declared values, or raise ValueError('semantic_selector_declaration')."""
    if not (isinstance(document, dict) and set(document) == {'schema', 'binary', 'model'}
            and document['schema'] == SCHEMA and _model_name(document['model'])
            and isinstance(document['binary'], dict) and set(document['binary']) == {'path', 'sha256'}
            and type(document['binary']['path']) is str and document['binary']['path'].startswith('/')
            and '\0' not in document['binary']['path'] and _sha256(document['binary']['sha256'])):
        raise ValueError('semantic_selector_declaration')
    return dict(binary=dict(document['binary']), model=document['model'])


def observed_binary(path):
    """SHA-256 of the regular file the harness binds as the selector, or None when it cannot be read."""
    try:
        return native_copy.file_sha256(str(path))
    except (OSError, ValueError):
        return None


def observed_calls(invocations):
    """Selector calls retained in hook observation rows, in order; None when a row is malformed.

    ``requested_model_observed`` is False when the process record carries no
    ``requested_model`` (for example a hook revision that never recorded it).
    A null ``requested_model`` is observed: the hook passed no ``--model``.
    """
    if not isinstance(invocations, list):
        return None
    calls = []
    for row in invocations:
        if not isinstance(row, dict) or not (row.get('recall') is None or isinstance(row['recall'], dict)):
            return None
        recall = row.get('recall') or {}
        for key, stage in STAGES:
            if key not in recall:
                continue
            record = recall[key]
            if not isinstance(record, dict):
                return None
            requested = record.get('requested_model')
            if not (requested is None or type(requested) is str):
                return None
            outcome = record.get('outcome')
            calls.append(dict(stage=stage, requested_model=requested,
                              requested_model_observed='requested_model' in record,
                              outcome=outcome if type(outcome) is str else None))
    return calls


def compare(declared, *, binary_sha256, invocations):
    """Compare declared with observed; refusals name each unobserved or contradicting part."""
    calls = observed_calls(invocations)
    refusals = []
    if binary_sha256 is None:
        refusals.append('semantic_selector_binary_unobserved')
    elif binary_sha256 != declared['binary']['sha256']:
        refusals.append('semantic_selector_binary_mismatch')
    if calls is None:
        refusals.append('semantic_selector_observation_malformed')
    elif not calls or not all(call['requested_model_observed'] for call in calls):
        refusals.append('semantic_selector_model_unobserved')
    elif any(call['requested_model'] != declared['model'] for call in calls):
        refusals.append('semantic_selector_model_mismatch')
    result = ('matched' if not refusals
              else 'mismatched' if any(r.endswith('_mismatch') for r in refusals) else 'unobserved')
    return dict(declared=declared, observed=dict(binary_sha256=binary_sha256, calls=calls),
                result=result, refusals=refusals, not_observed=list(NOT_OBSERVED))
