"""Semantic-fallback selector identity for the original legacy route (CAIRN-107).

An ambient arm with ``semantic_fallback: true`` makes the pinned hook call a
tool-free selector: ``memory.select_json`` runs ``config["claude"]`` with
``--model selector_model(config, stage)`` for the ``preview`` and ``recall``
stages. ``cairn.weekly-execution/1`` declares neither that binary nor that
model. Root selected (2026-10-21) a new ``cairn.weekly-execution/2`` whose
memory arms add one ``semantic_selector`` pin: required for an ambient arm with
``semantic_fallback``, null otherwise. ``/1`` stays exactly as it is.

The pin's content is the closed ``cairn.semantic-selector-identity/1``
declaration (``typed``): a ``binary`` ``{path, sha256}`` pin and one ``model``.

This module only supplies the pieces later wiring needs. It changes no caller,
and ``original_execution_unsupported:semantic_fallback_selector`` stays until
full-boundary wiring and tests exist:

- ``execution_arm_fields`` / ``check_arm_selector``: explicit /1 and /2 arm
  shapes and the /2 selector rule;
- ``load_declaration``: the typed declaration from its pin;
- ``retain`` / ``mount``: a retained read-only copy of the declared binary,
  re-hashed before each attempt and bound at ``TARGET``;
- ``hook_config`` / ``selector_argv``: the hook configuration and the exact
  selector argv of a reviewed pinned hook, derived statically (the hook is
  never imported or run on the host);
- ``registered_events``: the capture side effect of ``config["model"]``;
- ``parse_observations`` / ``classify_attempt``: one attempt's schema-checked
  hook rows, classified on their own.
- ``check_owner_executions``: the proposed owner-launched engine's per-attempt
  ledger (docs/weekly-runner-reconciliation.md); it authenticates nothing alone.

``requested_model`` is the ``--model`` argument the hook passed. It is never the
served model. The served model and the provider account are never observed.
"""
import ast
import collections
import hashlib
import json
import os
from pathlib import Path

import trial_native_copy as native_copy
import trial_paired_request as paired

SCHEMA = 'cairn.semantic-selector-identity/1'
EXECUTION_V1 = 'cairn.weekly-execution/1'
EXECUTION_V2 = 'cairn.weekly-execution/2'
V1_ARM_FIELDS = ('binary', 'hook', 'bridge', 'api_revision', 'recall_revision', 'recall_mode', 'semantic_fallback',
                 'embedding_worker', 'embedding_config', 'embedding_model_identity')
ARM_FIELDS = {EXECUTION_V1: V1_ARM_FIELDS, EXECUTION_V2: V1_ARM_FIELDS + ('semantic_selector',)}
TARGET = '/tmp/trial/selector/claude'  # the legacy harness's selector mount target
STAGES = (('preview_process', 'preview'), ('model_process', 'recall'))
NOT_OBSERVED = ('served_model', 'provider_account')

# trial_hook_observer's closed vocabulary for cairn.task-hook-observation/1.
OBSERVATION_SCHEMA = 'cairn.task-hook-observation/1'
OBSERVER_EVENTS = ('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure', 'PreCompact',
                   'SessionEnd', 'Stop', 'unknown')
RECALL_EVENTS = ('SessionStart', 'UserPromptSubmit')  # memory.handle_event calls recall only for these
CAPTURE_EVENTS = ('PreCompact', 'SessionEnd', 'Stop')  # memory.handle_event calls capture for these
RECALL_KEYS = frozenset((
    'outcome', 'bytes', 'discovery', 'optional_deferred', 'coverage', 'inspected', 'rejected',
    'semantic_pages', 'semantic_previews', 'preview_count', 'preview_input_bytes',
    'preview_seconds', 'preview_reported_cost_usd', 'preview_receipt_budget_dropped',
    'preview_admitted', 'receipt_attempts', 'receipt_pull_calls', 'pull_seconds',
    'selector_input_bytes', 'shortlist_candidates', 'shortlist_source_extents',
    'model_seconds', 'model_reported_cost_usd', 'source_extent', 'elapsed_seconds',
    'preview_process', 'model_process', 'duration_ms', 'error_type', 'recall_task_scope',
    'search_discovery'))
# Keys memory.bounded_command and memory.select_json write into a process record.
PROCESS_REQUIRED = frozenset(('requested_model', 'outcome', 'elapsed_ms'))
PROCESS_NUMBERS = frozenset(('spawn_ms', 'first_stdout_ms', 'first_stderr_ms', 'elapsed_ms', 'stdout_bytes',
                             'stderr_bytes', 'reported_duration_ms', 'reported_api_duration_ms',
                             'reported_cost_usd'))
PROCESS_KEYS = PROCESS_REQUIRED | PROCESS_NUMBERS | {'returncode', 'usage'}
PROCESS_OUTCOMES = ('incomplete', 'completed', 'timeout', 'spawn_failed', 'communication_failed',
                    'nonzero_exit', 'invalid_json', 'reported_error')
OBSERVATION_BYTES = 16 * 1024 * 1024

# Channels that authenticate the hook observation bytes. None exists in current
# source: the observer writes observations.jsonl inside the read-write hookstate
# mount that the actor's own tools can also write. Until root accepts a channel,
# every attempt refuses semantic_selector_capture_unauthenticated.
AUTHENTICATION_METHODS = frozenset()


def _model_name(value):
    # The hook's own selector_model rule: nonempty, at most 256, no whitespace or controls.
    return (type(value) is str and 0 < len(value) <= 256 and value == value.strip()
            and not any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in value))


def _sha256(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _number(value):
    return type(value) in (int, float) and value == value and value not in (float('inf'), float('-inf')) and value >= 0


# ---------------------------------------------------------------- declaration

def typed(document):
    """Return the typed declared values, or raise ValueError('semantic_selector_declaration')."""
    if not (isinstance(document, dict) and set(document) == {'schema', 'binary', 'model'}
            and document['schema'] == SCHEMA and _model_name(document['model'])
            and isinstance(document['binary'], dict) and set(document['binary']) == {'path', 'sha256'}
            and type(document['binary']['path']) is str and document['binary']['path'].startswith('/')
            and '\0' not in document['binary']['path'] and _sha256(document['binary']['sha256'])):
        raise ValueError('semantic_selector_declaration')
    return dict(binary=dict(document['binary']), model=document['model'])


def load_declaration(reference):
    """Read the pinned declaration file; its own SHA-256 is a pin, never the binary's."""
    try:
        document = paired.read_json_pin(reference)
    except paired.Refusal:
        raise ValueError('semantic_selector_unavailable') from None
    return typed(document)


def execution_arm_fields(schema):
    """Explicit per-schema memory-arm fields; /1 is unchanged and never gains defaults."""
    if schema not in ARM_FIELDS:
        raise ValueError('weekly_execution_schema')
    return ARM_FIELDS[schema]


def selector_required(arm):
    return arm['recall_mode'] == 'ambient' and arm['semantic_fallback'] is True


def check_arm_selector(schema, arm):
    """The /2 rule: a pin exactly when ambient fallback calls the selector, null otherwise.

    The pin's shape is checked here; its bytes are checked by ``load_declaration``.
    """
    fields = execution_arm_fields(schema)
    if not (isinstance(arm, dict) and set(arm) == set(fields) and arm['recall_mode'] in ('ambient', 'agent_tools')
            and type(arm['semantic_fallback']) is bool):
        raise ValueError('weekly_arm_shape')
    if schema == EXECUTION_V1:
        return None
    reference = arm['semantic_selector']
    if not selector_required(arm):
        if reference is not None:
            raise ValueError('weekly_selector_forbidden')
        return None
    if reference is None:
        raise ValueError('weekly_selector_required')
    try:
        paired.pin_shape(reference)
    except paired.Refusal:
        raise ValueError('weekly_selector_pin') from None
    if not (type(reference['path']) is str and reference['path'].startswith('/') and '\0' not in reference['path']):
        raise ValueError('weekly_selector_pin')
    return dict(reference)


# ---------------------------------------------------------------- binary custody

def retain(declared, out):
    """Copy the declared selector to ``out/selector/claude`` (0500) and re-hash the copy.

    Nothing is executed. A source that changed before or during the copy refuses.
    """
    path, sha256 = declared['binary']['path'], declared['binary']['sha256']
    try:
        digest, size = native_copy._hash(path)
    except (OSError, ValueError):
        raise ValueError('semantic_selector_binary_unobserved') from None
    if digest != sha256:
        raise ValueError('semantic_selector_binary_mismatch')
    directory = Path(out) / 'selector'
    directory.mkdir(mode=0o700)
    copied = directory / 'claude'
    try:
        native_copy._copy(path, copied, size)
        os.chmod(copied, 0o500)
        if native_copy.file_sha256(copied) != sha256:
            raise ValueError('semantic_selector_binary_mismatch')
    except BaseException:
        copied.unlink(missing_ok=True)
        raise
    return dict(declared=dict(declared['binary']), source=str(copied), sha256=sha256, target=TARGET)


def mount(retained):
    """Re-hash the retained copy just before an attempt; return its read-only bind and observed identity."""
    try:
        observed = native_copy.file_sha256(retained['source'])
    except (OSError, ValueError):
        observed = None
    if observed != retained['sha256']:
        raise ValueError('semantic_selector_binary_changed')
    return dict(bind=(retained['source'], retained['target'], 'ro'),
                observed=dict(source=retained['source'], target=retained['target'], sha256=observed))


# ---------------------------------------------------------------- configuration and argv

def hook_config(config, declared, target=TARGET):
    """The arm's hook configuration with the declared selector; never a silent override.

    The hook reads ``preview_model``/``recall_model`` before ``model``, so either
    one present would replace the declared model and refuses.
    """
    if {'preview_model', 'recall_model'} & set(config):
        raise ValueError('semantic_selector_config_conflict')
    for key, value in (('claude', target), ('model', declared['model'])):
        if key in config and config[key] != value:
            raise ValueError('semantic_selector_config_conflict')
    return dict(config, claude=target, model=declared['model'])


# The reviewed selector argv contract of memory.select_json / selector_model.
# ('config', 'claude') is the configured selector path; ('argument', name) is a
# per-call value select_json fills in (the JSON schema and the system prompt).
ARGV_CONTRACT = dict(
    argv=(('config', 'claude'), '--print', '--output-format', 'json', '--disable-slash-commands',
          '--no-session-persistence', '--setting-sources', '', '--settings', '{"disableAllHooks":true}',
          '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--tools', '', '--max-turns', '2',
          '--json-schema', ('argument', 'schema'), '--system-prompt', ('argument', 'prompt')),
    model_flag='--model',
    validated_keys=('preview_model', 'recall_model'),
    model_keys=dict(preview=('preview_model', 'recall_model', 'model'), recall=('recall_model', 'model'),
                    other=('model',)))
# SHA-256 of a reviewed memory.py -> the git revision it came from and its contract.
# The 43c4b61 entry is PROPOSED for root review; root adds or strikes entries.
REVIEWED_HOOKS = {
    '53d826f6ed396156de21b23865814f9f7e1db67051600c31ec40e8ef07efe896':
        dict(revision='43c4b6170f1f7784d70d55ca6669d770ab214ed1', contract=ARGV_CONTRACT),
}
HOOK_BYTES = 8 * 1024 * 1024


def _function(tree, name):
    found = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(found) != 1:
        raise ValueError('semantic_selector_argv')
    return found[0]


def _strings(node):
    if not (isinstance(node, ast.Tuple) and all(isinstance(e, ast.Constant) and type(e.value) is str
                                                  for e in node.elts)):
        raise ValueError('semantic_selector_argv')
    return tuple(e.value for e in node.elts)


def _argv_element(node):
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == 'config'
            and isinstance(node.slice, ast.Constant) and node.slice.value == 'claude'):
        return ('config', 'claude')
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'encoded'
            and len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Name)
            and node.args[0].id == 'schema'):
        return ('argument', 'schema')
    if isinstance(node, ast.Name) and node.id == 'prompt':
        return ('argument', 'prompt')
    raise ValueError('semantic_selector_argv')


def argv_contract(source):
    """Statically extract the selector argv contract from hook source; nothing is executed.

    Only ``ast.parse`` reads the source. Any construction outside the reviewed
    form refuses ``semantic_selector_argv``.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        raise ValueError('semantic_selector_argv') from None
    select = _function(tree, 'select_json')
    names = [a.arg for a in select.args.args]
    if names[:4] != ['config', 'schema', 'prompt', 'excerpt'] or 'stage' not in names:
        raise ValueError('semantic_selector_argv')
    writes = [n for n in ast.walk(select)
              if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign))
              and any(isinstance(t, ast.Name) and t.id == 'command'
                      for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
    mutations = [n for n in ast.walk(select) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                 and n.value.id == 'command']
    if len(writes) != 2 or mutations:
        raise ValueError('semantic_selector_argv')
    initial, extension = writes
    if not (isinstance(initial, ast.Assign) and isinstance(initial.value, ast.List)):
        raise ValueError('semantic_selector_argv')
    argv = tuple(_argv_element(e) for e in initial.value.elts)
    model_assign = [n for n in select.body if isinstance(n, ast.Assign) and len(n.targets) == 1
                    and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'model']
    guarded = [n for n in select.body if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
               and n.test.id == 'model' and not n.orelse and n.body == [extension]]
    if not (len(model_assign) == 1 and len(guarded) == 1 and isinstance(extension, ast.AugAssign)
            and isinstance(extension.op, ast.Add) and isinstance(extension.value, ast.List)
            and len(extension.value.elts) == 2 and isinstance(extension.value.elts[0], ast.Constant)
            and isinstance(extension.value.elts[1], ast.Name) and extension.value.elts[1].id == 'model'):
        raise ValueError('semantic_selector_argv')
    call = model_assign[0].value
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'selector_model'
            and [a.id for a in call.args if isinstance(a, ast.Name)] == ['config', 'stage'] and len(call.args) == 2):
        raise ValueError('semantic_selector_argv')
    resolver = _function(tree, 'selector_model')
    loops = [n for n in resolver.body if isinstance(n, ast.For)]
    if len(loops) != 2:
        raise ValueError('semantic_selector_argv')
    validated, chain = loops[0].iter, loops[1].iter
    body = loops[1].body
    # The resolution loop must be exactly: if config.get(key): return config[key]
    if not (len(body) == 1 and isinstance(body[0], ast.If) and not body[0].orelse
            and ast.dump(body[0].test) == ast.dump(ast.parse('config.get(key)', mode='eval').body)
            and len(body[0].body) == 1 and isinstance(body[0].body[0], ast.Return)
            and ast.dump(body[0].body[0].value) == ast.dump(ast.parse('config[key]', mode='eval').body)
            and ast.dump(resolver.body[-1]) == ast.dump(ast.parse('return None').body[0])):
        raise ValueError('semantic_selector_argv')
    model_keys = {}
    while isinstance(chain, ast.IfExp):
        test = chain.test
        if not (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == 'stage'
                and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)
                and isinstance(test.comparators[0], ast.Constant) and type(test.comparators[0].value) is str):
            raise ValueError('semantic_selector_argv')
        model_keys[test.comparators[0].value] = _strings(chain.body)
        chain = chain.orelse
    model_keys['other'] = _strings(chain)
    return dict(argv=argv, model_flag=extension.value.elts[0].value, validated_keys=_strings(validated),
                model_keys=model_keys)


def selector_argv(hook_path, config, declared, target=TARGET):
    """The selector argv the reviewed pinned hook builds from ``config``, without loading the hook.

    The hook bytes are hashed first: unreviewed source refuses
    ``semantic_selector_hook_unreviewed`` before it is even parsed. Reviewed
    source is parsed statically (never imported or run) and must still yield
    its reviewed contract. ``('argument', name)`` slots are per-call values.
    """
    try:
        with open(hook_path, 'rb') as source:
            data = source.read(HOOK_BYTES + 1)
    except OSError:
        raise ValueError('semantic_selector_hook_unreviewed') from None
    review = REVIEWED_HOOKS.get(hashlib.sha256(data).hexdigest()) if len(data) <= HOOK_BYTES else None
    if review is None:
        raise ValueError('semantic_selector_hook_unreviewed')
    contract = argv_contract(data)
    if contract != review['contract']:
        raise ValueError('semantic_selector_argv')
    if config.get('claude') != target or any(key in config for key in contract['validated_keys']):
        raise ValueError('semantic_selector_argv')
    result = {}
    for stage in ('preview', 'recall'):
        keys = contract['model_keys'].get(stage, contract['model_keys']['other'])
        model = next((config[key] for key in keys if config.get(key)), None)
        if model != declared['model']:
            raise ValueError('semantic_selector_argv')
        argv = [target if part == ('config', 'claude') else list(part) if isinstance(part, tuple) else part
                for part in contract['argv']]
        result[stage] = argv + [contract['model_flag'], model]
    return dict(revision=review['revision'], argv=result)


def registered_events(events):
    """Account for the capture consumer of ``config["model"]`` before effects.

    ``memory.capture`` calls ``select_json(..., stage="capture")``, which uses
    ``config["model"]`` and records no process observation. The legacy route
    registers no capture event, so its capture demand is zero. A registration
    that would add capture demand refuses instead of silently charging the
    selector model with it.
    """
    events = tuple(events)
    if set(events) & set(CAPTURE_EVENTS):
        raise ValueError('semantic_selector_capture_demand')
    return dict(registered=list(events), capture_events=[], capture_demand=0)


# ---------------------------------------------------------------- observation

def _process(record):
    """A typed selector process record, None when no call started, or ValueError."""
    if not isinstance(record, dict) or not set(record) <= PROCESS_KEYS:
        raise ValueError('semantic_selector_observation_malformed')
    if not record:
        return None  # select_json never reached the child (an invalid model setting or no time left)
    if not PROCESS_REQUIRED - {'requested_model'} <= set(record):
        raise ValueError('semantic_selector_observation_malformed')
    requested = record.get('requested_model')
    if (not (requested is None or type(requested) is str) or record['outcome'] not in PROCESS_OUTCOMES
            or not all(_number(record[k]) for k in PROCESS_NUMBERS & set(record))
            or not (record.get('returncode') is None or type(record['returncode']) is int)
            or ('usage' in record and not isinstance(record['usage'], dict))):
        raise ValueError('semantic_selector_observation_malformed')
    return dict(requested_model=requested, requested_model_observed='requested_model' in record,
                outcome=record['outcome'])


def parse_observations(data):
    """Schema-check every hook observation row of one attempt, then extract selector calls.

    ``data`` is the raw observations.jsonl bytes. Any malformed row refuses the
    whole attempt with ValueError('semantic_selector_observation_malformed').
    """
    if type(data) is not bytes or len(data) > OBSERVATION_BYTES:
        raise ValueError('semantic_selector_observation_malformed')
    rows, calls = [], []
    try:
        lines = data.decode('utf-8').splitlines()
        parsed = [json.loads(line, object_pairs_hook=paired.unique_object) for line in lines]
    except (UnicodeError, ValueError):
        raise ValueError('semantic_selector_observation_malformed') from None
    for index, row in enumerate(parsed):
        if not (isinstance(row, dict) and row.get('schema') == OBSERVATION_SCHEMA
                and row.get('event') in OBSERVER_EVENTS and type(row.get('recall_attempted')) is bool
                and 'recall' in row and _sha256(row.get('stdout_sha256'))
                and (row.get('exit_code') is None or type(row.get('exit_code')) is int)):
            raise ValueError('semantic_selector_observation_malformed')
        recall = row['recall']
        if recall is not None and not (row['recall_attempted'] and row['event'] in RECALL_EVENTS
                                       and isinstance(recall, dict) and set(recall) <= RECALL_KEYS):
            raise ValueError('semantic_selector_observation_malformed')
        rows.append(dict(event=row['event'], stdout_sha256=row['stdout_sha256']))
        for key, stage in STAGES:
            if recall is not None and key in recall:
                call = _process(recall[key])
                if call is not None:
                    calls.append(dict(row=index, stage=stage, **call))
    return dict(rows=rows, calls=calls, sha256=hashlib.sha256(data).hexdigest())


def _native_complete(rows, native_hooks):
    """Every native hook started and finished, one-to-one with an observer row by (event, stdout_sha256)."""
    if not isinstance(native_hooks, list):
        return False
    identities = []
    for hook in native_hooks:
        if not (isinstance(hook, dict) and set(hook) == {'hook_id', 'event', 'stdout_sha256'}
                and type(hook['hook_id']) is str and hook['hook_id'] and type(hook['event']) is str
                and _sha256(hook['stdout_sha256'])):
            return False  # an unfinished hook has no stdout digest
        identities.append((hook['event'], hook['stdout_sha256']))
    if len({hook['hook_id'] for hook in native_hooks}) != len(native_hooks):
        return False
    observed = collections.Counter((row['event'], row['stdout_sha256']) for row in rows)
    return (observed == collections.Counter(identities)
            and set(RECALL_EVENTS) <= {row['event'] for row in rows})


def _authenticated(authentication, digest):
    return (isinstance(authentication, dict) and set(authentication) == {'method', 'observations_sha256'}
            and authentication['method'] in AUTHENTICATION_METHODS
            and authentication['observations_sha256'] == digest)


def classify_attempt(declared, *, mounted, observations, native_hooks, authentication):
    """Classify one attempt from its own evidence only.

    - ``mounted``: this attempt's ``mount(...)['observed']``, or None.
    - ``observations``: this attempt's raw observations.jsonl bytes, or None.
    - ``native_hooks``: this attempt's native ``hook_response`` identities
      ``{hook_id, event, stdout_sha256}``; an unfinished hook has no digest.
    - ``authentication``: ``{method, observations_sha256}`` from an accepted
      channel, or None.

    ``result`` is ``matched``, ``not_invoked`` (a complete, authenticated
    capture with zero selector calls; the model stays unobserved),
    ``mismatched`` or ``incomplete``. Refusals name every failed part.
    """
    refusals = []
    binary = mounted.get('sha256') if isinstance(mounted, dict) else None
    if binary is None:
        refusals.append('semantic_selector_binary_unobserved')
    elif binary != declared['binary']['sha256']:
        refusals.append('semantic_selector_binary_mismatch')
    parsed = None
    if observations is None:
        refusals.append('semantic_selector_capture_incomplete')
    else:
        try:
            parsed = parse_observations(observations)
        except ValueError:
            refusals.append('semantic_selector_observation_malformed')
    if parsed is not None:
        if any(row['event'] in CAPTURE_EVENTS or row['event'] == 'unknown' for row in parsed['rows']):
            refusals.append('semantic_selector_capture_demand')
        if not _native_complete(parsed['rows'], native_hooks):
            refusals.append('semantic_selector_capture_incomplete')
        if not _authenticated(authentication, parsed['sha256']):
            refusals.append('semantic_selector_capture_unauthenticated')
    calls = parsed['calls'] if parsed is not None else None
    model = 'unobserved'
    if calls:
        if not all(call['requested_model_observed'] for call in calls):
            refusals.append('semantic_selector_model_unobserved')
        elif any(call['requested_model'] != declared['model'] for call in calls):
            refusals.append('semantic_selector_model_mismatch')
            model = 'requested_mismatch'
        else:
            model = 'requested_matches'
    if any(code.endswith('_mismatch') for code in refusals):
        result = 'mismatched'
    elif refusals:
        result = 'incomplete'
    else:
        result = 'matched' if calls else 'not_invoked'
    return dict(declared=declared,
                observed=dict(binary_sha256=binary, calls=calls,
                              observations_sha256=parsed['sha256'] if parsed is not None else None),
                result=result, model=model, refusals=refusals, not_observed=list(NOT_OBSERVED))


# ---------------------------------------------------------------- owner-launched engine (proposed)

LEDGER_SCHEMA = 'cairn.selector-owner-execution/1'
BINDINGS = ('engine_sha256', 'observer_sha256', 'config_sha256', 'selector_sha256')
TIMES = ('frame_received_seconds', 'deadline_seconds', 'completed_seconds')
LEDGER_FIELDS = frozenset(('schema', 'attempt_id', 'sequence', 'event', 'hook_input_sha256', 'stdout_sha256',
                           'exit_code', 'observation_sha256') + BINDINGS + TIMES)


def check_owner_executions(ledger, observations, *, attempt_id, bindings):
    """Check the proposed owner ledger of engine executions for one attempt.

    In the proposed owner-launched model the owner, not the actor, runs the
    engine for every hook signal and appends one ``cairn.selector-owner-execution/1``
    row per execution, outside every actor mount. Each row must:
    - belong to ``attempt_id``, in contiguous ``sequence`` order;
    - carry exactly the reviewed ``bindings`` (engine, observer, config and
      selector SHA-256 values the owner mounted);
    - bind the observation line at the same position by its SHA-256, event and
      stdout digest.

    It returns the refusal codes; an empty list is not authentication by itself.
    Native correlation (``classify_attempt``) still decides completeness, and no
    method is added to ``AUTHENTICATION_METHODS`` until the owner wiring exists
    and root accepts it.
    """
    if not (isinstance(bindings, dict) and set(bindings) == set(BINDINGS)
            and all(_sha256(bindings[k]) for k in BINDINGS)):
        raise ValueError('semantic_selector_owner_bindings')
    if not (isinstance(ledger, list) and type(observations) is bytes):
        return ['semantic_selector_owner_ledger_malformed']
    lines = observations.splitlines()
    refusals = []
    for index, row in enumerate(ledger):
        if not (isinstance(row, dict) and set(row) == LEDGER_FIELDS and row['schema'] == LEDGER_SCHEMA
                and type(row['sequence']) is int and row['event'] in OBSERVER_EVENTS
                and all(_sha256(row[k]) for k in ('hook_input_sha256', 'stdout_sha256', 'observation_sha256'))
                and (row['exit_code'] is None or type(row['exit_code']) is int)
                and all(_number(row[k]) for k in TIMES)
                and row['frame_received_seconds'] <= row['completed_seconds']):
            return ['semantic_selector_owner_ledger_malformed']
        if row['attempt_id'] != attempt_id or row['sequence'] != index:
            code = 'semantic_selector_owner_attempt_mismatch'
        elif any(row[k] != bindings[k] for k in BINDINGS):
            code = 'semantic_selector_owner_binding_mismatch'
        elif index >= len(lines) or hashlib.sha256(lines[index]).hexdigest() != row['observation_sha256']:
            code = 'semantic_selector_owner_observation_unbound'
        else:
            try:
                line = json.loads(lines[index])
            except ValueError:
                line = None
            code = (None if isinstance(line, dict) and line.get('event') == row['event']
                    and line.get('stdout_sha256') == row['stdout_sha256']
                    else 'semantic_selector_owner_observation_unbound')
        if code and code not in refusals:
            refusals.append(code)
    if len(lines) != len(ledger) and 'semantic_selector_owner_observation_unbound' not in refusals:
        refusals.append('semantic_selector_owner_observation_unbound')
    return refusals


# Native hook fields observed in retained Claude CLI streams (keys only):
#   hook_started:  type subtype hook_id hook_name hook_event session_id uuid
#   hook_response: the same plus stdout stderr output exit_code outcome
# Neither carries the hook's input bytes, an input digest or a CLI timestamp.
# The only clock is the owner's receive time of each stream line
# (trial_native_capture.capture timings, seconds since capture start).
NATIVE_INPUT_FIELD = 'input_sha256'  # NOT emitted by the CLI today; named as the missing observation
HOOK_SECONDS = 5  # original LIMITS['hook_seconds'] and the legacy command-hook timeout


def _native_hooks(events):
    """Pair native hook events by hook_id: {id: (event, started_s, response_s, stdout_sha256, input_sha256)}."""
    if not isinstance(events, list):
        raise ValueError('semantic_selector_native_malformed')
    started, finished = {}, {}
    for item in events:
        if not (isinstance(item, dict) and item.get('subtype') in ('hook_started', 'hook_response')
                and type(item.get('hook_id')) is str and item['hook_id']
                and item.get('hook_event') in OBSERVER_EVENTS and _number(item.get('received_seconds'))):
            raise ValueError('semantic_selector_native_malformed')
        target = started if item['subtype'] == 'hook_started' else finished
        if item['hook_id'] in target:
            raise ValueError('semantic_selector_native_malformed')
        if item['subtype'] == 'hook_started':
            anchor = item.get(NATIVE_INPUT_FIELD)
            if anchor is not None and not _sha256(anchor):
                raise ValueError('semantic_selector_native_malformed')
            started[item['hook_id']] = (item['hook_event'], item['received_seconds'], anchor)
        else:
            if type(item.get('stdout')) is not str:
                raise ValueError('semantic_selector_native_malformed')
            finished[item['hook_id']] = (item['hook_event'], item['received_seconds'],
                                         hashlib.sha256(item['stdout'].encode()).hexdigest())
    if set(started) != set(finished):
        raise ValueError('semantic_selector_native_unfinished')
    hooks = {}
    for ident, (event, start, anchor) in started.items():
        end_event, end, stdout = finished[ident]
        if end_event != event or end < start:
            raise ValueError('semantic_selector_native_malformed')
        hooks[ident] = dict(event=event, started=start, responded=end, stdout_sha256=stdout, input_sha256=anchor)
    return hooks


def _perfect_matching(rows, hooks, fits, banned=None):
    """One perfect row->hook assignment under ``fits``, or None (augmenting paths)."""
    if len(rows) != len(hooks):
        return None
    owner = {}

    def place(index, seen):
        for ident in hooks:
            if ident not in seen and (index, ident) != banned and fits(rows[index], hooks[ident]):
                seen.add(ident)
                if ident not in owner or place(owner[ident], seen):
                    owner[ident] = index
                    return True
        return False
    if not all(place(i, set()) for i in range(len(rows))):
        return None
    return {index: ident for ident, index in owner.items()}


def correlate_native(ledger, native_events, *, hook_seconds=HOOK_SECONDS):
    """Correlate owner executions with the CLI's own hook events; keep each claim separate.

    ``ledger`` rows are ``check_owner_executions``-valid rows whose times share
    the capture clock. ``native_events`` are the CLI's hook_started and
    hook_response events with the owner's ``received_seconds`` for each line.

    Matching is staged. Each stage looks for any one-to-one assignment under
    all constraints so far, and refuses only when none exists:
    1. ``output_correlated``: event and stdout digest;
    2. ``window_correlated``: each frame arrived after its hook_started line
       and finished before its hook_response line;
    3. ``deadline_from_native_start_receipt``: each deadline is that
       hook_started line's owner receipt time plus ``hook_seconds`` (never
       frame receipt plus ``hook_seconds``), and it completed by then. Receipt
       is later than the CLI's own spawn by an unobserved delay, so this is
       not the CLI's actual deadline;
    4. ``input``: ``anchored`` only when the CLI exposes an input digest on
       every hook and some assignment also matches it; ``mismatched`` when it
       exposes one and none does; ``unanchored`` otherwise.

    ``assignment`` is ``unique`` or ``ambiguous`` for the deepest stage
    reached (None when no stage matched). A match, unique or not, is never
    causal authentication. ``gaps`` names native observations that are missing.
    """
    claims = dict(output_correlated=False, window_correlated=False,
                  deadline_from_native_start_receipt=False, input='unanchored')
    try:
        hooks = _native_hooks(native_events)
    except ValueError as exc:
        return dict(claims=claims, pairs=None, assignment=None, refusals=[str(exc)], gaps=[])
    rows = list(ledger) if isinstance(ledger, list) else []
    anchored = bool(hooks) and all(hook['input_sha256'] is not None for hook in hooks.values())

    def output(row, hook):
        return (row['event'], row['stdout_sha256']) == (hook['event'], hook['stdout_sha256'])

    def window(row, hook):
        return (output(row, hook) and hook['started'] <= row['frame_received_seconds']
                <= row['completed_seconds'] <= hook['responded'])

    def deadline_set(row, hook):
        return window(row, hook) and row['deadline_seconds'] == hook['started'] + hook_seconds

    def deadline(row, hook):
        return deadline_set(row, hook) and row['completed_seconds'] <= row['deadline_seconds']

    def given(row, hook):
        return deadline(row, hook) and row['hook_input_sha256'] == hook['input_sha256']

    stages = [('output_correlated', output, 'semantic_selector_owner_output_uncorrelated'),
              ('window_correlated', window, 'semantic_selector_owner_window_uncorrelated'),
              ('deadline_from_native_start_receipt', deadline, None)]
    if anchored:
        stages.append(('input', given, 'semantic_selector_owner_input_mismatch'))
    refusals, pairs, fits = [], None, None
    for claim, test, code in stages:
        found = _perfect_matching(rows, hooks, test)
        if found is None:
            if claim == 'deadline_from_native_start_receipt':
                code = ('semantic_selector_owner_deadline_restarted'
                        if _perfect_matching(rows, hooks, deadline_set) is None
                        else 'semantic_selector_owner_deadline_exceeded')
            if claim == 'input':
                claims['input'] = 'mismatched'
            refusals.append(code)
            break
        claims[claim] = 'anchored' if claim == 'input' else True
        pairs, fits = found, test
    assignment = None
    if pairs is not None:
        # Another valid assignment exists iff some chosen pair can be excluded.
        ambiguous = any(_perfect_matching(rows, hooks, fits, banned=(index, ident)) is not None
                        for index, ident in pairs.items())
        assignment = 'ambiguous' if ambiguous else 'unique'
    gaps = ['native_hook_spawn_time_unobserved']  # hook_started receipt is later than the CLI's own start
    if not anchored:
        gaps.append('native_hook_input_unobserved')
    return dict(claims=claims, pairs=None if pairs is None else {rows[i]['sequence']: h for i, h in pairs.items()},
                assignment=assignment, refusals=refusals, gaps=gaps)
