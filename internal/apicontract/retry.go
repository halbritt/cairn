package apicontract

// retryClasses defines the x-cairn-retry values. No class permits automatic
// replay of an uncertain write by the relay: UPSTREAM_UNCERTAIN always goes
// back to the caller, which applies its operation's class.
var retryClasses = map[string]string{
	"read":       "No durable effect. Safe to repeat after any failure.",
	"request-id": "Durable effect keyed by request_id. After an uncertain outcome, repeat only with the same request_id and byte-identical arguments; a committed result is returned again. A different body under the same request_id is refused with IDEMPOTENCY_CONFLICT. Never mint a new request_id to force progress.",
	"idempotent": "Repeating has the same effect as one call (presence refresh, leave, lease extension, current-state host observation). Safe to repeat with the same arguments; a repeated observation does not refresh its freshness.",
	"lease":      "Keyed by delivery_id and lease_id. A repeat after an earlier success is refused with STALE_LEASE, which does not by itself prove the first call failed: inspect event status.",
	"no-key":     "May have a durable effect and has no retry key. After an uncertain outcome do not repeat automatically; inspect state first.",
}

// retryByOperation classifies operations whose request has no request_id.
// Build fails when an operation is missing, so a new route needs a decision.
var retryByOperation = map[string]string{
	"agent-directory": "read", "agent-resolve": "read",
	"assessments": "read", "assessments-page": "read",
	"conflict": "read", "conflicts": "read",
	"event-group": "read", "event-groups": "read", "event-inspect": "read",
	"event-list": "read", "event-metrics": "read", "event-subscriptions": "read", "event-watch": "read",
	"evidence-impact": "read", "get": "read", "handoff-request-status": "read", "history": "read",
	"pool-list": "read", "worker-list": "read", "wake-attempts": "read", "wake-control": "read",
	"preview-retract": "read", "supersession": "read", "refusal": "read",
	"recompile": "read", "explain-page": "read", "run-index": "read", "run-package": "read",
	"run-report": "read", "run-status": "read", "use-report": "read",
	"session-inbox-control": "read", "session-inbox-pending": "read", "session-inbox-ready": "read",
	"version": "read", "clients": "read",

	"agent-heartbeat": "idempotent", "agent-leave": "idempotent", "worker-heartbeat": "idempotent",
	"session-delivery-observe": "idempotent",

	"event-renew": "lease", "event-retry": "lease",

	// event-next claims a new delivery; a repeat may lease a second one while
	// the first stays leased. claim-run: a lost commit reply cannot authorize a
	// second execution (core.ClaimRun).
	"event-next": "no-key", "claim-run": "no-key",
}

func retryClass(operation string, properties map[string]bool) (string, bool) {
	if class, ok := retryByOperation[operation]; ok {
		return class, true
	}
	if properties["request_id"] {
		return "request-id", true
	}
	return "", false
}
