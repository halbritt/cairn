# Selected attempt retention

`scripts/trial_attempt_retention.py` is a separate selected failure-channel
writer for a fixed composition worker. It accepts a newly consumed `Retention`
factory object and duplicates its held directory descriptor. The fixed worker
must create it before admitting actor bytes. It does not reopen the run by pathname, delete partial files,
launch processes or admit providers.

`prepare(composer, hook_rows)` accepts the exact `CodingStream` type for the
same run, and refuses a previously prepared success-retention object. It closes
the composer and writes its selected observations, intent digest and source
manifest digest to the single exclusive `ATTEMPT-EVIDENCE.json` leaf. There is
no raw dictionary or file-content ingestion API. This path writes no success
SELECTED or PREPARED record and makes no task-success or cell-validity claim.

`prepare_with_end(composer, hook_rows, end_payload)` additionally decodes the
closed common E/1 declaration and checks its run, source manifest, D-byte count
and D-byte digest against the composer. A malformed declaration, incomplete
composer digest or mismatched binding consumes the writer before output.
Explicit unknown process/input observations may be retained on this failure
path. Valid shape and matching declarations do not authenticate an exit.

`prepare_success_observations(composer, hook_rows, end_payload, prepared_receipt)`
requires the original retention writer to be prepared already. It checks the
typed receipt's run and intent, and reads the existing private `SELECTED.json`
back through its held directory descriptor to verify size and hash. The end
actor identity and return code must also match the original private
`PREPARED.json` exit snapshot; its receipt and false attestation flags are
checked with a bounded duplicate-key-rejecting read. These comparisons bind
the original declarations without granting them authentication. The end
declaration must report known zero actor and capture waits, capture release,
complete input delivery and no capture/end uncertainty. The worker must supply
its actual observations; these declaration checks alone cannot establish them.
This path writes `OBSERVATIONS.json` beside the success files and closes the
composer without calling the success reader's finish operation again.

Both extended paths retain the strict end declaration and a nullable selected
success receipt. The observation receipt has the same selected hash/size shape
as the failure receipt. Neither path grades the work. A success-observation
write fault consumes this writer, preserves existing success files and any
partial observation file, and permits no failure fallback or success replay.
The fixed worker must treat that outcome as incomplete persistence.

The writer checks its held directory identity, owner and 0700 permissions,
creates the leaf with exclusive/no-follow access, flushes and fsyncs it and the
directory, then reads it back. Readback requires the written inode, regular-file
type, current owner, 0600 permissions, one link, expected size and exact bytes.
The existing 64 MiB selected-storage limit applies. Encoding happens before the
size check; the storage cap does not independently prove a hard peak-memory or
I/O deadline. The composer supplies the separately bounded selected input.

Preparation is consumed before processing begins. Refusals, exceptions,
interrupts and later write/readback/fsync faults leave that writer unavailable;
any partial file stays consumed. Closing drops the owned duplicate once and
reports uncertainty on identity or close faults. The original retention writer
has its own descriptor and close outcome. The fixed worker must close both
writers before it can emit a CLOSED declaration.

The immutable preparation receipt contains the selected file hash and byte
count; `return_receipt` remains false.
The receipt names this digest `selected_sha256`; it is distinct from the
`source_manifest_sha256` carried inside the file. The existing wire CLOSED field
`manifest_sha256` carries this selected artifact's digest explicitly.
Neither that receipt nor the retained file establishes runtime closure. The actual worker must validate its bounded end
packet and stream/source binding before preparation. The external controller
must separately observe actual worker return, containment release, complete
control-channel draining and the exact CLOSED declaration. This module alone
cannot establish any of those facts or authenticate process/account identity.
Same-UID changes, host loss and blocking filesystem calls remain within the
external ownership and deadline contract.

Tests use disposable local directories and constructed failed native streams.
They exercise selected-only output, descriptor ownership after original close
or directory rename, run/type refusal, existing symlinks, lost privacy, fsync
failure, hard links, replaced inodes, changed bytes, constructor interrupts and
close uncertainty. They run no provider, campaign, production database or grade.
End-binding tests additionally exercise source/stream drift, known nonzero exit,
unknown exit and incomplete input declarations, changed success receipts,
selected-file symlinks and success-observation fsync failure. Their process
declarations are constructed fixtures; they do not observe an actual actor.
