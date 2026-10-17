# Parent read custody: isolated composition draft

Implemented: callback-local parent custody observes offers only from the held
CommandOperation's successful service response before RPC RESULT_META. It checks
the original operation allocation before parsing. Pull authorization uses that
retained receipt/handle/record/version inventory, not a producer-supplied list.
Actual server authorization remains authoritative. History/pull-evidence are not
yet supported by this narrower candidate. It is not a general API replacement.

SharedCoordinator.prepare_next_command requires installed custody, checks the
actual pending request before operation construction, and confines request
references to returning handoff frames. It returns only progress. Legacy explicit
prepare_command remains compatible; when custody is installed it also checks it.
Exceptions use the existing poison/freeze boundary; traceback aliases stay
charged. RPC clears custody before its original callback allocation release.

Observation runs under the held command-parent working allocation. All original
command working still transfers cumulatively to the callback before retirement.
The additional parsed response, old/new offer dictionaries and normalization
scratch are working allocations requiring the final complete physical inventory;
positive ledger reservations are not proof they fit. Normalized offer metadata
must fit the existing document ceiling before publication, including added prefix
spans. No raw/working quote, aggregate ceiling or traffic charge is reduced.

Complete authenticated engine units establish these pull forms: whole pull;
exact match_span; lexical summary offset extended by min(1536,65536-offset);
and a prefix (0,min(768,returned-span-offset)) after an actual matching record
response. opening_bytes requires an admitted span and cannot exceed the768-byte
nonoverlapping prefix. The actual engine currently obtains its missing opening
with a second span request rather than sending opening_bytes. Synthetic tests run
its current_span_pull and attach_source_opening through real adapter/client/RPC
and service code with inert OS edges. Unsupported forms refuse before forwarding.

Still missing: a fixed builder binding the selected parent policy to actual
service and producer configuration custody. A successful service response is
observed on the owned channel and the service enforces its own scope, but that
is not proof of identity between a separately supplied policy object and the
future launched service/producer configuration. Do not treat bind_read_custody's
policy argument as such proof. This draft is NOT selected for main adoption or
runtime use until that construction contract is implemented and reviewed.

No producer entrypoint is implemented here. The proposed direct recall/observe
shortcut was not built: handle_event has session/project/event validation,
no-memory/disabled gates, event_state binding/reset, lock/persistence and failure
state obligations. Any split must preserve them or place them on a concrete
owner with parity evidence. ProducerClient only validates numeric ClientQuote;
it has no held-original-allocation bootstrap. ProducerResources lives with the
parent and owns its original allocation, owner and DATA stream. Copying that
bank into another process or accepting a serialized boolean/quote would not
establish the original parent's live reservation.

The next composition change must provide a fixed parent-owned bootstrap that
binds original producer owner/ticket/deadline and immutable engine/config/input
before materialization, then transports that prepared input over the existing
owned producer stdin. SDKProducerDriver currently streams only the original
Callback.iter_input_json and authenticates exactly those bytes. Introducing a
configuration/state envelope requires an explicit source change to that driver
and paired fixed entrypoint, with distinct original-event and envelope hashes,
charged copies and actual handle_event parity. A connected client alone cannot
substitute for that boundary. No new FD/process, generic adapter or authority
boolean is proposed. Native launch and unsupported_native remain unavailable.

Tests distinguish16 new cases (five boundary observations, eleven custody/caller
cases) from30 existing coordinator/phase regressions. The combined46 pass does
not establish complete physical fit, a running producer, native admission or
full107 completion. Initial tests also collected imported CoordinatorTests;
that import was corrected to a module namespace. Historical logs remain retained.
