# SDK controller input seal

`SDKControl.seal_input()` ends the controller's input phase once. The external
session owner must first validate its terminal document and session binding,
recheck held source custody, and ensure no active callback remains in its pump.
The controller accepts no result, permit flag or caller success assertion.
It neither closes stdin nor observes a native process, producer, wait or EOF.

The transition requires a live controller with successful initialization, the
complete reported user write, an empty current write queue, every observed
callback's reply fully reported written, and no unanswered or mandatory
unobserved reply reservations. Both SessionStart and UserPromptSubmit must have
been observed and answered. Replayed callback responses are not required:
replay correlation does not establish native settlement.

On success the method returns `None` and sets the sticky `input_ended` ledger
field to `True`. It queues no bytes and changes no write counts or callback
rows. The state does not establish that reported counts came from actual
syscalls. Actual stdin closure and its uncertain outcomes belong to the external
owner after this transition. A close failure cannot reopen the controller.

Before sealing, incomplete initialization, user input or observed replies refuse
with `sdk_input_not_drained`; unresolved mandatory events refuse with
`sdk_required_callbacks_unobserved`. These refusals follow the existing
terminal controller-failure rule. A closed or failed controller refuses with
`sdk_unusable`.

After sealing, another seal, `receive`, `receive_owned`, `reply`, `written`,
`callback_timeout` and `context_capacity` refuse with `sdk_input_ended`.
These post-seal refusals leave the controller ledger unchanged and do not poison
drain queries. `receive_owned` still consumes its token before refusal.
`pending_write()` remains `None` and `write_pending` remains `False`,
allowing an external pump to drain ordinary output and actual wait/EOF.
A pump can independently treat post-seal control traffic as fatal and call
`write_failed()`; ordinary controller failure/close then makes readiness
queries unusable. `close()` and `write_failed()` never reset `input_ended`.

The field is an additive Boolean in `cairn.sdk-hook-delivery/2`, initially
`False`, including the ledger nested in `pristine_binding()`. Pristine
review explicitly requires input not to have ended. Consumers that pin complete
pristine metadata must regenerate their projection from the actual original
controller; old hardcoded hashes or an exact old ledger-key whitelist are not
compatible with this added field. Existing schema name, callback rows, native
settlement/admission fields and budget ceilings are unchanged. The ledger-size
check includes the added field before accepting a callback.

The 8500-byte wire, 9500-byte selected-input, 64-KiB/128-row ledger and five-second
callback limits remain unchanged. Remaining-budget diagnostics continue to
describe arithmetic room, not permission to write after input ends.

`scripts/test_trial_sdk_input_seal.py` uses real controllers and strict
`OwnedJSON` tokens. Its reported write counts are pure model inputs; the tests
launch no native, producer or provider. They cover completed input, early seal
refusals, post-seal token consumption and stable refusals, and sticky state after
failure or close. The actual terminal parser, source recheck, physical close,
native compatibility and complete session evidence remain separate work.
