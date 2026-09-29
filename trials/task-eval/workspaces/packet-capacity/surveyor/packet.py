"""Encode a request packet for a small-capacity backend."""
import json

CAPACITY = 640  # bytes accepted by the smallest backend


def encode(schema, excerpt):
    """Return the packet; the source excerpt is truncated to fit CAPACITY."""
    head = json.dumps({"schema": schema}, indent=2)
    room = CAPACITY - len(head) - 32
    return head + "\n--- excerpt ---\n" + excerpt[: max(room, 0)]
