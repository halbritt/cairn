"""SDK-side mcp_message envelope and streaming replies, without dispatch.

Envelope evidence: retained sdk.mjs d768bb75542ea853a66c570bd82b010a7b843f4949ab3ebb20e9d1f2a27af881,
1100-byte ranges at 950450 and 966802. This does not attest a native executable.
"""
from dataclasses import dataclass
import itertools
import re

from trial_sdk_incoming import canonical_fragments, canonical_size_sha256


@dataclass(frozen=True)
class MemoryRequest:
    request_id: str
    message: dict


class MemoryWrite:
    kind = 'memory'

    def __init__(self, request_id, document):
        self.request_id = request_id
        self.size, self.digest = canonical_size_sha256(document)
        self.size += 1
        self.offset = 0
        self.wire = b''
        self._chunks = itertools.chain(canonical_fragments(document), (b'\n',))
        self._written = 0

    def pending(self, maximum):
        if self.offset == len(self.wire):
            self.wire = b''  # End prior backing BEFORE advancing the encoder.
            self.wire = next(self._chunks)
            self.offset = 0
        return self.wire[self.offset:self.offset + maximum]

    def written(self, count):
        if not 0 < count <= len(self.wire) - self.offset:
            raise ValueError('sdk_memory_write_progress')
        self.offset += count
        self._written += count
        return self._written == self.size


class MemoryControl:
    def __init__(self):
        self.rows = {}

    def receive(self, frame, metadata, need):
        need(set(frame) == {'type', 'request_id', 'request'}, 'sdk_memory_shape')
        ident, request = frame['request_id'], frame['request']
        need(type(ident) is str and re.fullmatch('[A-Za-z0-9._:-]{1,128}', ident)
             and ident not in self.rows and len(self.rows) < 128, 'sdk_memory_identity')
        need(type(request) is dict and set(request) == {'subtype', 'server_name', 'message'}
             and request['subtype'] == 'mcp_message' and request['server_name'] == 'cairn',
             'sdk_memory_server')
        message = request['message']
        need(type(message) is dict and message.get('jsonrpc') == '2.0'
             and not set(message) - {'jsonrpc', 'id', 'method', 'params'}
             and type(message.get('method')) is str and type(message.get('params', {})) is dict,
             'sdk_memory_message')
        if 'id' in message:
            value = message['id']
            need(type(value) is int and 0 <= value <= 2**53-1 or
                 type(value) is str and 0 < len(value) <= 128, 'sdk_memory_message_id')
        else:
            need(message['method'] == 'notifications/initialized', 'sdk_memory_notification')
        size, digest = canonical_size_sha256(message)
        self.rows[ident] = dict(message_id=message.get('id'), reply_queued=False, reply_written=False,
            input_bytes=size, input_sha256=digest, frame_raw_bytes=metadata[0],
            frame_raw_sha256=metadata[1], frame_encoding=metadata[2])
        return MemoryRequest(ident, message)

    def reply(self, ident, result, need):
        need(type(ident) is str and ident in self.rows, 'sdk_memory_reply_unknown')
        row = self.rows[ident]
        need(not row['reply_queued'], 'sdk_memory_reply_duplicate')
        need(type(result) is dict, 'sdk_memory_result')
        # Per-result transport ceiling, NOT the original 9500 selected-input
        # qualification. That charges the native-rendered tool_result, including
        # errors/repeats; an MCP response is not an equivalent measurement.
        need(canonical_size_sha256(result)[0] <= 32000, 'sdk_memory_result_bound')
        response = dict(jsonrpc='2.0', id=row['message_id'] if row['message_id'] is not None else 0,
                        result=result)
        document = dict(type='control_response', response=dict(subtype='success',
            request_id=ident, response=dict(mcp_response=response)))
        write = MemoryWrite(ident, document)
        row['reply_bytes'], row['reply_sha256'] = write.size, write.digest
        row['reply_queued'] = True
        return write
