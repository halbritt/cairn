# SDK input profile 2 semantics

`scripts/trial_sdk_input_profile.py` and `.cjs` provide pure input receipt parsing
and an explicitly selected profile 2 client state machine. They do not create IPC,
perform native writes, close stdin, authenticate a sender or load the SDK. The
[whole-record codec](sdk-ipc-codec.md) can select the matching profile 2 framing;
that does not establish a deployed receiver or authorize dispatch.

Construct `InputClient(2, maximum)` with a cumulative custody budget 1..3145728.
Python uses exact integers; CommonJS uses safe Numbers. The owner must bind that
budget to the original shared stream accounting; this helper creates no separate
allowance. Global operation IDs must increase and may have gaps. Framing sequence
is a separate per-direction counter. Production endpoints start it at one.

| Request | Direction/body | Correlated reply | Reply body |
|---|---|---|---|
| 9 INPUT_CUSTODY | N2P_DATA, 1..512 bytes | 140 INPUT_CUSTODY_RESULT | uint16BE custodied prefix count |
| 5 STDIN_END | N2P_DATA,0 bytes | 133 STDIN_END_RESULT | status 1 plus uint32BE known native total |

`begin_custody(op, offered)` / `beginCustody` admits one outstanding request only
from ready state. The full offered count must fit remaining cumulative custody
budget before transmission. `result(op,kind,body)` requires exactly the matching
operation and reply kind. Custody count 0 pauses until a separate `writable()`;
positive partial counts return ready, with the unconsumed suffix still owned and
charged by the caller. A future suffix request uses a fresh larger operation ID.
There is no automatic retransmission, rollback, reset or legacy kind 4/131 fallback.

Custody means ownership of staged input, not native acceptance. The intended
receiver collects original spelling as decoded Python working text, validates
the entire line, then drains its bounded native writer. It may refuse a complete
line even after acknowledging earlier fragments. This helper does not implement
that receiver. The separately named Node custody factory also remains outside
this implemented slice; the existing main spawn factory still means actual writes.

`begin_end(op)` / `beginEnd` is ready-only, with no outstanding request. A successful
end result must carry status 1 and total equal to all custodied bytes. Only then is
`known_native_total` populated and state closed. The producer of that result must
have observed actual acceptance of every validated byte and owned native stdin
closure. Parsing status 1, matching totals, stdout EOF or process wait supplies
none of that provenance. These fields remain trusted owner testimony, not
independent native observations or model-delivery evidence.

Invalid order, count, type, size, budget or correlation is terminal. Pending scalar
metadata and confirmed counters remain diagnostic, and subsequent operations
refuse. Explicit `transport_unknown()` / `transportUnknown` also marks unknown
for ready, pending or paused state. Other malformed protocol errors do not invent
a confirmed native total; unknown specifically records explicit transport
uncertainty, not the absence of possible effects from other failed messages.

Parsers borrow exact-width bodies synchronously and retain no body alias. Python
accepts immutable bytes or readonly contiguous unsigned-byte memoryviews. CommonJS
accepts fixed non-shared Uint8Array backing, rejecting shared, resizable, detached
and wrong-width inputs. The caller keeps backing stable during the call. Parser
numbers and snapshots are independent of the supplied body; snapshot mutation
cannot change client state. Raw storage, framing credit, transport copies,
authentication, actual sender/child ownership and aggregate memory remain external.

`make test-sdk-ipc` includes seven Python semantic methods and 36 Node terminal
scenarios plus parser/buffer checks, alongside both codec profiles. Tests use
synthetic receipts and guarded source execution. They do not establish the actual
SDK's producer behavior, native origin/closure, resource fit, runtime admission or
full CAIRN107 usefulness. Original caps, full 126/1900, two weekly campaigns,
dedicated capacity, fresh independent grading and capture HOLD remain unchanged.
