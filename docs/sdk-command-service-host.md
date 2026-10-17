# Ordinary command-service composition fixture

The three `trial_sdk_command_service_fixture_*` and
`trial_sdk_command_service_host_cases.py` sources define a finite ordinary-host
composition of the real parent transport/coordinator, child runtime and command
job. Source existence and pure checks do not imply a host run or admission.

The authored fixture executable validates its fixed synthetic route, waits for
actual stdin EOF, emits finite UTF-8 stdout and invalid-byte stderr, then exits17.
It opens no Cairn socket or credential and invokes no model, API or native SDK.
The child builder uses the actual RuntimeBinding and serve loop. The parent must
consume READY, drive one command under the shared gate and original tickets,
validate exact decoded text and the service-reported CLI status, end result
aliases, release the operation, consume BYE and close its channel. It then drains
both actual service stdio EOFs and observes its independently owned service wait
before retiring standing storage. The CLI report and parent service wait are
different observations.

The final report copies the transcript and renders its request, raw-stream and
text SHA-256 digests as hexadecimal strings. The original transcript keeps its
byte digests. Unknown bytes elsewhere still refuse JSON serialization.

The selected source quotes are combined parent/child standing raw5184 bytes,
including a512-byte parent diagnostic slab, and128MiB working; parent operation
raw40000/working64MiB; child operation raw65536/working64MiB, including child
preparation raw40000/working32MiB and the original job quote. These are explicit
logical subsets of the original128KiB raw and512MiB combined limits. They do not
measure process/kernel memory or replace the original aggregate controls. Every
actual wire, command and service-diagnostic byte remains in the original4MiB
traffic ledger. Any positive service diagnostic makes this selected fixture fail;
its observed prefix is retained rather than exempted.

An explicit fresh checker must pin the complete selected import/source closure,
copy it into a private source snapshot and record a consume-once intent before
launch. The selected outer mechanism is the existing
`check_trial_sdk_native_process_owner.py` supervisor: fixed ordinary leader,
held pidfd/WNOWAIT, inherited-group containment before leader reap, actual waits
and group ECHILD, work10s/outer12s and64KiB combined retained output. All selected
fixture sources must remain inside that inherited group. The inner fixture has
an8s lifetime and an original synthetic5s callback deadline.

Failure retains original commitments and partial receipts. It does not fabricate
a missing CLI wait/EOF, delete a directory while a service may retain it, replay
the attempt, or turn a group kill into API success. Directory mode and source
hashes are trusted serialized custody assumptions, not hostile same-UID isolation.

Actual observations belong in the selected private evidence directory and a
separate disposition. Full CAIRN-107 still requires the real source/current-read/
credential and native interfaces, authenticated delivery, actual capacity and
the original campaign/usefulness evidence. This ordinary synthetic fixture
supplies none of those results by itself.
