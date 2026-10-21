"""Harness side of the original per-arm controls (CAIRN-107 legacy weekly route).

``cairn.original-arm-controls/1`` (written by ``trial_weekly_run``) carries,
for each memory arm:
- the literal ``recall_mode`` and ``semantic_fallback``;
- ``recall_revision``;
- pins of the raw ``memory.py`` (``hook``) and ``inbox_recall.py``
  (``bridge``) blobs at that revision.

This module validates the document against the harness's own ``--memory``
specs before any store starts. It also loads the typed embedding identity
declaration. Each attempt re-hashes the mounted hook and bridge. Only paths
and digests are compared; nothing is reinterpreted or replaced by ambient
defaults.
"""
import hashlib
from pathlib import Path

import trial_embedding_identity as identity_declaration
import trial_native_copy as native_copy
import trial_paired_request as paired

SCHEMA = 'cairn.original-arm-controls/1'
MODES = ('ambient', 'agent_tools')


def _pinned(path, sha256, code):
    try:
        return paired.read_json_pin(dict(path=path, sha256=sha256))
    except paired.Refusal:
        raise ValueError(code) from None


def file_sha256(path):
    return native_copy.file_sha256(str(path))


def load_controls(path, sha256, memory_specs):
    """Validate the controls document against the --memory specs; return (plan record, per-label config)."""
    document = _pinned(path, sha256, 'original_arms_unavailable')
    labels = {spec.split(':', 1)[0]: spec.split(':', 2)[2] for spec in memory_specs}
    if not (isinstance(document, dict) and set(document) == {'schema', 'arms'} and document['schema'] == SCHEMA
            and isinstance(document['arms'], dict) and set(document['arms']) == set(labels)):
        raise ValueError('original_arms_shape')
    record, configs = {}, {}
    for label, arm in document['arms'].items():
        if not (isinstance(arm, dict)
                and set(arm) == {'recall_mode', 'semantic_fallback', 'recall_revision', 'hook', 'bridge'}
                and arm['recall_mode'] in MODES and type(arm['semantic_fallback']) is bool
                and type(arm['recall_revision']) is str and arm['recall_revision']):
            raise ValueError('original_arms_shape')
        for role in ('hook', 'bridge'):
            pin = arm[role]
            if not (isinstance(pin, dict) and set(pin) == {'path', 'sha256'} and type(pin['path']) is str
                    and pin['path'].startswith('/') and '\0' not in pin['path'] and type(pin['sha256']) is str
                    and len(pin['sha256']) == 64 and all(c in '0123456789abcdef' for c in pin['sha256'])):
                raise ValueError('original_arms_shape')
        if arm['recall_mode'] == 'ambient' and arm['semantic_fallback']:
            # The hook selector model and binary are not part of the declared contract.
            raise ValueError('original_execution_unsupported:semantic_fallback_selector')
        hook, bridge = Path(arm['hook']['path']), Path(arm['bridge']['path'])
        if (hook.name != 'memory.py' or bridge.name != 'inbox_recall.py' or hook.parent != bridge.parent
                or hook.resolve() != Path(labels[label]).resolve()):
            raise ValueError('original_arms_recall_paths')
        for pin in (arm['hook'], arm['bridge']):
            if file_sha256(pin['path']) != pin['sha256']:
                raise ValueError('original_arms_recall_mismatch')
        record[label] = dict(recall_mode=arm['recall_mode'], semantic_fallback=arm['semantic_fallback'],
                             recall_revision=arm['recall_revision'], hook_sha256=arm['hook']['sha256'],
                             bridge_sha256=arm['bridge']['sha256'])
        configs[label] = dict(record[label], bridge=str(bridge), hook=str(hook))
    return dict(controls=dict(path=path, sha256=sha256), arms=record), configs


def load_identity(path, sha256):
    """The typed declared backend identity; its file digest is a pin, never a model hash."""
    try:
        values = identity_declaration.typed(paired.read_json_pin(dict(path=path, sha256=sha256)))
    except paired.Refusal as refusal:
        if str(refusal) in ('pin_mismatch', 'pin_shape', 'pin_digest', 'absolute_path_required', 'path_indirection',
                            'regular_file_required', 'file_limit'):
            raise ValueError('original_embedding_identity_unavailable') from None
        raise ValueError('original_execution_unsupported:model_identity_declaration') from None  # opaque bytes
    except ValueError:
        raise ValueError('original_execution_unsupported:model_identity_declaration') from None
    return dict(declaration=dict(path=path, sha256=sha256), declared=values), values


def observed_arm(config):
    """Re-hash the hook and bridge that this attempt mounts; refuse any change since loading."""
    hook, bridge = file_sha256(config['hook']), file_sha256(config['bridge'])
    if (hook, bridge) != (config['hook_sha256'], config['bridge_sha256']):
        raise RuntimeError('original recall source changed before launch')
    return {k: config[k] for k in ('recall_mode', 'semantic_fallback', 'recall_revision', 'hook_sha256',
                                   'bridge_sha256')}
