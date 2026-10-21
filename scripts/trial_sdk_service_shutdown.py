"""Original service protocol/stdio closure; no new deadline or cleanup grant.

The fixed service has no console-output protocol. A positive diagnostic read
is charged and retained as failure evidence, never silently discarded as EOF.
The existing receipt-backed I/O port owns interrupted-prefix reconciliation.
"""
import hashlib

from trial_sdk_command_service_broker import ServiceLink
from trial_sdk_command_service_runtime import RAW_STORAGE as CHILD_RAW
from trial_sdk_producer_resources import ProducerResources
from trial_sdk_stream_credit import StreamCredit
import trial_sdk_command_service_wire as wire


def require(value, code):
    if not value:raise ValueError(code)


class ServiceOutput(ProducerResources):
    # Original 5184-byte fixed-service quote: parent and child record roots,
    # auxiliary scratch, plus one parent512-byte diagnostic slab.
    MINIMUM_RAW = ServiceLink.RAW_STORAGE + CHILD_RAW + 512
    ALLOCATION_LIFETIME = 'standing'

    def _stream_domain(self, resources, domain):
        # Parent diagnostic reads use the original local DATA slot, as in the
        # service host composition. The service DATA slot belongs to commands.
        return resources.local

    def __init__(self, link):
        require(type(link) is ServiceLink, 'service_output_binding')
        self.link = link
        self._slab = None
        self._reading = None
        self._cursor = 0
        self._done = False
        self.counts = dict(stdout=0, stderr=0)
        self.hashes = {name:hashlib.sha256() for name in self.counts}
        # Publish before construction effects, including uncertain bank handoff.
        self.stream = StreamCredit.__new__(StreamCredit)
        StreamCredit.__init__(self.stream, link.resources, link.resources.local, 1)
        super().__init__(link.resources, link.owner, link.domain,
                         link._allocation, self.stream)
        self._slab = bytearray(512)
        self.claim()

    def step(self):
        require(not self._done and self.owner is self.link.owner,
                'service_output_consumed')
        try:
            state=self.owner.snapshot()
            require(state['launched'] and state['release'] is None,
                    'service_output_owner')
            for _ in range(2):
                name=('stdout','stderr')[self._cursor]
                self._cursor=1-self._cursor
                if state[name+'_eof']:continue
                self._reading=(self.owner.io_receipt(),name)
                with memoryview(self._slab) as view:
                    count=self.read_into(name,view)
                    if count>0:
                        self.counts[name]+=count
                        with view[:count] as prefix:self.hashes[name].update(prefix)
                        # Keep this original slab/prefix. No next read can erase
                        # it once this port and its original bank are poisoned.
                        raise ValueError('service_unexpected_output')
                self._reading=None
                return
        except BaseException as primary:
            if not self._unknown:self.poison(primary)
            raise

    def finish(self):
        require(not self._done and not self._unknown and self._reading is None,
                'service_output_unavailable')
        state=self.owner.snapshot()
        require(state['stdout_eof'] and state['stderr_eof']
                and state['wait'] is not None and state['wait']['returncode']==0
                and state['release'] is not None and state['release']['released']
                and state['release']['handles_detached'], 'service_output_incomplete')
        self.stream.finish()
        self._done=True


class ServiceShutdown:
    """Once-only healthy terminal shutdown under the run's original deadline."""
    def __init__(self, run):
        from trial_sdk_fixed_run import FixedRunOwner
        require(type(run) is FixedRunOwner and run._service_shutdown is self
                and run._state=='terminal_waited' and not run._failed
                and not run._cancelled and run._callback is None
                and (run._memory is None or run._memory.operation is None),
                'service_shutdown_owner')
        self.run, self.link, self.output = run, run._link, run._service_output
        self.credit=None
        self.state='starting'
        require(type(self.output) is ServiceOutput and self.output.link is self.link
                and getattr(self.link,'_fixed_launch',None) is run._service
                and self.link._tx is None and self.link._rx is None
                and self.link.transcript.snapshot()['phase']=='idle'
                and run._ready_credit is None, 'service_shutdown_binding')
        self.credit=self.link.credit(64)
        self.link.arm_receive(self.credit,32)
        self.link.queue(wire.SHUTDOWN,0,self.credit)
        self.state='protocol'

    def step(self):
        require(self.run._service_shutdown is self and not self.run._failed,
                'service_shutdown_unavailable')
        if self.state=='done':return True
        require(self.run._now()<self.run._deadline, 'service_shutdown_deadline')
        self.output.step()
        if self.state=='protocol':
            if self.link._tx is not None:
                self.link.send_step()
            elif self.link.transcript.snapshot()['phase']!='closed':
                self.link.receive_step(lambda frame:None)
            else:
                self.credit.finish()
                self.link.close_channels()
                self.state='draining'
            return False
        require(self.state=='draining', 'service_shutdown_state')
        self.link.owner.observe_wait()
        state=self.link.owner.snapshot()
        if state['wait'] is None or not state['stdout_eof'] or not state['stderr_eof']:
            return False
        require(state['wait']['returncode']==0, 'service_shutdown_exit')
        release=self.link.owner.release()
        require(release['released'] and release['handles_detached'], 'service_shutdown_release')
        self.output.finish()
        self.state='done'
        return True
