package transcript

import (
	"bytes"
	"errors"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func requireProfileUnavailable(t *testing.T, e error) {
	t.Helper()
	var unavailable *value.ProfileUnavailable
	if !errors.As(e, &unavailable) {
		t.Fatalf("want profile error, got %v", e)
	}
}
func TestByteReaderBOMSurrogatepassAndTextSeparation(t *testing.T) {
	profile := fixtureProfile(t)
	surrogate := []byte("{\"x\":\"\xed\xa0\x80\"}")
	bom := append([]byte{0xef, 0xbb, 0xbf}, []byte(`{"x":"\ud800"}`)...)
	for _, raw := range [][]byte{surrogate, bom} {
		v, e := ParsePlainBytesJSONObject(raw, profile)
		if e != nil || field(v, "x").String[0] != 0xd800 {
			t.Fatal(v, e)
		}
		if _, e = StrictNativeLine(raw, profile); e == nil {
			t.Fatal("strict text accepted bytes-only input")
		}
		if _, e = ParsePlainJSONObject(raw, profile); e == nil {
			t.Fatal("discovery text accepted bytes-only input")
		}
		p := filepath.Join(t.TempDir(), "record")
		mustWrite(t, p, append(append([]byte(nil), raw...), '\n'))
		last, e := LastJSONLRecord(p, profile)
		if e != nil || field(last, "x").String[0] != 0xd800 {
			t.Fatal(last, e)
		}
	}
	cfg, state, p := claudeFixture(t)
	raw := append([]byte{0xef, 0xbb, 0xbf}, []byte(`{"type":"user","timestamp":"`+baseStamp+`","x":"`)...)
	raw = append(raw, 0xed, 0xa0, 0x80)
	raw = append(raw, []byte("\"}\n{\"type\":\"last-prompt\",\"value\":NaN}\n")...)
	mustWrite(t, p, raw)
	evidence, e := DaemonStartClaimEvidence(cfg, state, Attempt{CreatedAt: stamp(2)}, profile)
	if e != nil || evidence["records_after"] != 1 {
		t.Fatal(evidence, e)
	}
}

func TestNonfiniteAndSurrogateDiscoveryUniqueness(t *testing.T) {
	for _, literal := range []string{`NaN`, `Infinity`, `-Infinity`, `"\ud800"`} {
		for _, gap := range []string{"", "{}\n"} {
			t.Run(literal+gap, func(t *testing.T) {
				f := codexFixture(t)
				writeRecords(t, f.path, codexRefusalRecords(t))
				base, e := os.ReadFile(f.path)
				if e != nil {
					t.Fatal(e)
				}
				extra := []byte(`{"payload":{"turn_id":"` + turnID + `"},"extra":` + literal + "}\n")
				mustWrite(t, f.path, append(append(append([]byte(nil), base...), []byte(gap)...), extra...))
				got, e := f.reader.FindCodexRefusalLocators(f.cfg, f.state, turnID)
				if e != nil || got != nil {
					t.Fatal("matching extra record was skipped", got, e)
				}
			})
		}
	}
	// Raw surrogate bytes and a BOM are not accepted by the text/plain scan.
	for _, extra := range [][]byte{[]byte("{\"payload\":{\"turn_id\":\"" + turnID + "\"},\"extra\":\"\xed\xa0\x80\"}\n"), append([]byte{0xef, 0xbb, 0xbf}, []byte(`{"payload":{"turn_id":"`+turnID+`"}}`+"\n")...)} {
		f := codexFixture(t)
		ls := writeRecords(t, f.path, codexRefusalRecords(t))
		base, e := os.ReadFile(f.path)
		if e != nil {
			t.Fatal(e)
		}
		mustWrite(t, f.path, append(base, extra...))
		got, e := f.reader.FindCodexRefusalLocators(f.cfg, f.state, turnID)
		if e != nil || len(got) != 3 || got[0] != ls[0] {
			t.Fatal(got, e)
		}
	}
}
func paddedMatch(n int) []byte {
	base := []byte(`{"payload":{"turn_id":"` + turnID + `"},"pad":""}` + "\n")
	index := bytes.LastIndex(base, []byte(`"}`))
	return append(append(append([]byte(nil), base[:index]...), bytes.Repeat([]byte{'x'}, n-len(base))...), base[index:]...)
}
func TestDiscoveryExactLineBoundary(t *testing.T) {
	f := codexFixture(t)
	rs := codexRefusalRecords(t)
	tail := append(mustJSON(t, rs[1]), mustJSON(t, rs[2])...)
	big := paddedMatch(NativeRecordLimit)
	mustWrite(t, f.path, append(big, tail...))
	got, e := f.reader.FindCodexRefusalLocators(f.cfg, f.state, turnID)
	if e != nil || len(got) != 3 || got[0].Length != NativeRecordLimit {
		t.Fatal(got, e)
	}
	ls := writeRecords(t, f.path, rs)
	base, e := os.ReadFile(f.path)
	if e != nil {
		t.Fatal(e)
	}
	big = paddedMatch(NativeRecordLimit + 1)
	mustWrite(t, f.path, append(big, base...))
	got, e = f.reader.FindCodexRefusalLocators(f.cfg, f.state, turnID)
	if e != nil || len(got) != 3 || got[0].Offset != NativeRecordLimit+1 || got[0].Length != ls[0].Length {
		t.Fatal("oversized physical line not skipped", got, e)
	}
}
func TestLatestEndNonfiniteAndSurrogateStartBarriers(t *testing.T) {
	for _, literal := range []string{`NaN`, `Infinity`, `"\ud800"`} {
		f := codexFixture(t)
		writeRecords(t, f.path, codexRefusalRecords(t))
		raw, e := os.ReadFile(f.path)
		if e != nil {
			t.Fatal(e)
		}
		raw = append(raw, []byte(`{"type":"event_msg","payload":{"type":"task_started"},"extra":`+literal+"}\n")...)
		mustWrite(t, f.path, raw)
		got, e := f.reader.LatestCodexTurnEnd(f.cfg, f.state)
		if e != nil || got != nil {
			t.Fatal("start barrier was skipped", got, e)
		}
	}
}

func TestQueuePythonNumericWhitespaceAndMarkerSemantics(t *testing.T) {
	f := codexFixture(t)
	records := codexRefusalRecords(t)
	ls := writeRecords(t, f.path, records[:2])
	w := Wake{AttemptedAt: math.Inf(-1)}
	for _, attempted := range []any{math.Inf(-1), true, false} {
		w.AttemptedAt = attempted
		p, e := f.reader.CodexQueueRolloutProof(f.cfg, f.state, w, ls)
		if e != nil || p["outcome"] != "admitted_active" {
			t.Fatal("isinstance/comparison parity", attempted, p, e)
		}
	}
	for _, attempted := range []any{math.NaN(), math.Inf(1)} {
		w.AttemptedAt = attempted
		if _, e := f.reader.CodexQueueRolloutProof(f.cfg, f.state, w, ls); e == nil {
			t.Fatal("nonfinite comparison accepted", attempted)
		}
	}
	w.AttemptedAt = epochStamp(t, baseStamp)
	fixtureObject(records[2]["payload"])["last_agent_message"] = "\x1c\x1d"
	ls = writeRecords(t, f.path, records)
	if _, e := f.reader.CodexQueueRolloutProof(f.cfg, f.state, w, ls); e == nil {
		t.Fatal("Python whitespace treated as completed")
	}
	records = codexRefusalRecords(t)
	fixtureObject(records[0]["payload"])["turn_attribution"] = map[string]any{"turn_trigger": "owner"}
	ls = writeRecords(t, f.path, records)
	w.DeliveryID = `"turn_trigger":"owner"`
	p, e := f.reader.CodexQueueRolloutProof(f.cfg, f.state, w, ls)
	if e != nil || p["outcome"] != "never_admitted" {
		t.Fatal("extra compact marker search narrowed Source", p, e)
	}
	w.DeliveryID = `"turn_trigger": "owner"`
	if _, e = f.reader.CodexQueueRolloutProof(f.cfg, f.state, w, ls); e == nil {
		t.Fatal("missed Python-spaced marker")
	}
	for _, marker := range []string{`"x": "\ud800"`, `"nan": NaN`, `"positive": Infinity`, `"negative": -Infinity`} {
		if !queueMarkerFixture(t, [][]byte{[]byte(`{"x":"\ud800","nan":NaN,"positive":Infinity,"negative":-Infinity}`)}, Wake{DeliveryID: marker}) {
			t.Fatal("lossless writer missed", marker)
		}
	}
}

func TestProfileFailuresEscapeAtReachedSourceSites(t *testing.T) {
	profile := fixtureProfile(t)
	p := filepath.Join(t.TempDir(), "record")
	mustWrite(t, p, []byte("{}\n"))
	_, e := StrictNativeLine([]byte(`{}`), nil)
	requireProfileUnavailable(t, e)
	_, e = LastJSONLRecord(p, nil)
	requireProfileUnavailable(t, e)
	// Earlier Source file/locator winners retain priority over profile checks.
	got, e := LastJSONLRecord(filepath.Join(t.TempDir(), "missing"), nil)
	if got != nil || e != nil {
		t.Fatal(got, e)
	}
	_, e = ReadNativeRecord(p, Locator{}, nil)
	requireError(t, e, "native record locator must be OFFSET:LENGTH:SHA256 within bounds")
	cfg, state, path := claudeFixture(t)
	mustWrite(t, path, []byte(`{"timestamp":"`+baseStamp+`"}`+"\n"))
	_, e = DaemonStartClaimEvidence(cfg, state, Attempt{CreatedAt: stamp(2)}, nil)
	requireProfileUnavailable(t, e)
	f := codexFixture(t)
	rs := codexRefusalRecords(t)
	writeRecords(t, f.path, rs)
	f.reader.Profile = nil
	_, e = f.reader.FindCodexRefusalLocators(f.cfg, f.state, turnID)
	requireProfileUnavailable(t, e)
	_, e = f.reader.LatestCodexTurnEnd(f.cfg, f.state)
	requireProfileUnavailable(t, e)
	// Strict Native Source does not catch RecursionError; discovery does.
	profile.Routes = fixtureRoutes{failSite: SiteStrictNativeLine, failClass: "RecursionError"}
	f.reader.Profile = profile
	_, e = f.reader.LatestCodexTurnEnd(f.cfg, f.state)
	if value.ErrorClass(e) != "RecursionError" {
		t.Fatal(e)
	}
	profile.Routes = fixtureRoutes{failSite: SiteFindCodexRefusal, failClass: "RecursionError"}
	ls, e := f.reader.FindCodexRefusalLocators(f.cfg, f.state, turnID)
	if e != nil || ls != nil {
		t.Fatal(ls, e)
	}
	// Route failure is not a malformed line or an unsupported queue outcome.
	profile = fixtureProfile(t)
	profile.Routes = fixtureRoutes{failSite: SiteFindCodexRefusal}
	f.reader.Profile = profile
	_, e = f.reader.FindCodexRefusalLocators(f.cfg, f.state, turnID)
	requireProfileUnavailable(t, e)
	profile = fixtureProfile(t)
	profile.Routes = fixtureRoutes{failWriter: true}
	f.reader.Profile = profile
	fixtureObject(rs[0]["payload"])["turn_attribution"] = map[string]any{"turn_trigger": "owner"}
	pins := writeRecords(t, f.path, rs)
	_, e = f.reader.CodexQueueRolloutProof(f.cfg, f.state, Wake{AttemptedAt: epochStamp(t, baseStamp)}, pins)
	requireProfileUnavailable(t, e)
}
func TestEndedTurnSourceCheckPriority(t *testing.T) {
	f := codexFixture(t)
	rs := endedRecords(t)
	ls := writeRecords(t, f.path, rs)
	bad := []Locator{ls[0], {Offset: CodexEndedTurnRegionLimit + 10, Length: 3, SHA256: strings.Repeat("0", 64)}}
	bad[0].SHA256 = strings.Repeat("0", 64)
	_, e := f.reader.CodexEndedTurnProof(f.cfg, f.state, Attempt{CreatedAt: stamp(2), NativeTurnID: turnID}, bad)
	requireError(t, e, "native record bytes differ from their pin or are not one record")
	rs[1]["type"] = "tool_call"
	rs[2]["ordinal"] = 999
	ls = writeRecords(t, f.path, rs)
	_, e = f.reader.CodexEndedTurnProof(f.cfg, f.state, Attempt{CreatedAt: stamp(2), NativeTurnID: turnID}, endPins(ls))
	requireError(t, e, "Codex turn shows tool, assistant or unknown activity")
}
func TestClaudeCommandPythonWhitespace(t *testing.T) {
	cfg, state, path := claudeFixture(t)
	cfg.ConfigPath += "\x1c"
	attempt := Attempt{CreatedAt: stamp(2), Delivery: Delivery{DeliveryID: turnID, State: "leased", Attempts: 1}}
	ls := writeRecords(t, path, claudeRefusalRecords(state, cfg, attempt))
	_, e := NativeRefusalProof(cfg, state, attempt, ls, fixtureProfile(t))
	requireError(t, e, "native refusal record is not this binding's post-claim refusal of this wake")
}
func TestWorkerOtherDaemonAndWrongResumeIgnored(t *testing.T) {
	for _, otherDaemon := range []bool{true, false} {
		cfg, state, p := claudeFixture(t)
		root := t.TempDir()
		state.Process = writeProc(t, root, 400, 2, 100, "daemon", "/opt/claude/claude", []string{"claude", "daemon"})
		writeProc(t, root, 401, 400, 101, "bg-pty-host", "/opt/claude/claude", []string{"claude", "bg-pty-host"})
		parent := 401
		resume := p
		if otherDaemon {
			writeProc(t, root, 500, 2, 200, "daemon", "/opt/claude/claude", []string{"claude", "daemon"})
			writeProc(t, root, 501, 500, 201, "bg-pty-host", "/opt/claude/claude", []string{"claude", "bg-pty-host"})
			parent = 501
		} else {
			resume = p + ".other"
		}
		writeProc(t, root, 402, parent, 202, "claude", "/opt/claude/claude", []string{"claude", "--resume", resume})
		_, e := FindDaemonWorkerAt(root, cfg, state, uint32(os.Getuid()))
		requireError(t, e, "expected exactly one daemon worker for this conversation, found 0")
	}
}
