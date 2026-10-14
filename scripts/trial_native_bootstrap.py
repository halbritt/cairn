"""Offline source-review preparation; deliberately not a complete bootstrap.

No command, account source, hook gateway, mount operation or admission route is
implemented. The returned owner must stay open while its review bindings are used.
"""
import hashlib
import json
from pathlib import Path
import re

from trial_bootstrap_snapshot import Snapshot, record_cleanup
from trial_coding_launch import CodingLaunch, compile_launch
from trial_paired_request import (IMPLEMENTATION_SETTINGS_SHA,
                                 IMPLEMENTATION_SOURCE_SHA, ORIGINAL_LIMITS,
                                 PIN_LIMIT, Refusal, canonical_sha, identifier,
                                 mcp_arguments, need, shape)

SCHEMA = 'cairn.native-bootstrap-review/1'
SOURCE_ROLES = {
    'cli': ('claude', PIN_LIMIT, True),
    'mcp_binary': ('cairn', PIN_LIMIT, True),
    'permission_source': ('trial_task_permissions.py', 65536, False),
    'prompt': ('prompt.txt', 9500, False),
    'instruction': ('instruction.txt', 9500, False),
    'settings': ('settings.json', 65536, False),
    'mcp': ('mcp.json', 65536, False),
}
GATES = ('hook_interface_unavailable', 'native_runtime_unobserved',
         'account_authority_unproved', 'campaign_wall_billing_unfrozen')


class PreparationRefusal(Refusal):
    def __init__(self, code, cleanup_faults=()):
        super().__init__(code)
        self.cleanup_faults = cleanup_faults


def sha(text):
    need(isinstance(text, str) and re.fullmatch('[a-f0-9]{64}', text),
         'bootstrap_sha256')


def _unique(pairs):
    decoded = {}
    for key, entry in pairs:
        need(key not in decoded, 'bootstrap_json')
        decoded[key] = entry
    return decoded


def _json(content):
    try:
        return json.loads(content.decode('utf-8'), object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refusal('bootstrap_json')))
    except (UnicodeError, ValueError, RecursionError):
        raise Refusal('bootstrap_json') from None


def _text(content):
    try:
        return content.decode('utf-8')
    except UnicodeError:
        raise Refusal('bootstrap_text') from None


def _freeze(document):
    """Copy only bounded plain JSON values before any schema validator sees them."""
    nodes, text_bytes = 0, 0
    def clone(entry, depth):
        nonlocal nodes, text_bytes
        nodes += 1
        need(nodes <= 512 and depth <= 8, 'bootstrap_graph_limit')
        if type(entry) is str:
            need(len(entry) <= 4096, 'bootstrap_graph_limit')
            text_bytes += len(entry.encode('utf-8'))
            need(text_bytes <= 65536, 'bootstrap_graph_limit')
            return entry
        if entry is None or type(entry) in (bool, int):
            need(type(entry) is not int or entry.bit_length() <= 64,
                 'bootstrap_graph_limit')
            return entry
        if type(entry) is dict:
            need(len(entry) <= 32, 'bootstrap_graph_limit')
            need(all(type(key) is str for key in entry), 'bootstrap_plain_value')
            return {clone(key, depth + 1): clone(value, depth + 1)
                    for key, value in entry.items()}
        if type(entry) is list:
            need(len(entry) <= 64, 'bootstrap_graph_limit')
            return [clone(value, depth + 1) for value in entry]
        raise Refusal('bootstrap_plain_value')
    return clone(document, 0)


def _declaration(document):
    shape(document, 'schema binding launch sources mcp_binding limits permission_sha256 hook_interface',
          'bootstrap_shape')
    need(document['schema'] == SCHEMA, 'bootstrap_schema')
    shape(document['binding'], 'request_id occurrence_id case_id arm session_id workspace_sha256 request_sha256 source_set_sha256',
          'bootstrap_binding')
    binding = document['binding']
    for name in ('request_id', 'occurrence_id', 'session_id'):
        identifier(binding[name])
    need(isinstance(binding['case_id'], str) and binding['case_id'] in
         {'T1', 'T2', 'T3', 'T4', 'T5', 'T7', 'T8'}, 'bootstrap_case')
    need(isinstance(binding['arm'], str) and binding['arm'] in {'CURRENT', 'CAND'},
         'bootstrap_arm')
    for name in ('workspace_sha256', 'request_sha256', 'source_set_sha256'):
        sha(binding[name])
    shape(document['launch'], 'model effort', 'bootstrap_launch')
    need(document['limits'] == ORIGINAL_LIMITS and
         all(type(count) is int for count in document['limits'].values()), 'limits_changed')
    sha(document['permission_sha256'])
    need(document['permission_sha256'] == IMPLEMENTATION_SETTINGS_SHA,
         'permission_settings_mismatch')
    # There is no reviewed complete producer contract to interpret or execute.
    need(document['hook_interface'] is None, 'hook_interface_unavailable')
    need(isinstance(document['sources'], dict)
         and set(document['sources']) == set(SOURCE_ROLES), 'bootstrap_sources')
    for role, reference in document['sources'].items():
        shape(reference, 'path sha256', 'bootstrap_source_reference')
        need(isinstance(reference['path'], str) and 0 < len(reference['path']) <= 4096
             and Path(reference['path']).name == SOURCE_ROLES[role][0], 'bootstrap_source_path')
        sha(reference['sha256'])
    need(canonical_sha({role: reference['sha256'] for role, reference in
                        document['sources'].items()}) == binding['source_set_sha256'],
         'bootstrap_source_set')
    need(document['sources']['permission_source']['sha256'] == IMPLEMENTATION_SOURCE_SHA,
         'permission_source_mismatch')
    return mcp_arguments(document['mcp_binding'])


def _compile(snapshot, document, mcp_args):
    instruction = _text(snapshot.read('instruction.txt'))
    need('\0' not in instruction, 'bootstrap_instruction')
    try:
        launch = compile_launch(session_id=document['binding']['session_id'],
                                model=document['launch']['model'],
                                effort=document['launch']['effort'],
                                prompt=_text(snapshot.read('prompt.txt')),
                                memory_instruction=instruction)
    except ValueError as error:
        # compile_launch and the text reader expose fixed codes only.
        raise Refusal(str(error)) from None
    need(type(launch) is CodingLaunch, 'bootstrap_launch_profile')
    settings = _json(snapshot.read('settings.json'))
    shape(settings, 'permissions hooks autoMemoryEnabled claudeMdExcludes', 'bootstrap_settings')
    need(settings['permissions'] == _json(launch.permission_json)
         and canonical_sha(settings['permissions']) == document['permission_sha256'],
         'permission_settings_mismatch')
    need(settings['hooks'] == {} and settings['autoMemoryEnabled'] is False
         and settings['claudeMdExcludes'] == ['**'], 'bootstrap_settings')
    mcp = _json(snapshot.read('mcp.json'))
    expected = dict(mcpServers=dict(cairn=dict(type='stdio', command='/native/cairn',
                                               args=mcp_args)))
    need(mcp == expected, 'bootstrap_mcp')
    snapshot.generated('input.jsonl', launch.input_message)
    return launch


class ReviewPreparation:
    """Own copied sources and compiled declarations, never an execution capability."""
    def __init__(self, snapshot, launch, declaration):
        need(type(launch) is CodingLaunch, 'bootstrap_launch_profile')
        self._snapshot = snapshot
        self._launch = launch
        self._launch_sha = canonical_sha(list(launch.argv))
        self._hook_context_bytes = launch.hook_context_bytes
        self._binding = dict(declaration['binding'])
        self._declaration_sha = canonical_sha(declaration)

    @property
    def launch(self):
        return self._launch

    def review_bindings(self):
        try:
            sources = self._snapshot.bindings()
            need(self._snapshot.read('input.jsonl') == self._launch.input_message
                 and hashlib.sha256(self._launch.permission_json).hexdigest()
                 == IMPLEMENTATION_SETTINGS_SHA
                 and self._launch.hook_context_bytes == self._hook_context_bytes
                 and canonical_sha(list(self._launch.argv)) == self._launch_sha
                 and self._launch.argv[-1].encode('utf-8')
                 == self._snapshot.read('instruction.txt'), 'compiled_binding_changed')
        except (OSError, Refusal):
            cleanup = self.close()
            raise PreparationRefusal('review_custody_unknown', cleanup) from None
        except BaseException as primary:
            record_cleanup(primary, self.close())
            raise
        return dict(schema=SCHEMA, status='source_prepared_for_review',
                    complete_bootstrap=False, runtime_attested=False,
                    missing_gates=list(GATES), binding=dict(self._binding),
                    declaration_sha256=self._declaration_sha,
                    snapshots=sources,
                    launch_sha256=canonical_sha(list(self.launch.argv)),
                    input_bytes=len(self.launch.input_message),
                    input_sha256=hashlib.sha256(self.launch.input_message).hexdigest(),
                    instruction_sha256=sources['instruction.txt']['sha256'],
                    permissions_sha256=self.launch.permission_sha256,
                    hook_context_bytes=self.launch.hook_context_bytes,
                    limits=dict(ORIGINAL_LIMITS))

    def require_complete(self):
        self.review_bindings()
        raise PreparationRefusal('hook_interface_unavailable')

    def close(self):
        return self._snapshot.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, primary, traceback):
        cleanup = self.close()
        if cleanup:
            if primary is not None:
                record_cleanup(primary, cleanup)
                return False
            raise PreparationRefusal('descriptor_cleanup_unknown', cleanup)


def prepare_review(document, output):
    """Consume an exclusive private output before inspecting declared source files.

    Only explicit root-supplied selected sources are read. MCP binding paths are
    compiled as strings; socket/token/repository paths are never opened here.
    Source and snapshot identities remain held through review. No file in the
    snapshot is declared safe to mount for an actor, and no complete settings
    are fabricated in place of the missing gateway/producer interface.
    """
    snapshot = None
    try:
        snapshot = Snapshot(output)
        document = _freeze(document)
        mcp_args = _declaration(document)
        for role, (name, limit, executable) in SOURCE_ROLES.items():
            snapshot.copy(name, document['sources'][role], limit, executable)
        launch = _compile(snapshot, document, mcp_args)
        prepared = ReviewPreparation(snapshot, launch, document)
        prepared.review_bindings()
        return prepared
    except FileExistsError:
        cleanup = snapshot.close() if snapshot else ()
        raise PreparationRefusal('preparation_consumed', cleanup) from None
    except (OSError, Refusal, UnicodeError, TypeError, ValueError, RecursionError) as error:
        cleanup = snapshot.close() if snapshot else getattr(error, 'cleanup_faults', ())
        code = str(error) if isinstance(error, Refusal) else 'preparation_io_or_shape_unknown'
        raise PreparationRefusal(code, cleanup) from None
    except BaseException as primary:
        cleanup = snapshot.close() if snapshot else getattr(primary, 'cleanup_faults', ())
        record_cleanup(primary, cleanup)
        raise
