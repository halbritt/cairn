"""Original callback ticket transitions at actual serialized owner boundaries.

Only canonical scratch reservations end here. All working bytes remain held;
retired command working quotes accumulate for potentially borrowed results.
Named raw floors below reject undercounts, never establish a complete quote.
"""
from trial_sdk_incoming import MAX_CHUNK_BYTES
from trial_sdk_producer_driver import SDKProducerDriver
from trial_sdk_engine_memory_rpc import ProducerRPC
from trial_sdk_engine_memory_rpc_client import ProducerClient
from trial_sdk_engine_memory_rpc_wire import require


PRODUCER_CANONICAL = 3 * MAX_CHUNK_BYTES
REQUEST_CANONICAL = 4 * MAX_CHUNK_BYTES
# RPC/client roots each include body512 + tx544 + rx544 + auxiliary480.
# Parent UTF-8 output chunk512; producer stdout/stderr slabs1024; decoder
# transient carry head/copy/old carry <=12, plus the other client carry <=3.
# These are named healthy-path backings, NOT interpreter/exception/kernel fit.
SURVIVING_RAW = ProducerRPC.RAW_STORAGE + ProducerClient.RAW_STORAGE + 512 + 1024 + 12 + 15
MINIMUM_RAW = SURVIVING_RAW + PRODUCER_CANONICAL + REQUEST_CANONICAL


class CallbackPhases:
    def __init__(self, rpc, producer):
        require(type(rpc) is ProducerRPC and type(producer) is SDKProducerDriver,
                'rpc_phase_binding')
        binding = producer._resources
        require(binding is not None and binding.resources is rpc.resources
                and binding.owner is rpc.owner and binding.domain is rpc.domain
                and binding.allocation is rpc._allocation and binding.stream is rpc._stream
                and producer._binding['deadline_ns'] == rpc._callback_deadline
                and not producer._busy and not rpc._busy and rpc._phase == 'idle'
                and rpc._operation is None and not rpc.owner.snapshot()['launch_attempted'],
                'rpc_phase_binding')
        row = rpc.resources._lookup(rpc.resources._memory, rpc._allocation)
        require(row[2] >= MINIMUM_RAW,
                'rpc_phase_quote')
        self.rpc, self.producer = rpc, producer
        self.initial_raw, self.initial_working = row[2:]
        self.state = rpc.resources.bind_callback_memory(rpc._allocation)

    def _settled(self):
        rpc = self.rpc
        require(rpc._io is None and rpc._tx is None and rpc._rx is None
                and rpc._future is None and not rpc._auxiliary,
                'rpc_phase_io_pending')
        stream = rpc._stream.snapshot()
        require(stream['pending'] == 0 and stream['future_reserved'] == 0,
                'rpc_phase_io_pending')

    def _transition(self, raw, working, phase):
        state = self.rpc.resources.transition_callback_memory(self.state, raw, working, phase)
        require(state is not None, 'rpc_phase_capacity_denied')
        self.state = state

    def enter_command(self):
        rpc, producer = self.rpc, self.producer
        require(not rpc._busy and not producer._busy and rpc._phase == 'handoff'
                and rpc._decoder is None and rpc._operation is None
                and self.state.phase in ('input_request', 'request'), 'rpc_phase_boundary')
        self._settled()  # Actual receive/consumer has returned, not just set a flag.
        if not producer._input_exhausted:
            return False
        native = producer.native_owner.snapshot()
        require(producer._input is None and producer._pending is None
                and producer._failure is None and not producer._cancelled
                and not producer._generated_hash_unknown and not producer._write_hash_unknown
                and producer._written == producer._binding['input_bytes']
                and producer._generated == producer._written
                and producer._written_hash.hexdigest() == producer._binding['input_sha256']
                and producer._generated_hash.hexdigest() == producer._binding['input_sha256']
                and native['stdin_closed'] and native['native_input'] is not None
                and native['native_input']['closed_known']
                and not native['native_input']['write_unknown']
                and native['native_input']['known_written'] == producer._written
                and producer._resources._pending is None and not producer._resources._busy,
                'rpc_phase_input_unknown')
        self._transition(self.initial_raw - PRODUCER_CANONICAL - REQUEST_CANONICAL,
                         self.state.row[3], 'command')
        return True

    def retain_command_working(self):
        """Charge borrowed text/results; do not infer their lifetime has ended."""
        self._settled()
        operation = self.rpc._operation
        require(operation is not None and operation._closed and not operation._unknown
                and not operation._retired and self.state.phase == 'command',
                'rpc_phase_command_pending')
        row = self.rpc.resources._lookup(self.rpc.resources._memory, operation._parent)
        require(row[:2] == (self.rpc.resources.local, 'operation'), 'rpc_phase_command_binding')
        amount = row[3]
        self._transition(self.state.row[2], self.state.row[3] + amount, 'return')

    def before_status(self):
        require(self.rpc._phase == 'phase_return', 'rpc_phase_boundary')
        self.retain_command_working()
        self.rpc._retire_operation()
        self._transition(self.initial_raw - PRODUCER_CANONICAL, self.state.row[3], 'request')

    def snapshot(self):
        # Read the original ledger publication even if the return-to-owner
        # assignment was interrupted. The ledger retains old/proposed evidence.
        state = self.rpc.resources._callback_memory.get(self.state.ticket, self.state)
        return dict(phase=state.phase, generation=state.generation,
                    raw=state.row[2], working=state.row[3],
                    retained_command_working=state.row[3] - self.initial_working,
                    qualification='named_scratch_transition_not_complete_quote')
