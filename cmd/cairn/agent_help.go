package main

import (
	"flag"
	"fmt"
	"reflect"
	"strings"

	"github.com/halbritt/cairn/localapi"
)

// commandHelp is human-readable output for an explicitly requested help path.
// Ordinary command results retain their JSON response envelope.
type commandHelp string

const agentHelp = `Usage: cairn agent [--token-file FILE] [--socket PATH] OPERATION

Connection flags precede OPERATION. Normal calls use the authenticated Unix API.
Help needs no credentials, connection, home directory or stdin.

Everyday operations:
  publish, notice-flush, inbox, ack, complete, retry, renew: durable agent events (use --help)
  subscribe, unsubscribe, subscriptions, events, event-status, event-stats
  remember     Save a selected note using text, --stdin or a JSON draft
  send         Journal selected assignment source and explicit publication (--help)
  search       Find relevant notes using a query or --browse
  pull         Read a selected note using the returned receipt and handle
  pull-evidence Read a selected supporting source
  version      Identify the CLI and running API builds
  clients      List the client implementations this API process observed for your own principal (--help)

JSON operations (one request on stdin):
  agent-register, agent-context, agent-heartbeat, agent-leave, agent-directory, agent-resolve
  session-inbox-ready, session-inbox-claim, session-inbox-advance, session-inbox-reconcile Native host delivery
  session-inbox-pending Native host busy-session inbox counts (read-only)
  session-delivery-observe Native host delivery diagnosis (host-reported)
  session-inbox-control, session-tool-capture, session-tool-stop Native request cancellation
  wake-attempts Inspect current or historical wake attempts
  wake-claim, wake-change Host supervisor coordination
  worker-register, worker-heartbeat, worker-health Host slot availability
  worker-list, pool-list Inspect configured fresh-work capacity
  event-watch  Read one bounded inbox arrival page with a resumable cursor
  revise       Replace only a note's body
  append       Add text verbatim, including your chosen paragraph separators
  replace      Replace one uniquely matching passage
  set-sections Replace, delete or append ATX Markdown sections by heading title
  edit         Replace a complete draft; preserve metadata intentionally
  cite         Replace an ordinary note's evidence citations
  history      Inspect retained versions of one note
  handoff-request-status  Read handling of request events you link to a saved handoff
  assessments  Read owned task-review history
  assessments-page  Read owned task-review history in bounded pages
  assess-run   Append or correct a qualitative task review
  recompile    Reconstruct an owned historical receipt for inspection
  explain-page Inspect an owned receipt's retained candidate metadata in pages

Use cairn agent OPERATION --help for these JSON operations and their examples.
Save your request in a file, then execute:
  cairn agent --token-file TOKEN_FILE OPERATION < request.json

Use a new request UUID for each intended write; retry identical JSON after an
uncertain response. Read the complete current note before changing it.
The API still owns authorization, version checks and privacy.
`

type agentJSONHelpSpec struct {
	Detail  string
	Example string
}

var agentJSONHelp = map[string]agentJSONHelpSpec{
	"session-inbox-ready": {
		Detail:  "Native host adapter only: read the first eligible delivery ID for a live idle execution without claiming or changing work. Empty when busy, held or unavailable. This is a snapshot; only the native hook claims ownership.",
		Example: `{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"}`,
	},
	"session-inbox-pending": {
		Detail:  "Native host adapter only: count requests, notices and responses that native delivery would hand this execution at a later boundary, whatever its presence state. Kinds and the newest position only; no senders, sources or bodies. Claims, leases and acknowledges nothing; truncated at 100.",
		Example: `{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"}`,
	},
	"session-delivery-observe": {
		Detail:  "Native host adapter only: replace this execution's current host observation of why its ready delivery has or lacks an automatic wake. Closed condition codes; delivery-scope conditions name the exact observed delivery. Refresh a persisting condition within 60 seconds; the store stamps receipt time, ignores older or repeated observations and labels the result host_reported, never a delivery guarantee. condition none clears it without deleting ordering.",
		Example: `{"session":{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"},"delivery_id":"DELIVERY_UUID","condition":"wake_retained","wake":{"transport":"claude-channel","status":"submitted","attempted_at":"2026-09-27T12:00:00Z"},"observed_at":"2026-09-27T12:00:30Z"}`,
	},
	"event-watch": {
		Detail:  "Read inbox arrivals without claiming. Cursor binds inbox, filter, read destination and database generation; STALE_CURSOR requires an explicit rescan.",
		Example: `{"cursor":"RETURNED_CURSOR","limit":50}`,
	},
	"session-inbox-claim": {
		Detail:  "Native host adapter only: claim one event at a supported turn boundary and retain exclusive session ownership beyond lease expiry. Retry the same request UUID. Use the existing base profile and explicit session.",
		Example: `{"request_id":"NEW_UUID","session":{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"}}`,
	},
	"session-inbox-advance": {
		Detail:  "Native host adapter only: after explicit settlement of one pinned nonexclusive notice, atomically transfer its session hold to the next already-eligible nonurgent notice in the same native turn. At most three notices per batch. A request or urgent notice at the head waits for the next turn. Retry the same request UUID after uncertainty; no item is acknowledged by this operation.",
		Example: `{"request_id":"NEW_UUID","session":{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"},"attempt_id":"SETTLED_ATTEMPT_UUID","version":1}`,
	},
	"session-inbox-reconcile": {
		Detail:  "Native host adapter only: release its attempt after delivery_completed, process_exited, turn_ended or a confirmed cancel_confirmed cleanup. An unfinished delivery becomes failed after an observed process/turn end; uncertain work is never replayed automatically. An obsolete execution may close only its own attempt.",
		Example: `{"request_id":"NEW_UUID","session":{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"},"attempt_id":"ATTEMPT_UUID","reason":"process_exited"}`,
	},
	"session-inbox-control": {
		Detail:  "Native host adapter only: read the unfinished attempt owning this inbox, including any pending operator cancellation, pinned native turn, stop state and captured tool processes. Uses the session's current execution; the response echoes an earlier execution's own attempt for recovery.",
		Example: `{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"}`,
	},
	"session-tool-capture": {
		Detail:  "Native host adapter only: durably record tool processes observed for the attempt's exact pinned native turn. Items from any other turn are refused. Retain one stable request UUID per capture batch.",
		Example: `{"request_id":"NEW_UUID","session":{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"},"attempt_id":"ATTEMPT_UUID","items":[{"item_id":"exec-ID","process_id":"PID","command":"/bin/sleep 30","native_turn_id":"TURN_ID"}]}`,
	},
	"session-tool-stop": {
		Detail:  "Native host adapter only: report bounded stop progress for the cancelled attempt's turn (interrupted, ended, ambiguous) and captured tools (stop_issued, terminated, unavailable). The hold releases only through session-inbox-reconcile with reason cancel_confirmed.",
		Example: `{"request_id":"NEW_UUID","session":{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"},"attempt_id":"ATTEMPT_UUID","turn_stop":"interrupted","tools":[{"item_id":"exec-ID","stop_state":"terminated"}]}`,
	},
	"agent-register": {
		Detail:  "Register a conversation using the existing profile. New request IDs resume the binding/native-session pair with a new execution UUID. No session credential is created.",
		Example: `{"request_id":"NEW_UUID","binding":"codex-default","native_session_id":"NATIVE_THREAD","metadata":{"harness":"codex","project":"rhumb","workspace":"/work/rhumb","state":"busy","delivery_mode":"existing-session"}}`,
	},
	"agent-context": {
		Detail:  "Replace reported session context with a revision check. Heartbeats preserve this context.",
		Example: `{"request_id":"NEW_UUID","session":{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"},"expected_revision":1,"metadata":{"harness":"codex","project":"rhumb","workspace":"/work/rhumb","state":"idle","delivery_mode":"existing-session"}}`,
	},
	"agent-heartbeat": {
		Detail:  "Refresh 90-second presence or mark the current execution stopped. Use the base profile; obsolete execution UUIDs are refused.",
		Example: `{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"}`,
	},
	"agent-leave": {
		Detail:  "Refresh 90-second presence or mark the current execution stopped. Use the base profile; obsolete execution UUIDs are refused.",
		Example: `{"agent_id":"AGENT_UUID","execution_id":"EXECUTION_UUID"}`,
	},
	"agent-resolve": {
		Detail:  "Resolve exact selectors to unique, ambiguous, no-match or stale. Only unique includes a resolution; include it in event-publish to check freshness atomically.",
		Example: `{"harness":"codex","project":"rhumb"}`,
	},
	"agent-directory": {
		Detail:  "List currently live sessions using exact metadata selectors. include_offline includes expired/stopped entries; after is an exclusive ordinal cursor. Metadata is reported context.",
		Example: `{"harness":"codex","project":"rhumb","limit":20}`,
	},
	"wake-attempts": {
		Detail:  "Inspect this profile's wake attempts. active=true selects the unfinished attempt; attempt_id selects one UUID. Paging uses after and limit.",
		Example: `{"active":true}`,
	},
	"worker-register": {
		Detail:  "Host supervisor only: register configured launcher metadata after reconciling old attempts. A new request replaces the supervisor incarnation and preserves account health.",
		Example: `{"request_id":"NEW_UUID","spec":{"name":"worker-01","harness":"codex","workspace":"/workspace","pools":["coding"],"launch_spacing_seconds":3}}`,
	},
	"worker-heartbeat": {
		Detail:  "Refresh supervisor liveness for 90 seconds. Does not clear account unavailability or launch backoff.",
		Example: `{"supervisor_id":"SUPERVISOR_UUID"}`,
	},
	"worker-health": {
		Detail:  "Set this slot's availability with a revision check. Recovery is explicit; retry_at is informational and never clears unavailability.",
		Example: `{"request_id":"NEW_UUID","supervisor_id":"SUPERVISOR_UUID","expected_revision":1,"health":"paused","reason":"operator maintenance"}`,
	},
	"worker-list": {
		Detail:  "Inspect bounded configured launcher metadata or pool admission limits in this collection. Online does not establish provider capacity.",
		Example: `{}`,
	},
	"pool-list": {
		Detail:  "Inspect bounded configured launcher metadata or pool admission limits in this collection. Online does not establish provider capacity.",
		Example: `{}`,
	},
	"wake-claim": {
		Detail:  "Host supervisor only: claim one request and establish a durable hold. The request UUID identifies the attempt. An empty result has no durable effect.",
		Example: `{"request_id":"NEW_UUID"}`,
	},
	"wake-change": {
		Detail:  "Host supervisor only: start, enter, link, session, report or finish an attempt. session links a running attempt to its native {agent_id, execution_id}; it preserves the slot delivery owner. finish requires host confirmation that its entire unit has stopped. Reports do not attest task success.",
		Example: `{"request_id":"NEW_UUID","attempt_id":"ATTEMPT_UUID","operation":"finish","reason":"operator_confirmed_stopped"}`,
	},
	"revise": {
		Detail:  "Replace an active ordinary note's body, preserving its metadata and citations.\n",
		Example: `{"request_id":"NEW_UUID","record_id":"RECORD_UUID","expected_version":1,"repo":"REPOSITORY","body":"Corrected note text"}`,
	},
	"append": {
		Detail:  "Append body verbatim to an active ordinary note. Include paragraph separators\nyourself; existing text, metadata and citations remain. The combined body must\nfit the 65536-byte note limit.\n",
		Example: `{"request_id":"NEW_UUID","record_id":"RECORD_UUID","expected_version":1,"repo":"REPOSITORY","body":"\n\nSelected additional guidance."}`,
	},
	"replace": {
		Detail:  "Replace exactly one occurrence of old_text in an active ordinary note.\nNo match or multiple matches refuse. Supply enough surrounding text to identify\nthe passage. An explicit empty new_text removes it if the note stays nonblank.\nOther text, metadata and citations remain.\n",
		Example: `{"request_id":"NEW_UUID","record_id":"RECORD_UUID","expected_version":1,"repo":"REPOSITORY","old_text":"Earlier guidance","new_text":"Corrected guidance"}`,
	},
	"set-sections": {
		Detail:  "Replace or append Markdown ## sections by heading title in an active ordinary\nnote. Duplicate matching headings or unclosed fenced code blocks refuse; a new\ntitle appends a ## section. Other sections, metadata and citations remain.\n",
		Example: `{"request_id":"NEW_UUID","record_id":"RECORD_UUID","expected_version":1,"repo":"REPOSITORY","sections":{"Summary":"Updated summary text"}}`,
	},
	"edit": {
		Detail:  "Replace a complete draft of an active ordinary note. Unlike the native\ncairn_edit tool, this API operation requires draft; a top-level body is not\naccepted. Use revise, append, replace or set-sections for text-only changes. Start from the\ncurrent draft and preserve its kind, scope, pins, entities and other metadata\nintentionally. Scope and sensitivity changes require separate authority.\n",
		Example: `{"request_id":"NEW_UUID","record_id":"RECORD_UUID","expected_version":1,"draft":{"kind":"note","body":"Corrected note text","scope":{"repo":"REPOSITORY","task_id":"*","run_id":"*"},"claim_type":"self","sensitivity":"shareable"}}`,
	},
	"cite": {
		Detail:  "Replace an active ordinary note's evidence citations; an explicit [] clears\ncurrent citations while retaining earlier versions. Pull and verify selected\nevidence before attaching it. Each citation has evidence_id, expected_sha256 and\noptional spans [{offset,length}]. Text-only edits preserve these citations.\n",
		Example: `{"request_id":"NEW_UUID","record_id":"RECORD_UUID","expected_version":1,"repo":"REPOSITORY","evidence_citations":[{"evidence_id":"EVIDENCE_UUID","expected_sha256":"SOURCE_SHA256"}]}`,
	},
	"history": {
		Detail:  "List newest-first retained version metadata (limit 1..100, default 20).\nFollow next_before_version as before_version. For one exact body, replace limit\nwith a positive version; do not combine version with paging fields. An exact\nversion also accepts span: {offset,length} in bytes. History is not current\nauthority; pull the current note before editing. No request UUID is required.\n",
		Example: `{"record_id":"RECORD_UUID","limit":20}`,
	},
	"handoff-request-status": {
		Detail:  "Read-only check of up to 16 request events you link to one saved handoff.\nSupply the handoff's exact current record_id and version and one item_id/event_id\npair per item; the links are caller assertions, not verified associations. The\nhandoff must be active and readable to this profile. Each linked request is read\nwith your own event permissions: an inaccessible, absent or non-request event is\nreported only as unavailable. A request shows up to 16 delivery states and, for\nits publisher, the response group's state and counts. These describe handling,\nnever the work: task_outcome is always unknown. A stale version refuses with\nVERSION_CONFLICT and an oversized combined result with BUDGET_REFUSED. No request\nUUID is required; nothing is claimed, acknowledged, closed or changed.\n",
		Example: `{"handoff":{"record_id":"RECORD_UUID","version":1},"items":[{"item_id":"review","event_id":"REVIEW_REQUEST_EVENT_UUID"},{"item_id":"deliver","event_id":"DELIVERY_REQUEST_EVENT_UUID"}]}`,
	},
	"assessments": {
		Detail:  "Read retained task-review versions for an owned receipt, oldest first,\nincluding full reasons and evidence IDs. This is task-review history, not note\nhistory. Repository, owner and destination must still match the profile.\nNo request UUID is required.\n",
		Example: `{"receipt_id":"RECEIPT_UUID"}`,
	},
	"assessments-page": {
		Detail:  "Read retained task-review versions for an owned receipt in ascending\nversion order. Start after_version at 0; pass next_after_version from each\npage while more is true. Default limit 100, maximum 1000. Each page repeats\nrepository, owner and destination checks. No request UUID is required.\n",
		Example: `{"receipt_id":"RECEIPT_UUID","after_version":0,"limit":100}`,
	},
	"assess-run": {
		Detail:  "Append a review of an owned receipt. Read assessments first; expected_version\nis 0 for the initial review, otherwise the latest reviewed version. Agent\naccounts remain testimony. Qualitative benefit can be described while outcome\nis unknown; a successful process or tool call does not establish acceptance.\nAny non-unknown outcome requires selected evidence. Exact retries return the\noriginal review even after a later correction.\n",
		Example: `{"request_id":"NEW_UUID","receipt_id":"RECEIPT_UUID","expected_version":0,"task_outcome":"unknown","failure_domain":"unknown","failure_kind":"","method":"qualitative-review/1","evidence_ids":[],"reason":"Describe the task, recalled guidance, observed result, competing explanations and uncertainty."}`,
	},
	"recall-observation": {
		Detail:  "Report one lifecycle recall hook invocation's metrics: status, elapsed and\nsearch/selector/pull milliseconds, selector calls, model, token usage and\nprovider-reported cost, and injected bytes, for the harness named. Omit any value\nthe hook did not observe: omission is unknown, never zero, and a cost is only what\nthe provider reported. Prompt, query, note and model text are not accepted. Named\nreceipts must be this profile's own, in the same repository. The report is\ntestimony; it does not prove delivery or usefulness. It appears in use-report.\n",
		Example: `{"request_id":"NEW_UUID","repo":"REPOSITORY","receipt_ids":["RECEIPT_UUID"],"harness":"claude","hook_event":"UserPromptSubmit","method":"cairn-lifecycle/recall-meter/1","status":"completed","elapsed_ms":1840,"search_ms":210,"selector_ms":1390,"pulls_ms":120,"selector_calls":[{"stage":"select","outcome":"completed","elapsed_ms":1390,"model":"MODEL","model_source":"reported","input_tokens":2100,"output_tokens":40,"cost_usd":0.0121}],"injected_bytes":3300}`,
	},
	"capture-observation": {
		Detail:  "Report one selected-capture invocation under a session SHA-256 reference.\nThe snapshot SHA-256 and provider usage are optional; unknown metrics stay null.\nMethod /2 may report admission_candidate=transport_only as a shadow observation;\nit does not skip selection or authenticate the wake text. This is hook testimony,\nindependent of the capture marker or task acceptance. No dialogue, note body,\nmodel output or credentials are accepted.\n",
		Example: `{"request_id":"NEW_UUID","repo":"REPOSITORY","harness":"claude","hook_event":"PreCompact","method":"cairn-lifecycle/capture-meter/2","session_sha256":"SESSION_SHA256","snapshot_sha256":"SNAPSHOT_SHA256","outcome":"saved","selector_outcome":"completed","duration_ms":1800,"input_tokens":2100,"output_tokens":40}`,
	},
	"capture-observations": {
		Detail:  "Read retained capture invocation history with a local profile.\nRows are ordered by store sequence and may be filtered by session_sha256.\nAn absent row or null metric is unknown, not zero. This is separate from\nthe latest capture marker in the hook's session state.\n",
		Example: `{"repo":"REPOSITORY","limit":100,"offset":0}`,
	},
	"explain-page": {
		Detail:  "Inspect retained candidate references, ranks and omission reasons for an\nowned receipt. No bodies, previews, new handles or credits are returned. Use\nthe original owner and destination profile; current disclosure checks cover\nevery retained candidate on every page. Any inaccessible candidate refuses the\nwhole inspection without partial rows. Start without a cursor; pass\nnext_after_record_id and next_after_version as after_record_id and after_version\nwhile more is true. Omitted/zero limit means 20; maximum 100.\nThis is historical diagnostic metadata, not verified reconstruction, proof of\nrelevance or permission to act. No request UUID or original query is required.\n",
		Example: `{"receipt_id":"RECEIPT_UUID","limit":20}`,
	},
	"recompile": {
		Detail:  "Inspect an owned receipt's retained read set using its original query and\nentities, if any. Use the original owner and destination profile. Later notes\ndo not join the old set, and current privacy and forgetting still apply.\nA saved query digest cannot recover missing query text. This is historical\ninspection, not permission to launch or deliver an old package.\n",
		Example: `{"receipt_id":"RECEIPT_UUID","query":"original query"}`,
	},
}

func formatAgentJSONHelp(operation string, spec agentJSONHelpSpec) commandHelp {
	text := "Usage: cairn agent [--token-file FILE] [--socket PATH] " + operation + " < request.json\n\n" + spec.Detail
	text += "\nReplace example placeholders with actual values. For writes, generate a new\nrequest UUID and use the current expected_version; retry identical JSON after\nan uncertain response. VERSION_CONFLICT requires a fresh read and reconciliation.\nHelp does not read stdin or connect to the API.\n\nExample JSON:\n" + spec.Example + "\n"
	return commandHelp(text)
}

func searchOperationHelp() (commandHelp, error) {
	f, _ := newAgentSearchFlags()
	return agentFlagHelp(
		"cairn agent [--token-file FILE] [--socket PATH] search --task TASK --run RUN [OPTIONS] QUERY\n       cairn agent [--token-file FILE] [--socket PATH] search --task TASK --run RUN --browse [OPTIONS]",
		"Search eligible memory and return a bounded index with pull commands. Task and run identities are required; use --browse without query or entity hints to inspect eligible previews.", f), nil
}

func rememberOperationHelp() (commandHelp, error) {
	f, _ := newRememberFlags()
	return agentFlagHelp(
		"cairn agent [--token-file FILE] [--socket PATH] remember [OPTIONS] NOTE TEXT\n       cairn agent [--token-file FILE] [--socket PATH] remember [OPTIONS] --stdin",
		"Save one selected ordinary note through the authenticated API. Use --stdin for exact multiline text; pass -- before note text that begins with a hyphen.", f), nil
}

func clientsOperationHelp() (commandHelp, error) {
	f := flags("clients")
	f.Int("limit", 0, "maximum cohorts to return, 1 to 100 (default 50)")
	return agentFlagHelp("cairn agent [--token-file FILE] [--socket PATH] clients [--limit N]", clientsHelpDetail, f), nil
}

func pullOperationHelp(operation string) commandHelp {
	f, _ := newAgentPullFlags(operation)
	if operation == "pull-evidence" {
		return agentFlagHelp(
			"cairn agent [--token-file FILE] [--socket PATH] pull-evidence [OPTIONS] RECEIPT_UUID HANDLE_UUID EVIDENCE_UUID EXPECTED_SHA256\n       cairn agent [--token-file FILE] [--socket PATH] pull-evidence < request.json",
			"Read one selected supporting source from a search result. Pass the complete pull_arguments as JSON on stdin, or use all four positional identities. --offset and --length select a bounded byte span in positional form.", f)
	}
	return agentFlagHelp(
		"cairn agent [--token-file FILE] [--socket PATH] pull [OPTIONS] RECEIPT_UUID HANDLE_UUID\n       cairn agent [--token-file FILE] [--socket PATH] pull < request.json",
		"Read a selected note from a search result. Pass the complete pull_arguments as JSON on stdin, or use the receipt and handle as positional arguments. --offset and --length select a bounded byte span in positional form. For notes, --opening-bytes adds a bounded same-source prefix.", f)
}

func agentOperationHelp(operation string) (commandHelp, error) {
	sub, ok := lookupAgentSubcommand(operation)
	if !ok || sub.Help == nil {
		return "", invalid("no agent JSON-operation help for this name; use agent --help")
	}
	return sub.Help()
}

func agentFlagHelp(usage, detail string, f *flag.FlagSet) commandHelp {
	var text strings.Builder
	text.WriteString("Usage: ")
	text.WriteString(usage)
	text.WriteString("\n\n")
	text.WriteString(detail)
	text.WriteString("\n\nOptions:\n")
	f.SetOutput(&text)
	f.PrintDefaults()
	text.WriteString("\nHelp does not read stdin, inspect credentials, or connect to the API.\n")
	return commandHelp(text.String())
}

// derivedAgentJSONHelp describes a catalog operation from its declared
// request and response types, body limit, retry class and remote policy when
// no hand-written topic exists. It reads no stdin and opens no connection.
func derivedAgentJSONHelp(spec localapi.OperationSpec) commandHelp {
	var b strings.Builder
	b.WriteString("Usage: cairn agent [--token-file FILE] [--socket PATH] " + spec.Name + " < request.json\n\n")
	b.WriteString("Request: " + jsonTypeLabel(spec.RequestType) + "\n")
	fields := jsonFieldLines(spec.RequestType, map[reflect.Type]bool{})
	if len(fields) == 0 {
		b.WriteString("  (no fields; send {})\n")
	}
	for _, line := range fields {
		b.WriteString("  " + line + "\n")
	}
	b.WriteString("Response: " + jsonTypeLabel(spec.ResponseType) + "\n\n")
	remote := "denied"
	if spec.RemoteAllowed {
		remote = "allowed"
	}
	fmt.Fprintf(&b, "Encoded request limit: %d KiB. Remote machine profiles: %s.\n", localapi.RequestBodyLimit(spec.Name)/1024, remote)
	if spec.RequiresLocalDestination {
		b.WriteString("Requires a local profile: " + spec.LocalDestinationMessage + ".\n")
	}
	b.WriteString("Retry: " + retryClassHint(spec.RetryClass) + "\n")
	b.WriteString("\nThe request schema is docs/api/openapi.json (operationId " + spec.Name + ").\nHelp does not read stdin or connect to the API.\n")
	return commandHelp(b.String())
}

func retryClassHint(class string) string {
	switch class {
	case "read":
		return "read; no durable effect, safe to repeat after any failure."
	case "request-id":
		return "request-id; after an uncertain outcome repeat only with the same request_id and identical arguments."
	case "idempotent":
		return "idempotent; repeating has the same effect as one call."
	case "lease":
		return "lease; keyed by delivery_id and lease_id, a repeat after success is refused with STALE_LEASE."
	case "no-key":
		return "no-key; may have a durable effect with no retry key, inspect state before repeating."
	}
	return class
}

func jsonTypeLabel(t reflect.Type) string {
	if t == nil {
		return "none"
	}
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	if t.Name() != "" {
		return t.String()
	}
	switch t.Kind() {
	case reflect.Struct:
		if t.NumField() == 0 {
			return "empty object"
		}
		return "object"
	case reflect.Slice:
		return "array of " + jsonTypeLabel(t.Elem())
	case reflect.Map:
		return "object"
	}
	return t.Kind().String()
}

// jsonFieldLines lists the encoding/json field names of a request type with
// their Go types, promoting embedded structs the way the decoder does.
func jsonFieldLines(t reflect.Type, seen map[reflect.Type]bool) []string {
	if t == nil {
		return nil
	}
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	if t.Kind() != reflect.Struct || seen[t] {
		return nil
	}
	seen[t] = true
	var lines []string
	for i := 0; i < t.NumField(); i++ {
		field := t.Field(i)
		if !field.IsExported() && !field.Anonymous {
			continue
		}
		tag := field.Tag.Get("json")
		name, opts, _ := strings.Cut(tag, ",")
		if name == "-" {
			continue
		}
		if field.Anonymous && name == "" {
			lines = append(lines, jsonFieldLines(field.Type, seen)...)
			continue
		}
		if name == "" {
			name = field.Name
		}
		line := name + "  " + jsonTypeLabel(field.Type)
		if strings.Contains(opts, "omitempty") || strings.Contains(opts, "omitzero") {
			line += " (optional)"
		}
		lines = append(lines, line)
	}
	return lines
}

func evidenceOperationHelp() (commandHelp, error) {
	spec, ok := localapi.LookupOperation("evidence")
	if !ok {
		return "", invalid("evidence operation is not registered")
	}
	text := "Usage: cairn agent [--token-file FILE] [--socket PATH] evidence --file PATH --source LABEL [--repo REPO] [--shareable] [--request-id UUID]\n" +
		"       cairn agent [--token-file FILE] [--socket PATH] evidence < request.json\n\n" +
		"The file form captures one regular file as supporting evidence and builds the\n" +
		"JSON request for you. The stdin form sends the request below as is.\n\n"
	return commandHelp(text + strings.TrimPrefix(string(derivedAgentJSONHelp(spec)), "Usage: cairn agent [--token-file FILE] [--socket PATH] evidence < request.json\n\n")), nil
}
