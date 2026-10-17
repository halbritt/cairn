"""Offline source-review preparation; deliberately not a complete bootstrap.

No command, account source, hook gateway, mount operation or admission route is
implemented. The returned owner must stay open while its review bindings are used.
"""
import copy
from contextlib import contextmanager, nullcontext
import hashlib
import json
from pathlib import Path
import re

from trial_bootstrap_snapshot import Snapshot, record_cleanup
from trial_coding_launch import CodingLaunch, SDKLaunch, compile_launch, compile_sdk_launch
from trial_sdk_control import SDKControl, SDKControlError
from trial_sdk_incoming import canonical_size_sha256
from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_resource_failure import freeze_resource, poison_resource, annotate_resource_failure
from trial_paired_request import (IMPLEMENTATION_SETTINGS_SHA,
                                 IMPLEMENTATION_SOURCE_SHA, ORIGINAL_LIMITS,
                                 PIN_LIMIT, Refusal, identifier,
                                 mcp_arguments, need, shape)

SCHEMA = 'cairn.native-bootstrap-review/1'
SDK_SCHEMA = 'cairn.sdk-native-bootstrap-review/1'
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


def canonical_sha(document):
    """Hash closed preparation graphs without a whole canonical byte copy."""
    return canonical_size_sha256(document)[1]


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
        return json.loads(content.decode('utf-8') if isinstance(content, bytes) else content,
                          object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refusal('bootstrap_json')))
    except (UnicodeError, ValueError, RecursionError):
        raise Refusal('bootstrap_json') from None


def _text(snapshot, name, code='bootstrap_text'):
    try:
        return snapshot.read_text(name)
    except UnicodeError:
        raise Refusal(code) from None


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


def _declaration(document, *, schema=SCHEMA):
    shape(document, 'schema binding launch sources mcp_binding limits permission_sha256 hook_interface',
          'bootstrap_shape')
    need(document['schema'] == schema, 'bootstrap_schema')
    shape(document['binding'], 'request_id occurrence_id case_id arm session_id workspace_sha256 request_sha256 source_set_sha256',
          'bootstrap_binding')
    binding = document['binding']
    for name in ('request_id', 'occurrence_id', 'session_id'):
        identifier(binding[name])
    # SDK source preparation accepts weekly IDs as syntax, never membership or
    # launch authority. WeeklyPreparationOwner checks the actual selected row.
    if schema == SDK_SCHEMA:
        need(type(binding['case_id']) is str and
             re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}',binding['case_id']), 'bootstrap_case')
    else:
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
    instruction = _text(snapshot, 'instruction.txt')
    need('\0' not in instruction, 'bootstrap_instruction')
    try:
        launch = compile_launch(session_id=document['binding']['session_id'],
                                model=document['launch']['model'],
                                effort=document['launch']['effort'],
                                prompt=_text(snapshot, 'prompt.txt'),
                                memory_instruction=instruction)
    except ValueError as error:
        # compile_launch and the text reader expose fixed codes only.
        raise Refusal(str(error)) from None
    need(type(launch) is CodingLaunch, 'bootstrap_launch_profile')
    _configuration(snapshot, document, launch, mcp_args)
    snapshot.generated('input.jsonl', launch.input_message)
    return launch


def _configuration(snapshot, document, launch, mcp_args):
    """Common source agreement only; neither profile obtains admission here."""
    settings = _json(_text(snapshot, 'settings.json', 'bootstrap_json'))
    shape(settings, 'permissions hooks autoMemoryEnabled claudeMdExcludes', 'bootstrap_settings')
    need(settings['permissions'] == _json(launch.permission_json)
         and canonical_sha(settings['permissions']) == document['permission_sha256'],
         'permission_settings_mismatch')
    need(settings['hooks'] == {} and settings['autoMemoryEnabled'] is False
         and settings['claudeMdExcludes'] == ['**'], 'bootstrap_settings')
    mcp = _json(_text(snapshot, 'mcp.json', 'bootstrap_json'))
    expected = dict(mcpServers=dict(cairn=dict(type='stdio', command='/native/cairn',
                                               args=mcp_args)))
    need(mcp == expected, 'bootstrap_mcp')


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
            need(_text(self._snapshot, 'input.jsonl') == self._launch.input_message.decode('utf-8')
                 and hashlib.sha256(self._launch.permission_json).hexdigest()
                 == IMPLEMENTATION_SETTINGS_SHA
                 and self._launch.hook_context_bytes == self._hook_context_bytes
                 and canonical_sha(list(self._launch.argv)) == self._launch_sha
                 and self._launch.argv[-1]
                 == _text(self._snapshot, 'instruction.txt'), 'compiled_binding_changed')
        except (OSError, Refusal, UnicodeError):
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


def _sdk_state(launch, control, session_id, instruction_bytes):
    need(type(launch) is SDKLaunch and type(control) is SDKControl
         and launch.control is control, 'compiled_binding_changed')
    state = control.pristine_binding()
    need(state['bindings']['session_id'] == session_id
         and state['user_message_bytes'] == launch.user_message_bytes
         and state['user_message_sha256'] == launch.user_message_sha256
         and state['instruction_bytes'] == instruction_bytes
         and state['guaranteed_context_bytes'] == launch.guaranteed_context_bytes,
         'compiled_binding_changed')
    return state


def _close_control(control):
    try:
        control.close()
    except BaseException:
        return ('sdk_control_cleanup_unknown',)
    return ()


def _close_sdk_sources(owner):
    if not owner._control_close_attempted:
        owner._control_close_attempted = True
        owner._control_cleanup = _close_control(owner._control)
    # A control cleanup fault cannot skip the independently owned FDs.
    return tuple(sorted(set(owner._control_cleanup + owner._snapshot.close())))


class SDKPreparationAllocation:
    """Held accounting identity, not proof that the caller's quote is sufficient.

    Only the original attempt owner may release this allocation after ALL aliases
    end. File close and in-process custody transfer deliberately do not refund it.
    """
    def __init__(self, resources, ticket):
        self.resources, self.ticket = resources, ticket
        self._row = resources._lookup(resources._memory, ticket)
        self._begun = self._unknown = False
        self._verification_active = False
        self._resource_cleanup_faults = ()

    def begin(self):
        self.check()
        need(not self._begun, 'sdk_preparation_allocation_consumed')
        self._begun = True

    def check(self):
        need(not self._unknown, 'sdk_preparation_allocation_unknown')
        self.resources._check()
        need(self.resources._lookup(self.resources._memory, self.ticket) == self._row,
             'sdk_preparation_allocation_changed')

    @contextmanager
    def verification(self):
        """Exclusive scratch use under the unchanged original standing ticket.

        This does not size the quote, release it, or grant a metadata borrow a
        shorter lifetime. Unknown outcomes freeze all original charges.
        """
        try:
            self.check()
            need(not self._verification_active, 'sdk_verification_busy')
            self._verification_active = True
            yield
            self.check()
            self._verification_active = False
        except BaseException as primary:
            self.poison(primary)
            raise

    def poison(self, primary=None):
        self._unknown = True
        self._resource_cleanup_faults = tuple(sorted(set(
            self._resource_cleanup_faults + freeze_resource(self.resources))))
        return self._resource_cleanup_faults


class SDKReviewPreparation(ReviewPreparation):
    """Own pristine source review; an in-process move grants no runtime authority."""
    def __init__(self, snapshot, launch, declaration, *, _allocation=None):
        self._allocation = _allocation
        need(type(launch) is SDKLaunch and type(launch.control) is SDKControl,
             'bootstrap_launch_profile')
        self._snapshot, self._launch = snapshot, launch
        # Cleanup ownership never follows a subsequently replaced launch field.
        self._control = launch.control
        self._control_close_attempted = False
        self._control_cleanup = ()
        self._transferred = False
        self._move_attempted = self._move_failed = False
        self._move_recovery = None
        self._binding = dict(declaration['binding'])
        self._declaration_sha = canonical_sha(declaration)
        self._launch_sha = canonical_sha(list(launch.argv))
        self._user_binding = launch.user_message_bytes, launch.user_message_sha256
        self._hook_context_bytes = launch.guaranteed_context_bytes
        instruction = _text(snapshot, 'instruction.txt')
        state = _sdk_state(launch, self._control, self._binding['session_id'], len(instruction.encode('utf-8')))
        self._control_sha = canonical_sha(state)
        snapshot.generated('sdk-control.json',
            (json.dumps(state, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8'))

    @property
    def launch(self):
        if self._transferred or self._move_failed:
            raise PreparationRefusal('sdk_review_transferred')
        return self._launch

    def review_bindings(self):
        if self._transferred or self._move_failed:
            raise PreparationRefusal('sdk_review_transferred')
        try:
            if self._allocation is not None:
                self._allocation.check()
            sources = self._snapshot.bindings()
            instruction = _text(self._snapshot, 'instruction.txt')
            state = _sdk_state(self._launch, self._control, self._binding['session_id'], len(instruction.encode('utf-8')))
            need(canonical_sha(state) == self._control_sha
                 and _json(_text(self._snapshot, 'sdk-control.json', 'bootstrap_json')) == state
                 and (self.launch.user_message_bytes, self.launch.user_message_sha256) == self._user_binding
                 and self.launch.guaranteed_context_bytes == self._hook_context_bytes
                 and hashlib.sha256(self.launch.permission_json).hexdigest() == IMPLEMENTATION_SETTINGS_SHA
                 and canonical_sha(list(self.launch.argv)) == self._launch_sha
                 and self.launch.argv[-1] == instruction,
                 'compiled_binding_changed')
        except (OSError, Refusal, SDKControlError) as primary:
            faults = poison_resource(self._allocation, primary)
            cleanup = self.close()
            raise PreparationRefusal('review_custody_unknown', tuple(sorted(set(cleanup + faults)))) from None
        except BaseException as primary:
            faults = poison_resource(self._allocation, primary)
            record_cleanup(primary, self.close())
            annotate_resource_failure(primary, faults)
            raise
        return dict(schema=SDK_SCHEMA, status='source_prepared_for_review',
                    complete_bootstrap=False, runtime_attested=False,
                    missing_gates=list(GATES), input_mode='controller_owned',
                    binding=dict(self._binding), declaration_sha256=self._declaration_sha,
                    snapshots=sources, launch_sha256=self._launch_sha,
                    user_message_bytes=self.launch.user_message_bytes,
                    user_message_sha256=self.launch.user_message_sha256,
                    instruction_sha256=sources['instruction.txt']['sha256'],
                    permissions_sha256=self.launch.permission_sha256,
                    guaranteed_context_bytes=self.launch.guaranteed_context_bytes,
                    control=state, limits=dict(ORIGINAL_LIMITS))

    def consume_for_session(self):
        """Move the original objects once, without launch or runtime admission."""
        return SDKSessionCustody(self)

    def close(self):
        if self._move_failed:
            return self._move_recovery.close()
        return () if self._transferred else _close_sdk_sources(self)

    def __exit__(self, exc_type, primary, traceback):
        cleanup = self.close()
        if cleanup:
            if primary is not None:
                record_cleanup(primary, cleanup)
                return False
            raise PreparationRefusal('sdk_review_cleanup_unknown', cleanup)


class _SDKMoveRecovery:
    """Original cleanup/poison custody, reachable even after a partial move."""
    def __init__(self, preparation):
        self._snapshot = preparation._snapshot
        self._control = preparation._control
        self._allocation = preparation._allocation
        self._control_close_attempted = False
        self._control_cleanup = ()

    def close(self):
        return _close_sdk_sources(self)


class SDKSessionCustody:
    """Original source/controller ownership only; no process or runtime permit.

    Serialized calls and same-process custody are required. Initial control
    metadata remains historical once the borrowed controller makes progress.
    """
    @classmethod
    def empty(cls):
        """Inert shell, published by the receiving session before the move."""
        owner = cls.__new__(cls)
        owner._snapshot = owner._control = owner._allocation = None
        owner._move_recovery = None
        owner._control_close_attempted = False
        owner._control_cleanup = ()
        return owner

    def __init__(self, preparation):
        if type(preparation) is not SDKReviewPreparation:
            raise PreparationRefusal('sdk_review_required')
        if preparation._transferred or preparation._move_attempted:
            raise PreparationRefusal('sdk_review_transferred')
        # Capture original authority BEFORE any publication or old-field clear.
        recovery = _SDKMoveRecovery(preparation)
        self._move_recovery = None
        try:
            preparation._move_recovery = recovery  # Publish initial recovery.
            preparation._move_attempted = True
            self._initial = preparation.review_bindings()
            self._snapshot = recovery._snapshot
            self._allocation = recovery._allocation
            self._launch = preparation._launch
            self._control = recovery._control
            self._control_close_attempted, self._control_cleanup = False, ()
            self._source_failed = False
            preparation._transferred = True
            preparation._snapshot = None
            preparation._launch = None
            preparation._control = None
            preparation._allocation = None
            preparation._move_recovery = None
        except BaseException as primary:
            self._move_recovery = recovery
            preparation._move_attempted = preparation._move_failed = True
            preparation._move_recovery = recovery
            resource_faults = poison_resource(recovery._allocation, primary)
            if preparation._control_close_attempted:
                recovery._control_close_attempted = True
                recovery._control_cleanup = preparation._control_cleanup
            faults = recovery.close()
            record_cleanup(primary, faults)
            annotate_resource_failure(primary, resource_faults)
            raise

    @property
    def launch(self):
        return self._launch

    @property
    def control(self):
        return self._control

    def _verify_held_sources(self):
        initial, launch = self._initial, self._launch
        self._snapshot.verify_bindings(initial['snapshots'])
        instruction = _text(self._snapshot, 'instruction.txt')
        need(type(launch) is SDKLaunch and launch.control is self._control
             and self._control.bindings == initial['control']['bindings']
             and _json(_text(self._snapshot, 'sdk-control.json', 'bootstrap_json')) == initial['control']
             and launch.user_message_bytes == initial['user_message_bytes']
             and launch.user_message_sha256 == initial['user_message_sha256']
             and launch.guaranteed_context_bytes == initial['guaranteed_context_bytes']
             and hashlib.sha256(launch.permission_json).hexdigest() == initial['permissions_sha256']
             and canonical_sha(list(launch.argv)) == initial['launch_sha256']
             and launch.argv[-1] == instruction,
             'compiled_binding_changed')

    def _held_source_check(self, export):
        try:
            need(not self._source_failed, 'session_custody_unknown')
            scope = (self._allocation.verification() if self._allocation is not None
                     else nullcontext())
            with scope:
                self._verify_held_sources()
                if export:
                    # Explicit export retains its original charge. The receiver
                    # must own all borrowed graph lifetimes; no refund here.
                    result = copy.deepcopy(self._initial)
                    result['status'] = 'source_custodied_for_session'
                    result['initial_control'] = result.pop('control')
                    return result
        except (OSError, Refusal, SDKControlError) as primary:
            self._source_failed = True
            faults = poison_resource(self._allocation, primary)
            raise PreparationRefusal('session_custody_unknown', faults) from None
        except BaseException as primary:
            self._source_failed = True
            faults = poison_resource(self._allocation, primary)
            annotate_resource_failure(primary, faults)
            raise


    def verify_held_sources(self):
        """Void internal check; same mutations refuse, no metadata graph escapes."""
        self._held_source_check(False)

    def held_source_bindings(self):
        """Explicit charged metadata export after the same live verification."""
        return self._held_source_check(True)

    def source_bindings(self):
        """Legacy offline recheck: failure closes its original owned sources."""
        try:
            return self.held_source_bindings()
        except (OSError, Refusal, SDKControlError) as primary:
            annotate_resource_failure(primary, self.close())
            raise PreparationRefusal('session_custody_unknown',
                                     getattr(primary, 'cleanup_faults', ())) from None
        except BaseException as primary:
            annotate_resource_failure(primary, self.close())
            raise

    def require_complete(self):
        self.source_bindings()
        raise PreparationRefusal('hook_interface_unavailable')

    def close(self):
        if self._move_recovery is not None:
            return self._move_recovery.close()
        if self._snapshot is None:
            return ()
        if self._control is None:
            return self._snapshot.close()
        return _close_sdk_sources(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, primary, traceback):
        cleanup = self.close()
        if cleanup:
            if primary is not None:
                record_cleanup(primary, cleanup)
                return False
            raise PreparationRefusal('sdk_session_cleanup_unknown', cleanup)


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


def prepare_sdk_review(document, output, *, _allocation=None):
    """Separate closed SDK source review; no user-file writer or runtime permit.

    Compilation creates one controller. Keep it pristine and owned while review
    bindings are used; a future executable handoff needs its own contract. This
    helper never consumes its write queue and require_complete remains refused.
    """
    if _allocation is not None:
        need(type(_allocation) is SDKPreparationAllocation, 'sdk_preparation_allocation')
        _allocation.begin()
    snapshot = owner = control = None
    def cleanup(primary=None):
        if owner is not None:
            faults = owner.close()
        else:
            faults = _close_control(control) if control is not None else ()
            faults += snapshot.close() if snapshot is not None else ()
        # Diagnostics from a failed constructor are optional exception metadata.
        # Inspect them only after owned cleanup, without running arbitrary tuple
        # conversions or allowing a custom getter to replace the primary.
        try:
            inherited = getattr(primary, 'cleanup_faults', ())
            valid = (type(inherited) is tuple and len(inherited) <= 3
                     and all(type(code) is str and code in {
                         'descriptor_cleanup_unknown', 'sdk_control_cleanup_unknown',
                         'cleanup_annotation_unknown'} for code in inherited))
        except BaseException:
            valid = False
        if not valid:
            inherited = ('cleanup_annotation_unknown',)
        return tuple(sorted(set(inherited + faults)))
    try:
        snapshot = Snapshot(output)
        document = _freeze(document)
        mcp_args = _declaration(document, schema=SDK_SCHEMA)
        for role, (name, limit, executable) in SOURCE_ROLES.items():
            snapshot.copy(name, document['sources'][role], limit, executable)
        instruction = _text(snapshot, 'instruction.txt')
        try:
            launch = compile_sdk_launch(session_id=document['binding']['session_id'],
                model=document['launch']['model'], effort=document['launch']['effort'],
                prompt=_text(snapshot, 'prompt.txt'), memory_instruction=instruction)
        except ValueError as error:
            raise Refusal(str(error)) from None
        need(type(launch) is SDKLaunch and type(launch.control) is SDKControl,
             'bootstrap_launch_profile')
        control = launch.control
        _configuration(snapshot, document, launch, mcp_args)
        owner = SDKReviewPreparation(snapshot, launch, document, _allocation=_allocation)
        owner.review_bindings()
        return owner
    except FileExistsError:
        raise PreparationRefusal('preparation_consumed', cleanup()) from None
    except (OSError, Refusal, UnicodeError, TypeError, ValueError, RecursionError) as error:
        faults = cleanup(error)
        code = str(error) if isinstance(error, Refusal) else 'preparation_io_or_shape_unknown'
        raise PreparationRefusal(code, faults) from None
    except BaseException as primary:
        record_cleanup(primary, cleanup(primary))
        raise


def prepare_sdk_review_with_resources(document, output, resources, *, raw, working):
    """Reserve original local standing storage BEFORE preparation effects.

    This fixed profile uses the local standing slot once; occupied slots refuse.
    The caller must fund upstream document creation separately and supply a full
    reviewed simultaneous quote. This function binds accounting, not quote
    sufficiency, native origin, funded provider usage or runtime admission.
    """
    need(type(resources) is AttemptResources, 'sdk_preparation_resources')
    ticket = resources.allocate(resources.local, 'standing', raw, working)
    if ticket is None:
        raise PreparationRefusal('sdk_preparation_resources_exhausted')
    allocation = None
    try:
        allocation = SDKPreparationAllocation(resources, ticket)
        return prepare_sdk_review(document, output, _allocation=allocation)
    except BaseException as primary:
        # Failure frames may retain prepared payloads. Never refund by guessing
        # that descriptor/control cleanup destroyed every alias.
        faults = (freeze_resource(resources) if allocation is None
                  else poison_resource(allocation, primary))
        annotate_resource_failure(primary, faults)
        raise
