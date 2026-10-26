package apicontract

import "github.com/halbritt/cairn/localapi"

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

func retryClass(operation string, properties map[string]bool) (string, bool) {
	spec, ok := localapi.LookupOperation(operation)
	if !ok || spec.RetryClass == "" {
		return "", false
	}
	if _, defined := retryClasses[spec.RetryClass]; !defined {
		return "", false
	}
	if (spec.RetryClass == "request-id") != properties["request_id"] {
		return "", false
	}
	return spec.RetryClass, true
}
