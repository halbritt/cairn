"""Original-line selected-input measurement bound to the fixed run's custody.

No timing sidecar or full stream is retained. Source-level ownership here is not
selected-executable compatibility, task grading, or a physical process bound.
"""
import copy
import hashlib

from trial_native_observation import LineMeasurement
from trial_observation_storage import text_size_sha256
from trial_sdk_incoming import DecodedLine, OwnedJSON
from trial_sdk_stream_driver import StreamLineReceipt
from trial_sdk_shared_coordinator import require

# Two simultaneous small base64 input/output buffers require <1024 raw bytes.
# Two nested source-delivery parses each receive 256 working bytes per
# possible input byte (32MiB each), including containers/pairs/scalars, not a
# claim that every Python scalar has one fixed size. Another 32MiB covers
# Unicode traversal/serialization and the fixed projection inventories/copies.
# The original preparation continues to own the incoming graph and decoded line.
# These source reservations are not physical RSS attestation.
RAW = 1024
WORKING = 96 * 1024 * 1024
HOOK_LIMIT = 128


class SelectedInput:
    def __init__(self, run):
        from trial_sdk_fixed_run import FixedRunOwner
        require(type(run) is FixedRunOwner and run._selected_input is self,
                'selected_input_owner')
        self.run = run
        self.state = 'constructing'
        self._report = None
        self._measurement = None
        self._hooks = []
        self._hook_ids = set()
        self._unknown = set()
        self._hash = hashlib.sha256()
        self._bytes = self._lines = self._controls = 0
        self._completed = None
        binding = run._session_binding
        self._allocation = run.resources.allocate(binding.domain, 'standing', RAW, WORKING)
        require(self._allocation is not None, 'selected_input_resource_capacity')
        self.pump = run._session._pump
        self.retention = run._evidence
        self.launch = run._native_launch
        self._origin = self.launch.capture_origin_ns
        custody = run._session._custody
        prompt = custody._snapshot.read_text('prompt.txt')
        argv = run._session.launch.argv
        control = custody.control
        self._measurement = LineMeasurement(
            input_bytes=run._session.launch.user_message_bytes + control._initial_instruction_bytes + 512,
            expects_hooks=True, model=argv[argv.index('--model')+1], prompt=prompt,
            workspace=self.launch.cwd)
        self.state = 'open'

    def check(self):
        require(self.state == 'open' and self.run._selected_input is self
                and self.run._session._pump is self.pump
                and self.pump._selected_input is self
                and self.pump._retention is self.retention
                and self.run._native_launch is self.launch,
                'selected_input_binding')
        row = self.run.resources._lookup(self.run.resources._memory, self._allocation)
        require(row == (self.run._session_binding.domain, 'standing', RAW, WORKING),
                'selected_input_allocation')

    def consume(self, line, document, receipt):
        """Called synchronously by the original pump after strict/control parsing."""
        self.check()
        require(type(line) is DecodedLine and type(document) is OwnedJSON
                and type(receipt) is StreamLineReceipt, 'selected_input_line_binding')
        require(receipt.start_offset == self._bytes
                and receipt.end_offset == self._bytes + line.raw_bytes + 1
                and receipt.raw_sha256 == line.raw_sha256 == document.raw_sha256
                and document.raw_bytes == line.raw_bytes
                and receipt.first_byte_ns == line.first_byte_ns == document.first_byte_ns
                and self._origin <= receipt.first_byte_ns <= receipt.completed_ns
                and (self._completed is None or self._completed <= receipt.completed_ns),
                'selected_input_receipt_mismatch')
        # Original strict UTF-8 text, not a JSON reconstruction. LF is the
        # assembler's consumed delimiter; CR/spacing/escape spelling stay intact.
        size, digest = text_size_sha256(line.text, update=self._hash.update)
        require((size, digest) == (line.raw_bytes, line.raw_sha256),
                'selected_input_original_line_mismatch')
        self._hash.update(b'\n')
        self._bytes = receipt.end_offset
        self._completed = receipt.completed_ns
        self._lines += 1
        if document.kind is not None and document.kind.startswith('control_'):
            # Only SDKControl.receive_owned can consume this pump's control
            # document before this call. Failed/unknown/cancel controls never
            # reach here; the pump poisons this owner on those paths.
            require(document.taken and document.kind in ('control_request', 'control_response'),
                    'selected_input_control_unvalidated')
            self._controls += 1
        else:
            require(not document.taken, 'selected_input_document_consumed')
            self._measurement.consume_document(document._graph, size+1,
                {'received_seconds': (receipt.completed_ns-self._origin)/1e9})

    def hook(self, outer):
        """Observe original producer/RPC owners before their detach frame returns."""
        from trial_sdk_fixed_outer import FixedCallbackDriver
        self.check()
        c = self.run._coordinator
        require(type(outer) is FixedCallbackDriver and self.run._callback is outer
                and outer._coordinator is c and outer._state == 'writing'
                and c._reply_written and c.rpc is outer._rpc and c.rpc._closed
                and not c.rpc._unknown and c.producer is not None, 'selected_input_hook_binding')
        producer = c.producer
        state = producer.snapshot()
        ident = c._event.callback.request_id
        require(ident not in self._hook_ids and state['ready'] and state['output_taken']
                and not state['failure'] and not state['cancelled'] and not state['cleanup']
                and not state['stream_hash_unknown'] and state['stdout_eof'] and state['stderr_eof']
                and state['wait'] is not None and state['release'] is not None
                and state['release']['released'] and state['release']['handles_detached'],
                'selected_input_hook_unobserved')
        if len(self._hooks) >= HOOK_LIMIT:
            self._unknown.add('hook_observation_limit')
            return
        self._hook_ids.add(ident)
        self._hooks.append(dict(event=c._event.callback.event,
            stdout_sha256=state['stdout_sha256'], stdout_bytes=state['stdout_bytes'],
            stderr_bytes=state['stderr_bytes'], exit_code=state['wait']['returncode'],
            process_seconds=(self.run._now()-c._event.observation['first_byte_ns'])/1e9,
            memory_call_attempts=dict(c.rpc._call_attempts)))

    def finish(self):
        self.check()
        run, pump, retained = self.run, self.pump, self.retention
        require(not run._failed and not run._cancelled and run._terminal_metadata is not None,
                'selected_input_run_incomplete')
        require(not pump._cancelled, 'selected_input_stream_cancelled')
        self._verify_final()
        state = run._session.native_owner.snapshot()
        report = self._measurement.finish(self._hooks)
        gate = run._coordinator.gate.snapshot()
        if (gate['failure'] or gate['accounting_unknown'] or gate['completion_unknown']
                or gate['pending'] is not None
                or report['native_searches'] != gate['native_searches_debited']
                or report['hook_searches'] != gate['hook_searches']
                or report['total_actual_pull_calls'] != gate['expansions_debited']):
            self._unknown.add('rendered_memory_dispatch_uncorrelated')
        if state['wait']['returncode'] != 0:
            self._unknown.add('native_exit_unsuccessful')
        report['unknown'] = sorted(set(report['unknown']) | self._unknown)
        if report['unknown']:
            report['status'] = 'unknown'
        self._report = dict(schema='cairn.sdk-selected-input/1', measurement=report,
            stdout_bytes=self._bytes, stdout_sha256=self._hash.hexdigest(),
            complete_lines=self._lines, validated_controls=self._controls,
            capture_origin_ns=self._origin, last_completed_ns=self._completed,
            timing_basis='complete_read_after_private_retention',
            hook_time_basis='callback_first_read_through_reply_write',
            qualification='observed_selected_input_only_not_runtime_admission_or_task_grade')
        self.state = 'finished'

    def _verify_final(self):
        run, pump, retained = self.run, self.pump, self.retention
        require(run._selected_input is self and run._session._pump is pump
                and pump._selected_input is self and pump._retention is retained
                and run._evidence is retained, 'selected_input_final_owner')
        require(run.resources._lookup(run.resources._memory, self._allocation) ==
                (run._session_binding.domain, 'standing', RAW, WORKING),
                'selected_input_allocation')
        state = run._session.native_owner.snapshot()
        require(retained.state == 'closed' and not retained.faults and retained.pending is None
                and pump._failure is None and not pump._cleanup_unknown
                and pump._lines is None and pump._active is None and pump._assembler.pending_bytes == 0
                and state['wait'] is not None and state['stdout_eof'] and state['stderr_eof']
                and state['release'] is not None and state['release']['released']
                and state['release']['handles_detached'] and not state['has_handles'],
                'selected_input_custody_incomplete')
        require(self._bytes == pump._line_offset == pump._bytes['stdout']
                == retained.observed['stdout'] == retained.written['stdout'] == retained.synced['stdout']
                and self._hash.hexdigest() == pump._hash['stdout'].hexdigest()
                == retained.hashes['stdout'].hexdigest(), 'selected_input_stream_mismatch')
        if self._report is not None:
            require(self._report['stdout_bytes'] == self._bytes
                    and self._report['stdout_sha256'] == self._hash.hexdigest(),
                    'selected_input_report_binding')

    def abort(self):
        if getattr(self, 'state', None) != 'finished':
            self.state = 'unknown'
            if getattr(self, '_measurement', None) is not None:
                self._measurement.close()

    def evidence(self):
        """Return only this owner-produced report after original final binding."""
        require(self.state == 'finished' and self._report is not None,
                'selected_input_unqualified')
        require(self.run._selected_input is self and self.run._evidence is self.retention
                and self.retention.state == 'closed' and not self.run._failed,
                'selected_input_evidence_binding')
        self._verify_final()
        return copy.deepcopy(self._report)


def qualification(run):
    """Private export either binds the original owner or records unavailable."""
    owner = run._selected_input
    if owner is None:
        return dict(schema='cairn.sdk-selected-input/1', status='unknown',
                    reason='original_stream_qualification_unavailable')
    require(type(owner) is SelectedInput and owner.run is run, 'selected_input_export_owner')
    if owner.state != 'finished' or run._failed:
        return dict(schema='cairn.sdk-selected-input/1', status='unknown',
                    reason='original_stream_qualification_unavailable')
    return owner.evidence()
