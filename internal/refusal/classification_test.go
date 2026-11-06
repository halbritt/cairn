package refusal

import "testing"

// Typed admission details belong to exactly the admission and closure-capacity
// refusals; every other code, including unreconciled closures, keeps its
// existing envelope.
func TestAdmissionDetailCodesAreExact(t *testing.T) {
	for code, want := range map[string]bool{
		"EVENT_ADMISSION_REFUSED": true, "CLOSURE_RESERVE_FULL": true,
		"CLOSURE_UNRECONCILED": false, "RATE_UNRECONCILED": false, "INVALID_REQUEST": false, "STORE_ERROR": false,
		"QUOTA_EXHAUSTED": false, "": false, "event_admission_refused": false,
	} {
		if got := CarriesAdmissionDetails(code); got != want {
			t.Fatalf("%q carries details %v, want %v", code, got, want)
		}
	}
	for code, want := range map[string]Classification{
		"EVENT_ADMISSION_REFUSED": {HTTPStatus: 429, ExitCode: 4},
		"CLOSURE_RESERVE_FULL":    {HTTPStatus: 429, ExitCode: 4},
		"CLOSURE_UNRECONCILED":    {HTTPStatus: 409, ExitCode: 4},
		"RATE_UNRECONCILED":       {HTTPStatus: 409, ExitCode: 4},
	} {
		if got := Classify(code); got != want {
			t.Fatalf("%s classified %+v, want %+v", code, got, want)
		}
	}
}
