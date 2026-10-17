"""Paired fixed entry functions. Import is inert; launch is not selected here.

Only the parent-owned private snapshot/argv/stdin path may call these entries.
The serialized quote is not a resource capability. The parent retains the actual
original bank ticket through child wait and every result/error alias lifetime.
"""
import hashlib
import os
from pathlib import Path
import sys
import time
import types

# Isolated Python ignores ambient PYTHONPATH/site-user imports. The only authored
# import root is the original parent's held, authenticated private snapshot.
if __name__ == '__main__':
    if not sys.flags.isolated:
        raise SystemExit('fixed_entry_not_isolated')
    sys.path.insert(0, str(Path(__file__).absolute().parent))

from trial_sdk_fixed_config import bindings, require
from trial_sdk_incoming import BodyDecoder, MAX_DOCUMENT_BYTES, canonical_chunks, canonical_size_sha256, parse_document
from trial_sdk_engine_adapter import EngineMemoryAdapter, engine_context
from trial_sdk_engine_memory_rpc_client import ProducerClient, ClientQuote
from trial_sdk_command_service_runtime import serve


def read_document(source, expected, deadline_ns, *, clock=time.monotonic_ns):
    decoder = BodyDecoder(MAX_DOCUMENT_BYTES)
    while True:
        require(clock() < deadline_ns, 'fixed_entry_deadline')
        block = source.read(512)
        require(type(block) is bytes and len(block) <= 512, 'fixed_entry_read')
        if not block:
            break
        decoder.feed(block)
        block = None
    text = decoder.finish()
    require(decoder.raw_sha256 == expected, 'fixed_entry_digest')
    return parse_document(text, decoder.raw_bytes)


def invoke(engine, bootstrap, client):
    """The original handle owns validation, locks, state and reporting finally.

    No returned context exists until handle has completed final save_state.
    On error the original exception/traceback is retained, not converted to {}.
    """
    require(type(bootstrap) is dict and set(bootstrap) == {
        'schema', 'config', 'input', 'input_bytes', 'input_sha256', 'event',
        'deadline_ns', 'rpc_path', 'client_raw', 'client_working'}, 'fixed_bootstrap_shape')
    require(bootstrap['schema'] == 'cairn.fixed-producer/1', 'fixed_bootstrap_schema')
    event = bootstrap['input']
    require(canonical_size_sha256(event) == (bootstrap['input_bytes'], bootstrap['input_sha256'])
        and event.get('hook_event_name') == bootstrap['event']
        and bootstrap['event'] in ('SessionStart','UserPromptSubmit','PostToolUse','PostToolUseFailure'),
        'fixed_original_input')
    with EngineMemoryAdapter(engine, bootstrap['config'], client):
        result = engine.handle(bootstrap['config'], event)
        engine_context(result, bootstrap['event'])
        return result


def load_engine(directory, expected):
    """Compile the held private source with bounded explicit read buffers.

    The parent authenticates/holds the entire snapshot and its path edges. This
    additional digest detects drift before execution; it is not OS isolation.
    Unicode fragments/join, UTF-8 caches and compiler storage remain unproved
    simultaneous backing under the original allocation, not a raw-fit claim.
    """
    import codecs
    import stat
    path = Path(directory) / 'memory.py'
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
                and 0 < before.st_size < 256*1024, 'fixed_engine_source')
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        fragments, digest, count = [], hashlib.sha256(), 0
        while count < before.st_size:
            block = os.read(fd, min(8192, before.st_size-count))
            require(bool(block), 'fixed_engine_changed')
            count += len(block); digest.update(block); fragments.append(decoder.decode(block))
            block = None
        fragments.append(decoder.decode(b'', final=True))
        identity = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        require(not os.read(fd,1) and identity(before) == identity(os.fstat(fd))
                and digest.hexdigest() == expected, 'fixed_engine_changed')
        source = ''.join(fragments); fragments = None
        engine = types.ModuleType('cairn_fixed_producer_engine')
        engine.__file__ = str(path)
        exec(compile(source, str(path), 'exec'), engine.__dict__)
        require(identity(before) == identity(os.fstat(fd)), 'fixed_engine_changed')
        return engine
    finally:
        os.close(fd)


def producer(source, target, bootstrap_sha256, deadline_ns, directory, engine_sha256):
    """Called only by the fixed parent's actual launch, never as a quote grant."""
    document = read_document(source, bootstrap_sha256, deadline_ns)
    require(document.get('deadline_ns') == deadline_ns, 'fixed_entry_deadline')
    # The original parent already holds the complete allocation before first
    # input, module materialization or client construction; no bank is copied.
    engine = load_engine(directory, engine_sha256)
    client = ProducerClient(deadline_ns, ClientQuote(document['client_raw'], document['client_working']))
    client.connect(document['rpc_path'])
    result = invoke(engine, document, client)
    client.close()  # Actual protocol closure precedes publication, not graph retirement.
    for block in canonical_chunks(result):
        require(time.monotonic_ns() < deadline_ns, 'fixed_entry_deadline')
        written = target.write(block)
        require(written == len(block), 'fixed_entry_output')
    target.flush()


def main():
    # No optional argv, fallback path or caller-selected function/module.
    args = sys.argv[1:]
    require(len(args) in (3,5), 'fixed_entry_arguments')
    mode, digest, deadline = args[:3]
    deadline = int(deadline)
    directory = str(Path(__file__).absolute().parent)
    if mode == 'service' and len(args) == 3:
        with open(Path(directory)/'service.json', 'rb', buffering=0) as source:
            document = read_document(source, digest, deadline)
        runtime, _ = bindings(document)
        require(runtime.deadline_ns == deadline, 'fixed_entry_deadline')
        serve(runtime)
    elif mode == 'producer' and len(args) == 5:
        require(args[3] == directory, 'fixed_entry_directory')
        producer(sys.stdin.buffer, sys.stdout.buffer, digest, deadline, directory, args[4])
    else:
        require(False, 'fixed_entry_arguments')


if __name__ == '__main__':
    main()
