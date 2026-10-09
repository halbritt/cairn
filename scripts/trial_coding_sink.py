"""Fixed coding composition worker; declarations never grant runtime admission.

This worker owns transient parsing and exclusive selected persistence. Its
external controller owns the I/O deadline, actual waits, released containment,
independent file inspection and cell policy. No provider launch API exists.
"""
import json
import struct

from selected_native.retention import Intent, Retention, RetentionError, ExitSnapshot
from trial_attempt_retention import AttemptRetention, ConsumedHooks
from trial_coding_stream import CodingStream, TOTAL
from trial_coding_protocol import CodingDecoder, T_LIMIT, H_LIMIT
from trial_worker_protocol import encoded


class SinkError(ValueError):
    pass


class CodingSink:
    def __init__(self, root_fd, intent, composer, intent_sha256):
        if (type(intent) is not Intent or type(composer) is not CodingStream
                or composer.closed or composer.total or composer.reader.expected['session_id'] != intent.run_id):
            raise SinkError('coding_sink_factory')
        self.composer, self.intent = composer, intent
        # Same fixed one-user-message encoding as compile_launch. The owner
        # additionally binds the separately charged instruction/configuration.
        self.input_bytes = len((json.dumps(dict(type='user', message=dict(
            role='user', content=composer.prompt)), ensure_ascii=False)+'\n').encode())
        self.retention, self.writer = None, None
        self.decoder = CodingDecoder(intent.run_id, intent.source_manifest_sha256)
        self.hook_snapshot = None
        self.state, self.close_unknown = 'opening', False
        try:
            self.retention = Retention.create(root_fd, intent)
            if self.retention.intent_sha256 != intent_sha256:
                raise SinkError('coding_sink_intent_binding')
            self.writer = AttemptRetention(self.retention)
            self.state = 'open'
        except BaseException:
            try:
                self.close()
            except BaseException:
                self.close_unknown = True  # Keep the original acquisition fault.
            raise

    def ready(self):
        if self.state != 'open':
            raise SinkError('coding_sink_unavailable')
        return dict(kind='READY', run_id=self.intent.run_id,
                    intent_sha256=self.retention.intent_sha256)

    def feed(self, tag, payload):
        if self.state != 'open':
            raise SinkError('coding_sink_unavailable')
        try:
            if tag == b'D' and self.composer.total + len(payload) > TOTAL:
                raise SinkError('coding_sink_stream_bounds')
            result = self.decoder.feed(tag, payload)
            if result[0] == 'data':
                self.composer.feed(result[1], result[2])
            elif result[0] == 'hooks':
                self.hook_snapshot = ConsumedHooks.consume(payload, run_id=self.intent.run_id,
                    source_manifest_sha256=self.intent.source_manifest_sha256)
        except BaseException:
            self.state = 'failed'
            raise

    def finish(self):
        """Called only after the fixed pipe reader observes EOF after exact E.

        The returned CLOSED declaration is available only after both owned
        writers close. Parent observations remain necessary to accept it.
        """
        if self.state != 'open' or self.decoder.end is None:
            self.state = 'failed'
            raise SinkError('coding_sink_end_missing')
        self.state = 'failed'  # Every preparation is consumed once.
        self.writer.check_coding_hooks(self.decoder, self.hook_snapshot)
        end = self.decoder.end
        actor, capture = end['actor_wait'], end['capture_wait']
        if end['input_bytes'] != self.input_bytes:
            self.composer.unknown.add('coding_input_binding_unknown')
        known_zero = (actor is not None and capture is not None
            and all(value is not None for value in actor.values())
            and all(value is not None for value in capture.values())
            and actor['returncode'] == capture['returncode'] == 0
            and end['capture_released'] is True
            and end['input_bytes'] == self.input_bytes and end['input_bytes_written'] == self.input_bytes
            and not end['observations'] and not end['capture_cleanup_observations']
            and not end['coding_observations'])
        prepared = None
        if (known_zero and self.composer.frame_failure is None and not self.composer.stopped
                and not self.composer.unknown and not self.composer.pending):
            try:
                prepared = self.retention.prepare(self.composer.reader, ExitSnapshot(
                    actor['pid'], actor['start_ticks'], actor['boot_id'], actor['returncode'], False))
            except RetentionError:
                # Original retention remains consumed and preserves its selected
                # diagnostic/partial files. This is a separate failure channel.
                self.composer.unknown.add('success_retention_refused')
        receipt = self.writer.prepare_coding(self.composer, self.decoder, prepared,
                                            hook_snapshot=self.hook_snapshot)
        outcome = 'success_retention' if prepared is not None else 'failure_evidence'
        declaration = dict(kind='CLOSED', run_id=self.intent.run_id,
            intent_sha256=self.retention.intent_sha256, outcome=outcome,
            manifest_sha256=receipt.selected_sha256, selected_bytes=receipt.selected_bytes)
        self.close()
        if self.close_unknown:
            raise SinkError('coding_sink_close_unknown')
        self.state = 'closed'
        return declaration

    def abort(self):
        """Preserve selected prior channels when closure is unavailable.

        A diagnostic is not a CLOSED declaration. Never retry a consumed
        persistence operation or replace its original exception.
        """
        interruption = None
        try:
            if self.writer is not None and self.writer.state == 'open' and not self.composer.closed:
                self.composer.unknown.add('coding_transport_unknown')
                self.writer.prepare(self.composer, None)
        except RetentionError:
            pass  # The caller still reports failure, never successful closure.
        except BaseException as exc:
            interruption = exc
        self.state = 'failed'
        try:
            self.close()
        except BaseException as exc:
            interruption = interruption or exc
        if interruption is not None:
            raise interruption

    def close(self):
        self.state = 'failed'
        interruption = None
        for writer in (self.writer, self.retention):
            if writer is not None:
                try:
                    writer.close()
                except (RetentionError, OSError):
                    self.close_unknown = True
                except BaseException as exc:
                    self.close_unknown = True
                    interruption = interruption or exc
        if not self.composer.closed:
            # This only erases transient input; it never finishes the reader or
            # manufactures an actor return. Keep original interrupt visible.
            try:
                self.composer.observations(None)
            except BaseException as exc:
                interruption = interruption or exc
        if interruption is not None:
            raise interruption


def exact(source, size):
    data = bytearray()
    while len(data) < size:
        block = source.read(size-len(data))
        if not block:
            raise SinkError('coding_sink_packet_truncated')
        data.extend(block)
    return bytes(data)


def serve(sink, source, target):
    """Fixed packet pipe, bounded reads; external owner bounds blocking I/O."""
    def send(value):
        target.write(encoded(value)+b'\n')
        target.flush()
    try:
        send(sink.ready())
        while True:
            tag = source.read(1)
            if not tag:
                break
            size = struct.unpack('!I', exact(source, 4))[0]
            if tag not in (b'T', b'D', b'H', b'E') or size > (T_LIMIT if tag == b'T' else H_LIMIT):
                raise SinkError('coding_sink_packet_bounds')
            sink.feed(tag, exact(source, size))
        declaration = sink.finish()
        send(declaration)
        return declaration
    except BaseException:
        try:
            sink.abort()
        except BaseException:
            sink.close_unknown = True  # Never replace the original pipe/interrupt fault.
        raise
