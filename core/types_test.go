package core

import "testing"

func TestDraftValidation(t *testing.T) {
	for _, scenario := range []string{"instruction", "empty", "wildcard", "unknown-claim", "bad-attempt", "self-proxy", "long-body"} {
		t.Run(scenario, func(t *testing.T) {
			draft := note()
			switch scenario {
			case "instruction":
				draft.Kind = "instruction"
			case "empty":
				draft.Scope.Repo = ""
			case "wildcard":
				draft.Scope.Repo = "*"
			case "unknown-claim":
				draft.ClaimType = "instrumented"
			case "bad-attempt":
				draft.ClaimType = "completion"
				draft.AttributedProducer = "someone"
				draft.AttemptID = "invalid"
			case "self-proxy":
				draft.AttributedProducer = "someone"
			case "long-body":
				draft.Body = string(make([]byte, 65537))
			}
			requireCode(t, draft.validate(), "INVALID_REQUEST")
		})
	}
}
