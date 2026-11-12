package coordination

import (
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestSharedWatchHistoricalCleanupCannotLoseUnknownsWithoutCancel(t *testing.T) {
	for _, raw := range []string{
		`{"terminal_scan":"unknown_remaining"}`,
		`{"turn_stop_state":"ambiguous"}`,
		`{"capture_state":"lost"}`,
		`{"capture_gap":true}`,
		`{"tools":[{"item_id":"tool","stop_state":"running"}],"terminal_scan":"clear"}`,
		`{"cancel":{"requested_at":"2026-11-12T19:00:00Z"},"terminal_scan":"clear","turn_stop_state":"ended"}`,
	} {
		attempt, err := value.DecodeBytes([]byte(raw), 0)
		if err != nil {
			t.Fatal(err)
		}
		if err := historicalCancellation(attempt); err == nil {
			t.Fatalf("unknown cleanup accepted: %s", raw)
		}
	}
	for _, raw := range []string{
		`{}`,
		`{"capture_state":"complete","terminal_scan":"clear","turn_stop_state":"ended","tools":[{"item_id":"tool","stop_state":"terminated"}]}`,
		`{"cancel":{"confirmed_at":"2026-11-12T19:00:00Z"},"terminal_scan":"clear","turn_stop_state":"interrupted","tools":[]}`,
	} {
		attempt, err := value.DecodeBytes([]byte(raw), 0)
		if err != nil {
			t.Fatal(err)
		}
		if err := historicalCancellation(attempt); err != nil {
			t.Fatalf("positive terminal cleanup refused: %s: %v", raw, err)
		}
	}
}
