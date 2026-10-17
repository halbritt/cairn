# Compiler raw preflight source candidate

The compiler previously retained complete prompt and instruction UTF-8 encodings
while allocating the legacy JSON message, then checked its expanded byte cost.
At the real resource-bound SDK preparation boundary, permitted 9500-byte prompt
and instruction files could therefore retain 76061 raw bytes before refusal
(9500 NUL prompt bytes + 9500 instruction bytes + 57061 escaped message bytes).
Direct compiler callers have no Snapshot file ceiling: their whole raw input
encodings could be arbitrarily larger before `coding_input_budget`.

`compile_launch` now validates/counts both inputs with strict UTF-8, encoding at
most 2048 Unicode characters (8192 raw bytes) at a time. Each raw block is cleared
before the next encode. It still validates the complete prompt, then complete
instruction, before either byte-budget refusal. Invalid Unicode after an oversized
valid prefix, or in the instruction after an oversized prompt, still yields
`coding_input`. Identity/type/empty-input checks retain their original order.

After the original input limits pass, the compiler constructs the same Unicode
message text using `json.dumps(..., ensure_ascii=False)`, default spaces, insertion
order and one final LF. It counts that exact representation with bounded raw
scratch and checks the unchanged 9500-byte total, 512-byte reserve and 1000-byte
minimum hook room before encoding the complete message. Compact/sorted canonical
serialization is not substituted. Accepted message bytes, permissions and argv
remain unchanged. The SDK wrapper still applies its own stricter controller budget.

For ordinary string inputs, named compiler raw lifetime is now:

| Phase | Named compiler raw payload |
| --- | --- |
| Input or legacy-message counting | One block, at most 8192 bytes |
| Ordinary invalid-Unicode or budget refusal | No retained compiler raw block |
| Accepted whole message | At most 7987 bytes, after counting frame returns |
| Permission encoding and return | Admitted message plus unchanged permission bytes (705 at this base) |
| Interruption during counting | Up to one block retained through the exception traceback |

The resource-bound preparation path still closes original owned sources, preserves
the primary exception and retains its original standing allocation on failure.
No refund is inferred from compiler locals being cleared; caller strings, Unicode
JSON text, exception frames and other preparation/controller aliases may survive.
Strict validation scans the full direct input to preserve refusal precedence; it
does not establish a time bound for arbitrarily large direct caller strings.

`test_trial_coding_preflight.py` exercises real encoding calls and live frame aliases
at direct and preparation boundaries, not mocked counting results. It covers
exact byte parity, byte-boundary acceptance/refusal, oversized inputs, invalid
Unicode after large prefixes, JSON escape expansion, whole-message allocation
after scratch ends, original cleanup/accounting, and interruption with a real
counting block retained. The trace instrumentation and synthetic inputs are
test-owned and are not a whole-process memory measurement.

This is a necessary source reduction, not a complete standing quote. All quote
fields and original 128KiB raw / 512MiB combined / 4MiB traffic / five-second
callback limits remain unchanged. Neither the inventory's conditional 33781
ordinary-path payload envelope nor its corrected 33790 refused-decoder-path
envelope is admitted. Complete allocator capacities, Unicode/JSON/interpreter
working storage, controller/callback/caller aliases, kernel queues and native
owner/dispatch storage remain unresolved. No runtime or weekly-evaluation
admission follows from this candidate or its inert tests.
