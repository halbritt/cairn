"""Fixed parent-owned service source/config and callback stdin composition.

Source work only: methods with launch effects require separate runtime selection.
Neither this class nor a successful synthetic test admits the aggregate quote.
"""
import os
from pathlib import Path
import stat
import sys

from trial_bootstrap_snapshot import Snapshot, write_all
from trial_native_bootstrap import _freeze
from trial_sdk_command_service_broker import ServiceLink
from trial_sdk_command_service_runtime import CommandServiceRuntime
from trial_sdk_fixed_config import bindings, require
from trial_sdk_fixed_source_pins import SOURCE_PINS
from trial_sdk_incoming import canonical_chunks, canonical_size_sha256, parse_document
from trial_sdk_session_driver import SDKSessionDriver

EXCLUDED = {(64512,n) for n in (22838820,22946591,22946592,22981266,22981268)}


class FixedServiceLaunch:
    @classmethod
    def prepare(cls, session, link, document, output):
        owner = cls.__new__(cls)
        owner.prepare_owned(session, link, document, output)
        return owner

    def prepare_owned(owner, session, link, document, output):
        """Run owner retains this object BEFORE any source acquisition."""
        require(not hasattr(owner, '_preparation_started'), 'fixed_source_consumed')
        owner._preparation_started = True
        require(type(session) is SDKSessionDriver and session._resources is not None
                and type(link) is ServiceLink and session._resources.resources is link.resources,
                'fixed_original_resources')
        session.source_bindings()
        allocation = session._custody._allocation
        require(allocation is not None, 'fixed_source_allocation')
        allocation.check()  # BEFORE config cloning, snapshot copy or decoding.
        link.resources._lookup(link.resources._memory, link._allocation)
        require(not link.owner.snapshot()['launch_attempted'], 'fixed_service_consumed')
        owner._session, owner.link, owner._allocation = session, link, allocation
        owner._snapshot = None
        owner._started = owner._failed = owner._closed = False
        owner._producers = []
        owner._cleanup_faults = set()
        try:
            data = _freeze(document)
            runtime, config = bindings(data)
            CommandServiceRuntime(runtime)  # Validate inert runtime configuration.
            row = link.resources._lookup(link.resources._memory, link._allocation)
            require(runtime.deadline_ns == link._deadline, 'fixed_service_deadline')
            require(row[2] >= runtime.standing_raw and row[3] >= runtime.standing_working,
                    'fixed_service_allocation')
            owner._runtime, owner._config = runtime, config
            owner._directory = str(Path(output))
            owner._snapshot = Snapshot.__new__(Snapshot)
            Snapshot.__init__(owner._snapshot, output)
            root = Path(__file__).absolute().parent
            for name, digest in SOURCE_PINS.items():
                path = root.parent/'integrations/lifecycle/memory.py' if name == 'memory.py' else root/name
                for ancestor in (path, *path.parents):
                    st = ancestor.lstat()
                    require(not stat.S_ISLNK(st.st_mode) and (st.st_dev,st.st_ino) not in EXCLUDED,
                            'fixed_source_path')
                owner._snapshot.copy(name, {'path':str(path),'sha256':digest},
                                     256*1024 if name == 'memory.py' else 65536,
                                     executable=bool(path.stat().st_mode & 0o111))
            length, digest = canonical_size_sha256(data)
            require(length <= 65536, 'fixed_config_bound')
            destination = owner._snapshot._destination('service.json')
            for block in canonical_chunks(data):
                write_all(destination.fd, block)
            block = None
            owner._snapshot._seal('service.json', destination, digest, length, False)
            owner._initial = owner._snapshot.bindings()
            owner._config_sha = digest
            return owner
        except BaseException as primary:
            owner._failed = True
            try:
                allocation.poison(primary)
            except BaseException:
                owner._cleanup_faults.add('source_poison_unknown')
            # This unlaunched owner's descriptors may close, but no allocation
            # refund is inferred; traceback and caller aliases remain charged.
            if owner._snapshot is not None:
                try:
                    owner._cleanup_faults.update(owner._snapshot.close())
                except BaseException:
                    owner._cleanup_faults.add('source_close_unknown')
            if owner._cleanup_faults:
                from trial_sdk_resource_failure import annotate_resource_failure
                annotate_resource_failure(primary, tuple(sorted(owner._cleanup_faults)))
            raise

    def _check(self):
        try:
            return self._verify()
        except BaseException as primary:
            self._failed = True
            try:
                self._allocation.poison(primary)
            except BaseException:
                self._cleanup_faults.add('source_poison_unknown')
            raise

    def _verify(self):
        require(not self._failed and not self._closed, 'fixed_launch_unavailable')
        self._allocation.check()
        self._session.source_bindings()
        self.link._check()
        self.link.resources._lookup(self.link.resources._memory, self.link._allocation)
        require(self._snapshot.bindings() == self._initial, 'fixed_sources_changed')
        text = self._snapshot.read_text('service.json')
        runtime, config = bindings(parse_document(text, self._initial['service.json']['bytes']))
        require(runtime == self._runtime and config == self._config, 'fixed_config_changed')

    def start_service(self, directory):
        """Sole actual launch path for this policy; never called by offline tests."""
        try:
            self._check()
            require(not self._started and not self.link.owner.snapshot()['launch_attempted'],
                    'fixed_service_consumed')
            require(self._runtime.path == str(Path(directory)/'memory.sock'), 'fixed_service_path')
            self._started = True  # Consume before any launch effect.
            argv = (sys.executable, '-I', '-S', '-B', str(Path(self._directory)/'trial_sdk_fixed_entry.py'),
                    'service', self._config_sha, str(self._runtime.deadline_ns))
            self.link.prestart(directory, argv, self._runtime.cwd, self._runtime.env)
            require(self.link.owner.snapshot()['launched'], 'fixed_service_launch_unknown')
            self.link._fixed_launch = self
        except BaseException as primary:
            self._failed = True
            try:
                self._allocation.poison(primary)
            except BaseException:
                self._cleanup_faults.add('source_poison_unknown')
            raise

    def policy_for(self, rpc):
        self._check()
        require(self._started and rpc.link is self.link
                and rpc.resources is self.link.resources
                and getattr(self.link, '_fixed_launch', None) is self
                and self.link.owner.snapshot()['launched']
                and self.link.owner.snapshot()['wait'] is None
                and self.link.owner.snapshot()['primary'] is None, 'fixed_service_not_held')
        return self._runtime.policy

    def bind_producer(self, producer, rpc, event, rpc_path):
        try:
            return self._bind_producer(producer, rpc, event, rpc_path)
        except BaseException as primary:
            self._failed = True
            # Independent poison attempts; cleanup never replaces the primary.
            for action in (lambda: self._allocation.poison(primary),
                           lambda: producer._poison(primary),
                           lambda: rpc._poison('fixed_bootstrap_failed')):
                try:
                    action()
                except BaseException:
                    self._cleanup_faults.add('bootstrap_poison_unknown')
            raise

    def _bind_producer(self, producer, rpc, event, rpc_path):
        """Populate stdin before original producer launch, under its held ticket."""
        from trial_sdk_producer_driver import SDKProducerDriver
        from trial_sdk_read_custody import ReadCustody
        require(type(producer) is SDKProducerDriver and producer._resources is not None,
                'fixed_producer_binding')
        resources = producer._resources
        resources.resources._lookup(resources.resources._memory, resources.allocation)
        require(resources.allocation is rpc._allocation and resources.owner is rpc.owner
                and resources.resources is self.link.resources
                and rpc._read_custody is None and not self._producers, 'fixed_producer_binding')
        policy = self.policy_for(rpc)
        require(type(rpc_path) is str and rpc_path.startswith('/')
                and rpc._listener is not None and rpc._listener._started
                and rpc._listener._failure is None and rpc._connection is None, 'fixed_rpc_path')
        # The fixed builder must use the actual listener, not an arbitrary path.
        require(rpc_path == rpc._listener._path, 'fixed_rpc_path')
        callback = event.callback
        document = dict(schema='cairn.fixed-producer/1', config=self._config,
            input=callback._input, input_bytes=callback.input_bytes,
            input_sha256=callback.input_sha256, event=callback.event,
            deadline_ns=event.observation['deadline_ns'], rpc_path=rpc_path,
            client_raw=rpc.quote.client.raw, client_working=rpc.quote.client.working)
        self._producers.append(producer)  # Retain cleanup custody before mutation.
        producer._bind_bootstrap(event, document)
        custody = ReadCustody(rpc, policy)
        custody._service_custody = self
        rpc._read_custody = custody

    def start_producer(self, producer):
        try:
            self._check()
            require(any(producer is held for held in self._producers), 'fixed_producer_binding')
            require(getattr(self.link, '_fixed_launch', None) is self
                    and self.link.owner.snapshot()['launched']
                    and self.link.owner.snapshot()['wait'] is None, 'fixed_service_not_held')
            producer._usable()
            producer._now()
            resources = producer._resources
            resources.resources._lookup(resources.resources._memory, resources.allocation)
            require(not resources.owner.snapshot()['launch_attempted'], 'fixed_producer_consumed')
            argv = (sys.executable, '-I', '-S', '-B', str(Path(self._directory)/'trial_sdk_fixed_entry.py'),
                'producer', producer._transport['input_sha256'], str(producer._binding['deadline_ns']),
                self._directory, SOURCE_PINS['memory.py'])
            # Ambient disable flags remain semantically visible. No service
            # credential/route or arbitrary inherited environment is supplied.
            env = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'}
            for key in ('CAIRN_LIFECYCLE_DISABLED','CAIRN_LIFECYCLE_CHILD'):
                if key in os.environ:
                    env[key] = os.environ[key]
            resources.owner.start(argv, self._runtime.cwd, env)
        except BaseException as primary:
            self._failed = True
            for action in (lambda: self._allocation.poison(primary),
                           lambda: producer._resources.poison(primary)):
                try:
                    action()
                except BaseException:
                    self._cleanup_faults.add('launch_poison_unknown')
            raise

    def release_producer_sources(self, rpc):
        """End only this owner's producer backing; output aliases stay caller-owned.

        RPC invokes this at its existing original-allocation retirement boundary,
        after actual child release and protocol close. No new refund is made.
        """
        require(rpc.link is self.link and rpc._closed and not rpc._unknown
                and len(self._producers) == 1, 'fixed_producer_retirement')
        producer = self._producers[0]
        require(producer._resources.allocation is rpc._allocation
                and producer._resources.owner is rpc.owner
                and producer._ready and producer._taken and producer._output is None
                and not producer._busy and producer._failure is None,
                'fixed_producer_retirement')
        state = producer.native_owner.snapshot()
        require(state['wait'] is not None and state['release'] is not None
                and state['release']['released'] is True, 'fixed_producer_retirement')
        producer.cancel()
        producer._callback = producer._bootstrap = None
        producer._slabs.clear()
        self._producers.clear()

    def close(self):
        # Source backing stays live through actual process waits. This does not
        # retire allocations, output graphs or exception tracebacks.
        if self._closed:
            return self._snapshot.cleanup_faults
        owners = [self.link.owner, *(p.native_owner for p in self._producers)]
        require(all(not o.snapshot()['launch_attempted'] or o.snapshot()['wait'] is not None
                    for o in owners), 'fixed_sources_in_use')
        self._closed = True
        return self._snapshot.close()
