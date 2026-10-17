# Direct SDK callback writer

`trial_sdk_direct_writer.SDKDirectWriter(control, native)` borrows one pristine
`SDKControl` and a fresh `NativeProcessOwner`. The trusted session builder owns
admission, exclusive access to both objects and the actual direct child. The
writer starts no process, closes no descriptor, handles no callback and grants
no runtime authority. A Node SDK protocol writer must not write the same stdin.
The independently tested Node proxy remains a separate mechanism.

Construct the writer before the external owner starts its fixed child. Each
`advance()` reacquires the controller's current pending suffix and makes at most
one unbuffered write of at most 512 bytes through the native owner. A positive
local count is immediately reported to `SDKControl.written`; would-block leaves
both counters unchanged. Zero denotes idle or a caught would-block, never a
successful zero-byte syscall. A zero/invalid syscall result or another exception
poisons the writer and controller; an original exception remains the primary.
There is no automatic retry after uncertainty.

Calls and controller/native-owner operations must be serialized. No other
writer may use either object or retain the offered buffer. Independent progress
is refused before another write when the native, controller and writer counters
disagree. These checks are bookkeeping under trusted code, not authentication
or protection against private-field/FD mutation. `snapshot()` separates positive
write returns, controller counts, the native input owner's known counts and its
write-outcome uncertainty. A zero known count after an ambiguous exception does
not prove that no bytes reached the child.

An empty control queue leaves stdin open. The future session driver must decide
terminal input from its separately reviewed protocol, then own stop, drain,
actual wait and release through `NativeProcessOwner`. The writer supplies no
terminal policy, readiness loop, producer owner, capture clock, selected H/E
profile, retention or execution lease. Existing complete-bootstrap and campaign
gates remain unavailable. The 8500-byte wire and 9500-byte selected allowances
remain the controller's original limits; this adapter adds no input allowance.

The optional local check is:

```sh
python3 -B scripts/check_trial_sdk_direct_writer.py --output /absolute/fresh/output
```

It consumes an exclusive output directory, freezes fourteen guarded public
source files and uses the existing bounded owner-test supervisor. Its one fixed
leader filename is an explicit alias for `trial_sdk_direct_writer_host.py`, not
a rerun of the original process-owner cases. It has no selectable command,
provider, SDK path, account or namespace option. It observes ordinary Python
echo children only, with ten work seconds, twelve total seconds and 64 KiB of
combined output. Outer observations are saved before later log/pin processing.
Later observation/log/source-check/result-write faults become named errors and
leave the collected waits available to the final stdout summary. Failed partial
artifacts remain consumed; this is not persistence when every output channel
fails. Pure filesystem regressions use constructed wait facts and launch no child.

The cases cover exact initialization/user/four synthetic callback replies,
actual seven-byte writes, a constructed pre-syscall would-block, unchanged
stdin lifetime while idle, false reported progress and an actual seven-byte
effect followed by a constructed exception. They retain separate actual child
and outer waits, EOF/release and FD inventory. Synthetic callback frames do not
qualify the native hook schema, actual producer work, kernel backpressure,
native/model delivery, resource containment, serving capacity or task usefulness.
