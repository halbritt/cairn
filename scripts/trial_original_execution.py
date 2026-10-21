"""Original-execution seam for the CAIRN-107 legacy weekly route.

It carries the originally declared ``cairn.weekly-execution/1`` controls
(``trial_weekly_input._execution``) from the weekly runner into the legacy
harness, and compares what the harness observed against them. Three kinds of
evidence stay separate:

- the exact reviewed harness commit (outside this module);
- what each attempt row and the plan observed at runtime;
- this module's comparison of the two.

None of them stands in for another.

The intermediate subset that the legacy harness implements today is
``LEGACY_CAPABILITIES``. The native binary is run as a retained copy in the
run directory, mounted read-only at the sandbox target ``~/.local/bin/claude``
and executed there by absolute path. The model and the effort are passed to
the provider. The embedding worker is the runner's shared worker.

The other originally selected controls are never dropped, coerced or replaced
by ambient values. These are per-arm ``recall_mode``, ``semantic_fallback`` and
``recall_revision``, plus the embedding config and model identity. A declared
field the harness cannot carry refuses ``original_execution_unsupported:<field>``.
"""
import trial_paired_request as paired
import trial_weekly_input as weekly_input

SCHEMA = 'cairn.original-execution/1'
ARMS = ('CURRENT', 'CAND')
FIELDS = ('native', 'model', 'effort', 'recall_mode', 'semantic_fallback', 'recall_revision',
          'embedding_worker', 'embedding_config', 'embedding_model_identity')
# What trial_task_eval.py agent --legacy-fixtures --original-native-* carries today
# (test_trial_original_execution): the retained native copy, model and effort; the
# shared embedding worker; and the backend identity observed at index readiness.
# Recall controls and embedding-config consumption are follow-ons.
LEGACY_CAPABILITIES = frozenset(('native', 'model', 'effort', 'embedding_worker', 'embedding_model_identity'))
IDENTITY_SCHEMA = 'cairn.embedding-model-identity/1'
need = paired.need


class Unsupported(paired.Refusal):
    """Named refusal for each declared field the harness cannot carry yet."""

    def __init__(self, fields):
        super().__init__('original_execution_unsupported:' + fields[0])
        self.fields = ['original_execution_unsupported:' + field for field in fields]


def declared(profile, *, current, candidate, models):
    """The full originally selected execution subset, checked against the run's own pins."""
    reference = profile['execution']
    need(reference is not None, 'original_execution_unbound')
    document = paired.read_json_pin(reference)
    need(isinstance(document, dict) and isinstance(document.get('weekly_source'), dict), 'weekly_execution_shape')
    source = document['weekly_source']
    need({k: source.get(k) for k in ('label_version', 'frozen_sha256')} == profile['weekly_source'],
         'original_execution_source_mismatch')
    execution = weekly_input._execution(dict(source, execution=reference))
    native, arms = execution['native'], execution['memory_arms']
    need(native['model'] == models['CURRENT'] == models['CAND'], 'original_execution_model_mismatch')
    need((arms['CURRENT']['api_revision'], arms['CAND']['api_revision']) == (current, candidate),
         'original_execution_arm_mismatch')
    need(arms['CURRENT']['embedding_worker'] == profile['embedding']['worker'], 'original_execution_embedding_mismatch')
    return dict(identity=_identity(arms['CURRENT']['embedding_model_identity']), **_fragment(reference, native, arms))


def _identity(reference):
    """Typed backend values declared in the pinned model-identity file.

    The file's own SHA-256 pins the declaration; it is never the backend's
    model hash. The contents must be the semantic.EmbeddingIdentity shape
    {model_sha256, algorithm, dimensions} under IDENTITY_SCHEMA. An opaque or
    untyped declaration refuses; nothing is reinterpreted.
    """
    try:
        document = paired.read_json_pin(reference)
        paired.shape(document, 'schema model_sha256 algorithm dimensions', 'identity')
        model = document['model_sha256']
        need(document['schema'] == IDENTITY_SCHEMA and type(model) is str and len(model) == 64
             and all(c in '0123456789abcdef' for c in model)
             and type(document['algorithm']) is str and 0 < len(document['algorithm']) <= 128
             and type(document['dimensions']) is int and document['dimensions'] > 0, 'identity')
    except paired.Refusal as refusal:
        if str(refusal) in ('pin_mismatch', 'pin_shape', 'pin_digest'):
            raise
        raise Unsupported(['model_identity_declaration']) from None
    return {k: document[k] for k in ('model_sha256', 'algorithm', 'dimensions')}


def _fragment(reference, native, arms):
    return dict(schema=SCHEMA, execution=dict(reference), native=dict(native['binary']), model=native['model'],
                effort=native['effort'],
                arms={name: {k: arms[name][k] for k in ('recall_mode', 'semantic_fallback', 'recall_revision')}
                      for name in ARMS},
                embedding={k: dict(arms['CURRENT']['embedding_' + k]) for k in ('worker', 'config', 'model_identity')})


def supported(fragment, capabilities):
    """Declared fields the harness cannot carry, in FIELDS order; every field is always declared."""
    return [field for field in FIELDS if field not in capabilities]


def check_supported(fragment, capabilities):
    unsupported = supported(fragment, capabilities)
    if unsupported:
        raise Unsupported(unsupported)


def harness_flags(fragment):
    """Structured legacy harness flags; no PATH:SHA concatenation."""
    return ['--original-native-path', fragment['native']['path'],
            '--original-native-sha256', fragment['native']['sha256'],
            '--reasoning-effort', fragment['effort']]


def _hex64(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _utc(value):
    try:
        paired.utc(value)
    except paired.Refusal:
        return False
    return True


def _copy_shape(copy):
    """The normal producer's retained-copy observation (trial_native_copy.retain)."""
    return (set(copy) == {'source', 'sha256', 'version', 'observed_at', 'target'}
            and type(copy['source']) is str and copy['source'].startswith('/') and copy['source'].endswith('/native/claude')
            and type(copy['target']) is str and copy['target'].startswith('/') and copy['target'] != copy['source']
            and _hex64(copy['sha256']) and type(copy['version']) is str and _utc(copy['observed_at']))


def check_observed(plan, records, fragment):
    """Compare observed plan and row values with the declaration.

    Returns None, or (status, code): an absent observation is incomplete, a
    malformed one or a contradicting one is rejected. Never raises on shape.
    """
    if not isinstance(plan, dict) or plan.get('original_native') is None:
        return 'incomplete', 'original_execution_unobserved'
    native = plan['original_native']
    if not isinstance(native, dict) or set(native) != {'declared', 'observed'} or not isinstance(native['observed'], dict):
        return 'rejected', 'original_execution_observation_malformed'
    copy = native['observed']
    if not _copy_shape(copy):
        return 'rejected', 'original_execution_observation_malformed'
    if (native['declared'] != fragment['native'] or copy['sha256'] != fragment['native']['sha256']
            or plan.get('reasoning_effort') != fragment['effort'] or plan.get('model') != fragment['model']):
        return 'rejected', 'original_execution_mismatch'
    backends = plan.get('memory_backends')
    if backends is None:
        return 'incomplete', 'original_execution_identity_unobserved'
    if not isinstance(backends, dict):
        return 'rejected', 'original_execution_observation_malformed'
    for name in ARMS:
        value = backends.get(name)
        for key in ('readiness', 'identity', None):
            if value is None:
                return 'incomplete', 'original_execution_identity_unobserved'
            if not isinstance(value, dict):
                return 'rejected', 'original_execution_observation_malformed'
            if key is None:
                identity = value
                break
            value = value.get(key)
        if identity.get('model_sha256') is None or identity.get('algorithm') is None:
            return 'incomplete', 'original_execution_identity_unobserved'
        # Discovery exposes model_sha256 and algorithm only; dimensions stay declared.
        if {k: identity[k] for k in ('model_sha256', 'algorithm')} != {
                k: fragment['identity'][k] for k in ('model_sha256', 'algorithm')}:
            return 'rejected', 'original_execution_identity_mismatch'
    expected = dict(binary_sha256=fragment['native']['sha256'], effort=fragment['effort'], target=copy['target'])
    for record in records:
        row = record.get('original_native') if isinstance(record, dict) else None
        if row is None:
            return 'incomplete', 'original_execution_unobserved'
        if row != expected or record.get('model') != fragment['model']:
            return 'rejected', 'original_execution_mismatch'
    return None


def observed(plan):
    """Receipt projection of what the harness observed (no declared values)."""
    return dict(native=plan['original_native']['observed'], reasoning_effort=plan['reasoning_effort'],
                identities={name: dict(model_sha256=plan['memory_backends'][name]['readiness']['identity']['model_sha256'],
                                       algorithm=plan['memory_backends'][name]['readiness']['identity']['algorithm'],
                                       dimensions='unobserved') for name in ARMS})
