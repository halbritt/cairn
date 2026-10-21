"""UTF-8 accounting without a complete encoded alias of a borrowed document.

Each encoded fragment is at most 512 bytes. A fragment is discarded before the
next is created. Canonical JSON uses the evaluator's original compact encoding;
Unicode JSON tokens are transient working storage, never a second raw document.
"""
import hashlib
import json


def text_size_sha256(text, *, update=None):
    digest, size = hashlib.sha256(), 0
    for offset in range(0, len(text), 128):
        fragment = text[offset:offset+128].encode('utf-8')
        size += len(fragment)
        digest.update(fragment)
        if update is not None:
            update(fragment)
        del fragment
    return size, digest.hexdigest()


def canonical_size(value):
    size = 0
    for token in json.JSONEncoder(ensure_ascii=False, separators=(',', ':')).iterencode(value):
        size += text_size_sha256(token)[0]
    return size
