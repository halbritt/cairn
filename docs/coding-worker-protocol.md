# Pure coding T/H/E codec

This internal codec validates selected packet shape and declared run/source
binding. It does not authenticate runtime, hook provenance, permissions, native
admission, actual wait or persistence. It adds no provider execution route.
Default byte-only D/E and its common E validator are unchanged.

`trial_coding_protocol` exposes `validate_start`, `validate_timing/decode_timing`,
`validate_hooks/decode_hooks` and `validate_coding_end/decode_coding_end`, with
keyword `run_id` and `source_manifest_sha256`; `coding_packet(tag,payload)` encodes
a one-byte tag plus network-order uint32 length. T is at most 1KiB; D/H/E at most
64KiB. Strict selected_native JSON rejects duplicate keys, nonfinite constants,
malformed/deep input and extra payload fields. Helpers raise fixed ProtocolError,
never payload/exception text. Null timing/hooks require fixed unknown observations.

START is the fixed capture's out-of-band control record with kind CAPTURE_START,
schema cairn.worker-capture-start/1, run/source hashes and start_monotonic_ns.
Only a separate reviewed fixed capture can supply actual origin immediately before
actor Popen. The coding transport must hold capture time/time_for_children
namespace FDs matching controller clock domain before gate and obtain its own
post-read monotonic_ns. Gate time or sink arrival never substitutes for origin.
The codec itself accepts no clock callback and proves no clock provenance.

T schema cairn.worker-capture-timing/1 contains run/source binding, nullable
start_monotonic_ns/received_monotonic_ns, offset, bytes, sha256 and observations.
Exactly one T precedes a nonempty unchanged D. Its offset/length/hash binds that
D; origin remains constant and known read timestamps never decrease. Known values
produce (received-start)/1e9. Explicit capture_timing_unknown returns null elapsed
even when numeric timestamps were supplied. The real transport still owns origin,
timestamps, malformed/missing control policy and observed-versus-forwarded bytes.

H schema cairn.worker-hook-measurements/1 contains run/source binding, rows and
observations. It occurs once after T/D, before E. Rows are null with
hook_measurements_unknown or at most 128 exact selected hook rows: four existing
events, canonical stdout hash, stdout/stderr/counts 0..1e9, finite seconds 0..1e12,
exit -255..255, exact five memory-call counters. No raw stdout/stderr, prompt,
argv, credentials, IDs or unknown fields accepted. Hook rows are declarations
until root binds independently owned measurement. Empty rows do not satisfy the
memory-on required-hook contract by themselves.

Coding E schema cairn.worker-coding-end-observation/1 preserves every current
common E fact and adds wire_sha256, wire_bytes, timing_packets,
hook_packet_sha256 and coding_observations. Common canonical actual-wait/null,
release, input counts, stream/observed digests and concurrent unknown checks are
reused without dropping coding facts into a seemingly complete common report.
D hash/count remains native bytes only. Wire hash/count covers complete framed
T/D/H packets before E; hook_packet_sha256 hashes complete framed H. It excludes
E itself to avoid a recursive digest. Matching these declarations alone is not
runtime authentication or cell validity.

`CodingDecoder(run_id,source_manifest_sha256).feed(tag,payload)` returns
('timing',None), ('data',D,elapsed_or_None), ('hooks',H), or ('end',E).
It holds one pending T, selected bounded H, counters and running hashes, with no
packet history or accumulated D. Malformed ordering/binding poisons it; earlier
selected observations remain distinct from a refused end. Root fixed sink must
read EOF and independently close writer/persistence, actual wait/containment and
runtime bindings beyond decoder acceptance or returned CLOSED.

`wire_ceiling(B,N)` bounds pre-E wire as B + N*(1024+10) +65536+5:
B native D bytes, at most N<=B nonempty D/T pairs, two 5-byte packet headers per
pair, one 64KiB H/header. There is no lower chunk-count allowance. At 4MiB raw with
one-byte fragmentation, pre-E ceiling is 4,341,170,181 bytes; at 64MiB internal
maximum it is 69,457,739,781 bytes. E adds at most 65,541 more bytes. These are
large finite worst cases, not comparable wire/CPU cost to unframed native output.
No packet-history allocation, queue/resource/wall enlargement or native allowance
increase is authorized. The actual owner must continue enforcing 128KiB queue,
aggregate 512MiB/no-swap/32PID/one-core and frozen wall budget.

Nine constructed pure tests cover complete framed binding, tampering/poisoning,
100 one-byte fragments, order/clock inconsistencies, explicit unknowns, exact H
privacy/size/count/huge-number bounds and common closure refusal. They attest no
native runtime, provider account or real capture timing. The separate transport
and provider-free real process fixtures follow in another reviewable commit.
