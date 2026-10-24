"""Original-execution seam for the CAIRN-107 legacy weekly route.

It carries the originally declared ``cairn.weekly-execution/1`` controls
(``trial_weekly_input._execution``) from the weekly runner into the legacy
harness, and compares what the harness observed against them. Three kinds of
evidence stay separate:

- the exact reviewed harness commit (outside this module);
- what each attempt row and the plan observed at runtime;
- this module's comparison of the two.

None of them stands in for another.

What the legacy harness carries (``LEGACY_CAPABILITIES``, tested in
test_trial_original_execution and test_trial_recall_controls):

- the native binary, as a retained copy run at the sandbox target;
- the model and the effort;
- per-arm ``recall_mode`` and ``semantic_fallback``, literally;
- ``recall_revision``, bound to the raw ``memory.py`` and ``inbox_recall.py``
  blobs at that commit;
- the embedding worker and config, through ``trial_embedding_launch.prepare``;
- the typed embedding model identity, compared before any attempt.

One combination stays refused: an ambient arm with ``semantic_fallback``
needs a hook selector model and binary that the execution contract does not
declare. It refuses ``original_execution_unsupported:semantic_fallback_selector``.
No value is ever dropped, coerced or replaced by an ambient one.
"""
from pathlib import Path

import trial_embedding_identity as identity_declaration
import trial_paired_request as paired
import trial_weekly_input as weekly_input

SCHEMA = 'cairn.original-execution/1'
ARMS = ('CURRENT', 'CAND')
FIELDS = ('native', 'model', 'effort', 'recall_mode', 'semantic_fallback', 'recall_revision',
          'embedding_worker', 'embedding_config', 'embedding_model_identity')
LEGACY_CAPABILITIES = frozenset(FIELDS)
IDENTITY_SCHEMA = identity_declaration.SCHEMA
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
    provider = execution['provider'] if execution['schema'] == weekly_input.CODEX_EXECUTION_SCHEMA else execution['native']
    arms = execution['memory_arms']
    need(provider['model'] == models['CURRENT'] == models['CAND'], 'original_execution_model_mismatch')
    need((arms['CURRENT']['api_revision'], arms['CAND']['api_revision']) == (current, candidate),
         'original_execution_arm_mismatch')
    need(arms['CURRENT']['embedding_worker'] == profile['embedding']['worker'], 'original_execution_embedding_mismatch')
    return dict(identity=_identity(arms['CURRENT']['embedding_model_identity']),
                **_fragment(reference, provider, arms, harness='codex' if execution['schema'] == weekly_input.CODEX_EXECUTION_SCHEMA else 'claude'))


def _identity(reference):
    """Typed backend values declared in the pinned model-identity file (trial_embedding_identity)."""
    try:
        document = paired.read_json_pin(reference)
        return identity_declaration.typed(document)
    except paired.Refusal as refusal:
        if str(refusal) in ('pin_mismatch', 'pin_shape', 'pin_digest'):
            raise
        raise Unsupported(['model_identity_declaration']) from None  # opaque bytes, never reinterpreted
    except ValueError:
        raise Unsupported(['model_identity_declaration']) from None


def _fragment(reference, actor, arms, *, harness='claude'):
    provider_pin=dict(actor['binary'])
    fragment=dict(schema=SCHEMA, execution=dict(reference), harness=harness, model=actor['model'], effort=actor['effort'],
                arms={name: {k: (dict(arms[name][k]) if k in ('hook', 'bridge') else arms[name][k])
                             for k in ('recall_mode', 'semantic_fallback', 'recall_revision', 'hook', 'bridge')}
                      for name in ARMS},
                embedding={k: dict(arms['CURRENT']['embedding_' + k]) for k in ('worker', 'config', 'model_identity')})
    fragment['provider' if harness=='codex' else 'native']=provider_pin
    return fragment


def supported(fragment, capabilities):
    """Declared fields the harness cannot carry, in FIELDS order; every field is always declared."""
    return [field for field in FIELDS if field not in capabilities]


def check_supported(fragment, capabilities):
    unsupported = supported(fragment, capabilities)
    if 'semantic_fallback' not in unsupported and any(
            arm['recall_mode'] == 'ambient' and arm['semantic_fallback'] for arm in fragment['arms'].values()):
        # Ambient fallback calls a hook selector whose model and binary the contract does not declare.
        unsupported.append('semantic_fallback_selector')
    if unsupported:
        raise Unsupported(unsupported)


CONTROLS_SCHEMA = 'cairn.original-arm-controls/1'


def arm_controls(fragment, recall):
    """The per-arm controls document the harness receives: literal recall controls plus the
    retained raw recall blobs (``recall`` maps arm -> {hook, bridge} pins of those blobs)."""
    return dict(schema=CONTROLS_SCHEMA, arms={
        name: dict(recall_mode=fragment['arms'][name]['recall_mode'],
                   semantic_fallback=fragment['arms'][name]['semantic_fallback'],
                   recall_revision=fragment['arms'][name]['recall_revision'],
                   hook=dict(recall[name]['hook']), bridge=dict(recall[name]['bridge'])) for name in ARMS})


def harness_flags(fragment, controls):
    """Structured legacy harness flags; no PATH:SHA concatenation."""
    identity = fragment['embedding']['model_identity']
    actor='provider' if fragment.get('harness')=='codex' else 'native'
    flags=(['--codex-binary',fragment[actor]['path'],'--codex-binary-sha256',fragment[actor]['sha256'],
            '--codex-weekly-limits'] if actor=='provider' else
           ['--original-native-path', fragment[actor]['path'], '--original-native-sha256',fragment[actor]['sha256']])
    return flags + ['--reasoning-effort', fragment['effort'],
            '--original-arms', controls['path'], '--original-arms-sha256', controls['sha256'],
            '--original-embedding-identity', identity['path'],
            '--original-embedding-identity-sha256', identity['sha256']]


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
    codex=fragment.get('harness')=='codex'
    observed_key='original_codex' if codex else 'original_native'
    if not isinstance(plan, dict) or plan.get(observed_key) is None:
        return 'incomplete', 'original_execution_unobserved'
    native = plan[observed_key]
    if not isinstance(native, dict) or set(native) != {'declared', 'observed'} or not isinstance(native['observed'], dict):
        return 'rejected', 'original_execution_observation_malformed'
    copy = native['observed']
    if codex:
        valid_copy=(isinstance(copy,dict) and set(copy)=={'sha256','version','target'} and
                    _hex64(copy.get('sha256')) and type(copy.get('version')) is str and
                    copy.get('target')==str(Path.home()/'.codex/codex'))
    else:
        valid_copy=_copy_shape(copy)
    if not valid_copy:
        return 'rejected', 'original_execution_observation_malformed'
    actor_key='provider' if codex else 'native'
    if (native['declared'] != fragment[actor_key] or copy['sha256'] != fragment[actor_key]['sha256']
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
    expected = dict(binary_sha256=fragment[actor_key]['sha256'], effort=fragment['effort'], target=copy['target'])
    arms = {name: dict(recall_mode=arm['recall_mode'], semantic_fallback=arm['semantic_fallback'],
                       recall_revision=arm['recall_revision'], hook_sha256=arm['hook']['sha256'],
                       bridge_sha256=arm['bridge']['sha256']) for name, arm in fragment['arms'].items()}
    loaded = plan.get('original_arms')
    if loaded is None:
        return 'incomplete', 'original_execution_unobserved'
    if not isinstance(loaded, dict) or not isinstance(loaded.get('arms'), dict):
        return 'rejected', 'original_execution_observation_malformed'
    if loaded['arms'] != arms:
        return 'rejected', 'original_execution_mismatch'
    for record in records:
        row = record.get(observed_key) if isinstance(record, dict) else None
        arm = record.get('original_arm') if isinstance(record, dict) else None
        if row is None or arm is None:
            return 'incomplete', 'original_execution_unobserved'
        if (row != expected or record.get('model') != fragment['model']
                or arm != arms.get(record.get('arm'))):
            return 'rejected', 'original_execution_mismatch'
    return None


def observed(plan):
    """Receipt projection of what the harness observed (no declared values)."""
    codex=plan.get('harness')=='codex'
    key='original_codex' if codex else 'original_native'
    actor='provider' if codex else 'native'
    return dict(**{actor:plan[key]['observed']}, reasoning_effort=plan['reasoning_effort'],
                arms=plan.get('original_arms'),
                identities={name: dict(model_sha256=plan['memory_backends'][name]['readiness']['identity']['model_sha256'],
                                       algorithm=plan['memory_backends'][name]['readiness']['identity']['algorithm'],
                                       dimensions='unobserved') for name in ARMS})
