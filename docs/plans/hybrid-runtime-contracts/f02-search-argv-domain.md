# F02 search query argv domain

This source-only unit resolves the unrun R1 relation from final composition
review `ebad2e65-ca53-4dcf-9835-584920af88fb/v1`. Its independent starting point
is accepted composition `ce98790e0cd17ef86486cf3e79d8c271eab46009`, tree
`8d76d5493c2e268a42504e9070606e06d4cfcade`.

The first two actual Source/Go process pairs use Claude PreCompact user text
and checked UserPromptSubmit recall containing `"a\u0000b"`. Both normalize
the phrase away: its one-letter fragments supply no retained terms, so
`quoted_phrases` does not retain it as an anchor. Both queries are NUL-free.
Capture invokes the local selector once, creates two records and commits the
same state; recall makes the same version/index calls and emits the same
delegation block. Neither result supports a general message-level NUL ban.

Only the external Unix API and selector are synthetic. These JSON events
exercise real Source bodies, bounded dialogue and retrieval intent; they are
not recorded native writer evidence. Further quoted phrases with retained
terms were tested separately. With `"alpha\u0000beta"`, Source capture performs
its initial valid checkpoint lookup, then raises ValueError before the selector
and preserves exact seed bytes. Go performs two NUL-bearing candidate searches,
invokes the selector, creates two records and commits state. Source prompt recall
raises ValueError before any API call; Go queries and emits context. This is an
actual Source/Go RED in `search1374-retained-nul-red.log`. The run used dirty
`a26c5a75` with only the new regression methods added and unchanged production
code; it is not mislabeled as a clean committed run.

The first exploratory run's equality assertions passed;
its incorrectly predicted refusal assertions failed. That log is retained as
`search1374-first-pairs.log`, not presented as a Source/Go RED.

Owner decision `98d46758-c61c-42b4-9912-76cf9dfb0dc0/v1` keeps three input
classes distinct: Python-writer and recorded hook inputs require paired exact
behavior; malformed or hostile inputs require stable safe Go refusal; resource
and interpreter limits require documented Go behavior. JSON representability,
writer shape and actual native admission are separate claims. This unit does
not establish all-input CPython parity or native acceptance.

Foundation owns final review, landing, push and cleanup. This unit does not
change provider parsing, capture title guards, receipt/activation, binding,
transport, indexview, Source, Coordination, host configuration or staged rows.
