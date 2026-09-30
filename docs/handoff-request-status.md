# Saved handoffs and request status

A saved handoff is a versioned note. Reading that note does not grant access to
coordination events mentioned in its body. A UUID in prose is not a typed link
or a capability. Event status uses the caller's existing publisher/consumer,
collection and destination checks; recipients see only their own deliveries.

A request delivery marked `handled` does not establish task completion. It can
have no result, and a response group can remain open with task outcome unknown.
Cancellation, failure and collected replies likewise do not prove that the
handoff's work is finished. Check current task evidence before explicitly
revising a saved handoff. Existing lifecycle capture requires an explicit report
that all work in that handoff is complete.

Cairn does not currently infer handoff closure, change its ranking, or rewrite
its history when a referenced request reaches a terminal delivery state.
CAIRN-115's original automatic-closure proposal requires a corrected contract:
any future status annotation must preserve event visibility, distinguish request
handling from task outcome, and leave retained note bodies unchanged. No new
status-sharing permission or endpoint is established by this document.

The regression `TestHandoffLinksDoNotConferEventAccessOrTaskClosure` exercises two
handled requests with no replies, a shareable handoff readable by a third
principal, denied event/group reads for that principal, and unchanged retained
note history. It characterizes existing behavior; it does not validate a future
link or annotation feature.

See [agent events](agent-event-fabric.md), [response groups](response-groups.md)
and [record history](record-history.md) for the implemented contracts.
