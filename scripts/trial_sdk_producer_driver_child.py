"""Fixed ordinary full-input child; no memory, native CLI or provider access."""
import hashlib
import json
import os
import sys

from trial_sdk_incoming import BodyDecoder, MAX_DOCUMENT_BYTES, parse_document


def write(fd, body):
    with memoryview(body) as view:
        while view:
            count = os.write(fd, view[:511])
            if count <= 0:
                raise ValueError('fixture_write')
            view = view[count:]


def main():
    mode = sys.argv[1]
    if mode not in ('normal', 'empty', 'nonzero', 'malformed', 'duplicate',
                    'input_drift', 'close_unknown', 'read_clock', 'late_write', 'write_unknown', 'generated_hash', 'session'):
        return 4
    if mode not in ('empty','session'):
        write(1, b'{"early":"' + b'x' * 40000 + b'",')
    write(2, b'producer-progress\n' if mode == 'session' else b'stderr-before-input\n' + b'z' * 50000)
    decoder, slab = BodyDecoder(MAX_DOCUMENT_BYTES), bytearray(512)
    while True:
        count = os.readv(0, [slab])
        if not count:
            break
        with memoryview(slab)[:count] as part:
            decoder.feed(part)
    text = decoder.finish()
    graph = parse_document(text, decoder.raw_bytes)
    assert graph['extra'].startswith('Full callback content: café 😀')
    if mode != 'empty':
        tail = dict(input_bytes=decoder.raw_bytes, input_sha256=decoder.raw_sha256,
            event=graph['hook_event_name'], context='ordinary producer context: café 😀')
        wire = json.dumps(tail, ensure_ascii=False, separators=(',', ':')).encode()
        write(1, wire if mode == 'session' else wire[1:])
        if mode == 'malformed':
            write(1, b'\xff')
        elif mode == 'duplicate':
            write(1, b'{}')
    write(2, b'\ninput-eof-observed\n')
    return 17 if mode == 'nonzero' else 0


if __name__ == '__main__':
    raise SystemExit(main())
