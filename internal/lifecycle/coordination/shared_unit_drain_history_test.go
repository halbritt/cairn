package coordination

import (
	"context"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestWatchHistoricalPolicyOnlyEmptyPoll(t *testing.T) {
	ref := nativeMap("agent_id", uuid.NewString(), "execution_id", uuid.NewString())
	claim := uuid.NewString()
	state := nativeMap("inbox_result_policy", nativeMap("claim_request_id", claim, "version", 1))
	before := nativeClone(state)
	profile := stage2ServicesProfile()
	cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
	reads := 0
	cli.Host.Call = func(_ context.Context, _ *value.Value, op string, body *value.Value, _ NativeCallOptions) (*value.Value, error) {
		if op != "session-inbox-poll-status" || !nativeEqual(body.Get("session"), ref) || !value.SameText(body.Get("request_id"), claim) {
			t.Fatalf("wrong empty poll query: %s %+v", op, body)
		}
		reads++
		return nativeMap("request_id", claim, "session", ref, "attempt_id", nil), nil
	}
	r := &watchDrainRead{cli: cli, facts: map[string]string{}}
	err := r.historicalAttempt(context.Background(), watchDrainSession{config: value.Mapping(), ref: ref, state: state})
	if err != nil || reads != 1 || r.settled[state] == nil || len(r.facts) != 1 {
		t.Fatalf("authenticated empty poll not observed: reads=%d facts=%v err=%v", reads, r.facts, err)
	}
	if !nativeEqual(before, state) {
		t.Fatal("empty poll inspection mutated retained policy")
	}
}

func TestWatchHistoricalPolicyOnlyEmptyPollRefusals(t *testing.T) {
	for _, problem := range []string{"missing-policy", "version", "float-version", "nonstring-id", "empty-id", "extra-policy", "malformed-policy", "present-malformed-attempt", "foreign-agent", "wrong-poll", "missing-attempt-field", "wrong-attempt", "api-unavailable", "auth", "stale", "unknown-id"} {
		t.Run(problem, func(t *testing.T) {
			ref := nativeMap("agent_id", uuid.NewString(), "execution_id", uuid.NewString())
			claim := uuid.NewString()
			policy := nativeMap("claim_request_id", claim, "version", 1)
			state := nativeMap("inbox_result_policy", policy)
			poll := nativeMap("request_id", claim, "session", nativeClone(ref), "attempt_id", nil)
			switch problem {
			case "missing-policy":
				nativePop(state, "inbox_result_policy")
				state.Set("inbox_completion", value.Text(claim))
			case "version":
				policy.Set("version", value.IntegerText("2"))
			case "float-version":
				policy.Set("version", &value.Value{Kind: value.Float, Number: 1})
			case "nonstring-id":
				policy.Set("claim_request_id", value.IntegerText("1"))
			case "empty-id":
				policy.Set("claim_request_id", value.Text(""))
			case "extra-policy":
				policy.Set("authorized", value.Boolean(true))
			case "malformed-policy":
				state.Set("inbox_result_policy", value.Text(claim))
			case "present-malformed-attempt":
				state.Set("inbox_attempt", value.Boolean(false))
			case "foreign-agent":
				poll.Get("session").Set("agent_id", value.Text(uuid.NewString()))
			case "wrong-poll":
				poll.Set("request_id", value.Text(uuid.NewString()))
			case "missing-attempt-field":
				nativePop(poll, "attempt_id")
			case "wrong-attempt":
				poll.Set("attempt_id", value.Text(uuid.NewString()))
			}
			before := nativeClone(state)
			profile := stage2ServicesProfile()
			cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
			reads := []string{}
			cli.Host.Call = func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
				reads = append(reads, op)
				switch problem {
				case "api-unavailable":
					return nil, nativeRefusal("API_CONNECTION_FAILED", "unavailable")
				case "auth":
					return nil, nativeRefusal("AUTHORITY_DENIED", "denied")
				case "stale":
					return nil, nativeRefusal("STALE_SESSION", "fenced")
				case "unknown-id":
					return nil, nativeRefusal("NOT_FOUND", "unknown")
				}
				return nativeClone(poll), nil
			}
			r := &watchDrainRead{cli: cli, facts: map[string]string{}}
			err := r.historicalAttempt(context.Background(), watchDrainSession{config: value.Mapping(), ref: ref, state: state})
			if err == nil || r.settled[state] != nil || !nativeEqual(before, state) {
				t.Fatalf("%s admitted/mutated retained state: reads=%v err=%v", problem, reads, err)
			}
			if (problem == "api-unavailable" || problem == "auth" || problem == "stale") && len(reads) != 1 {
				t.Fatalf("unavailable/auth failed poll fell through: %v", reads)
			}
			if problem == "unknown-id" && (len(reads) != 2 || reads[1] != "session-inbox-attempt-status") {
				t.Fatalf("missing poll did not preserve exact attempt lookup: %v", reads)
			}
		})
	}
	for _, field := range AttemptKeys {
		if field == "inbox_result_policy" {
			continue
		}
		t.Run("retained-"+field, func(t *testing.T) {
			ref := nativeMap("agent_id", uuid.NewString(), "execution_id", uuid.NewString())
			claim := uuid.NewString()
			state := nativeMap("inbox_result_policy", nativeMap("claim_request_id", claim, "version", 1), field, false)
			profile := stage2ServicesProfile()
			cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
			cli.Host.Call = func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
				return nativeMap("request_id", claim, "session", ref, "attempt_id", nil), nil
			}
			r := &watchDrainRead{cli: cli, facts: map[string]string{}}
			if err := r.historicalAttempt(context.Background(), watchDrainSession{config: value.Mapping(), ref: ref, state: state}); err == nil || r.settled[state] != nil {
				t.Fatalf("empty poll waived retained %s", field)
			}
		})
	}
}

// Public drain regression with the existing file/journal fixture and explicit
// API doubles. Authenticated poll/attempt reads are also covered by owned PG.
func TestWatchHistoricalPolicyOnlyTerminalAttempt(t *testing.T) {
	for _, problem := range []string{"opened-poll", "no-poll", "grant-positive", "unfinished", "foreign", "intent", "close", "completion", "response", "journal-result", "tool"} {
		t.Run(problem, func(t *testing.T) {
			f := newGrantCurrentnessFixture(t, "codex")
			nativePop(f.state, "inbox_attempt")
			f.state.Set("inbox_result_policy", nativeMap("claim_request_id", stage2Attempt, "version", 1))
			// Isolate historical correspondence from the fixture's optional
			// positive activation grant. Its separate control must still refuse.
			if problem != "grant-positive" {
				if err := os.Remove(f.ledgerPath); err != nil {
					t.Fatal(err)
				}
			}
			want := ""
			switch problem {
			case "grant-positive":
				want = "exact owning turn/settled attempt correspondence is unknown"
			case "unfinished":
				nativePop(f.remote, "finished_at")
				want = "unfinished"
			case "foreign":
				f.remote.Get("session").Set("agent_id", value.Text(uuid.NewString()))
				want = "correspondence"
			case "intent":
				f.state.Get("inbox_intent").Set("request_id", value.Text(uuid.NewString()))
				want = "retained intent"
			case "close":
				f.state.Get("inbox_close").Set("reason", value.Text("other"))
				want = "retained close"
			case "completion":
				f.state.Set("inbox_completion", value.Text(uuid.NewString()))
				want = "completion retry"
			case "response":
				f.state.Set("inbox_response", value.Text(uuid.NewString()))
				want = "response retry"
			case "journal-result":
				f.journal.Get("completion").Set("result", nativeMap("record_id", uuid.NewString(), "version", 1))
				want = "completion result"
			case "tool":
				f.remote.Set("tools", nativeList(nativeMap("stop_state", "running")))
				want = "tool is not terminal"
			}
			f.persist(t)
			original := f.cli.Host.Call
			f.cli.Host.Call = func(ctx context.Context, cfg *value.Value, op string, body *value.Value, opts NativeCallOptions) (*value.Value, error) {
				if op == "session-inbox-poll-status" {
					if problem == "no-poll" {
						return nil, nativeRefusal("NOT_FOUND", "no poll")
					}
					return nativeMap("request_id", stage2Attempt, "session", f.remote.Get("session"), "attempt_id", stage2Attempt), nil
				}
				return original(ctx, cfg, op, body, opts)
			}
			before := censusTestSnapshot(t, f.root)
			receipt, err := f.cli.CaptureWatchDrain(context.Background(), f.selected, []*value.Value{})
			if want == "" {
				if err != nil || receipt == nil {
					t.Fatalf("closed policy-only attempt refused: %v", err)
				}
				if err := receipt.recheck(context.Background()); err != nil {
					t.Fatal(err)
				}
			} else if err == nil || !strings.Contains(err.Error(), want) {
				t.Fatalf("want %q refusal, got %v", want, err)
			}
			assertCurrentnessSnapshot(t, before, censusTestSnapshot(t, f.root))
		})
	}
}

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
