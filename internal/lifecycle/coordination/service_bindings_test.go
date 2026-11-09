package coordination

import (
	"context"
	"errors"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// All additional sites use explicit synthetic routes. This is neither a
// production profile nor the deferred isolated-launch qualification.
func stage2ServicesProfile() *value.SourceReaderProfile {
	p := stage2FileProfile()
	codec := &value.Codec{Name: "utf-8", Errors: "strict"}
	for _, site := range []string{SiteConfigRead, SiteDrainStateRead, SiteInboxContextRead, SiteCompactIndexRead, SiteCompactStateRead, SiteCompactContextRead, SiteCompactJournalRead, SiteCompactReplyRead, SiteGrantLedger, SiteGrantMarker, SiteCodexBudget} {
		p.Definition.Readers[site] = value.ReaderRoute{Input: "text-json", Codec: codec, Domain: "synthetic-stage2c"}
	}
	yes, no := true, false
	for _, site := range []string{SiteCLIOutput, SiteRecoveryOutput} {
		p.Definition.Writers[site] = value.WriterRoute{Route: "dumps", Codec: codec, EnsureASCII: &yes, Compact: &no, TrailingLF: &yes, Domain: "synthetic-stage2c"}
	}
	p.Definition.Writers[SiteInboxContextWrite] = p.Definition.Writers[SiteSessionWrite]
	return p
}
func stage2WriteValue(t *testing.T, p *value.SourceReaderProfile, path string, v *value.Value) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	e := p.Write(SiteSessionWrite, f, v)
	closeErr := f.Close()
	if e != nil || closeErr != nil {
		t.Fatal(e, closeErr)
	}
}
func stage2ErrorClass(t *testing.T, err error, want string) {
	t.Helper()
	if err == nil || sourceErrorName(err) != want {
		t.Fatalf("error %v, class %s; want %s", err, sourceErrorName(err), want)
	}
}

func TestStage2ObserveTimeConcrete(t *testing.T) {
	for _, tc := range []struct {
		name          string
		at            *value.Value
		now           float64
		observed, age string
	}{
		{"bool", value.Boolean(true), 3.9, "1970-01-01T00:00:01Z", "2"},
		{"half_even_zero", &value.Value{Kind: value.Float, Number: 0.0000005}, 1, "1970-01-01T00:00:00Z", "0"},
		{"half_even_up", &value.Value{Kind: value.Float, Number: 0.0000015}, 2, "1970-01-01T00:00:00.000002Z", "1"},
		{"negative", &value.Value{Kind: value.Float, Number: -0.25}, 1, "1969-12-31T23:59:59.750000Z", "1"},
		{"negative_half", &value.Value{Kind: value.Float, Number: -0.0000015}, 0, "1969-12-31T23:59:59.999998Z", "0"},
		{"second_carry", &value.Value{Kind: value.Float, Number: 59.9999996}, 0, "1970-01-01T00:01:00Z", "0"},
		{"year_one", value.IntegerText("-62135596800"), 0, "0001-01-01T00:00:00Z", "62135596800"},
		{"year_last", value.IntegerText("253402300799"), 0, "9999-12-31T23:59:59Z", "0"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			stamp, age, err := ObserveTime(tc.at, tc.now)
			if err != nil || !value.SameText(stamp, tc.observed) || age == nil || age.Integer != tc.age {
				t.Fatal(stamp, age, err)
			}
		})
	}
	for _, tc := range []struct {
		at    *value.Value
		now   float64
		class string
	}{
		{value.IntegerText("9223372036854775808"), 0, "OverflowError"},
		{value.IntegerText("9223372036854775807"), 0, "OSError"},
		{value.IntegerText("253402300800"), math.Inf(1), "ValueError"},
		{&value.Value{Kind: value.Float, Number: math.NaN()}, 0, "ValueError"},
		{&value.Value{Kind: value.Float, Number: math.Inf(1)}, 0, "OverflowError"},
		{value.IntegerText("0"), math.NaN(), "ValueError"},
		{value.IntegerText("0"), math.Inf(-1), "OverflowError"},
	} {
		_, _, err := ObserveTime(tc.at, tc.now)
		stage2ErrorClass(t, err, tc.class)
	}
	_, age, err := ObserveTime(value.IntegerText("0"), 1e100)
	n, ok := new(big.Int).SetString(age.Integer, 10)
	if err != nil || !ok || n.BitLen() < 300 {
		t.Fatal(age, err)
	}
}

func stage2GrantFixture(t *testing.T) (GrantBindings, *value.Value, *value.Value, string, *value.Value) {
	t.Helper()
	p := stage2ServicesProfile()
	dir := t.TempDir()
	if err := os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	state := stage2State()
	config := nativeMap("state_dir", dir, "harness", "codex")
	path := filepath.Join(dir, stage2Agent+".inbox-recall.json")
	grant := nativeMap("identity", nativeMap("attempt_id", stage2Attempt), "limit_bytes", 100, "output_bytes", 1, "native_allowance_bytes", 2)
	ledger := nativeMap("schema", InboxLedgerSchema, "session", stage2Agent, "grants", nativeMap(strings.Repeat("a", 64), grant))
	stage2WriteValue(t, p, path, ledger)
	stage2WriteValue(t, p, strings.TrimSuffix(path, ".json")+".initialized", nativeMap("schema", InboxInitSchema, "session", stage2Agent))
	return GrantBindings{Profile: p}, config, state, path, ledger
}
func TestStage2GrantValidationConcrete(t *testing.T) {
	for _, tc := range []struct {
		name, reason string
		change       func(*value.Value)
	}{
		{"counter_bool", "shape", func(v *value.Value) { v.Get("grants").Members[0].Value.Set("limit_bytes", value.Boolean(true)) }},
		{"counter_sum", "shape", func(v *value.Value) { v.Get("grants").Members[0].Value.Set("output_bytes", value.IntegerText("100")) }},
		{"required_count_bool", "required_status", func(v *value.Value) {
			v.Set("required", nativeMap("count", true, "pending", false, "selection_sha256", ""))
		}},
		{"required_pending_int", "required_status", func(v *value.Value) { v.Set("required", nativeMap("count", 0, "pending", 0, "selection_sha256", "")) }},
		{"refresh_float", "refresh_counter", func(v *value.Value) { v.Set("required_refresh_bytes", &value.Value{Kind: value.Float, Number: 0}) }},
		{"pending_range", "pending_charge", func(v *value.Value) { v.Set("pending_bytes", value.IntegerText("9501")) }},
		{"active_absent", "active_grant", func(v *value.Value) { v.Set("active", value.Text("missing")) }},
		{"folded_extra", "folded", func(v *value.Value) { v.Set("folded", nativeMap("count", 0, "tokens", stringArray(), "extra", true)) }},
		{"folded_token", "folded", func(v *value.Value) { v.Set("folded", nativeMap("count", 1, "tokens", stringArray("UPPER"))) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g, _, _, path, ledger := stage2GrantFixture(t)
			tc.change(ledger)
			stage2WriteValue(t, g.Profile, path, ledger)
			_, err := g.LoadGrants(path, value.Text(stage2Agent))
			var failure *GrantLedgerError
			if !errors.As(err, &failure) || failure.Reason != tc.reason {
				t.Fatal(err)
			}
		})
	}
	g, _, _, path, ledger := stage2GrantFixture(t)
	grant := ledger.Get("grants").Members[0].Value
	grant.Set("limit_bytes", value.IntegerText(strings.Repeat("9", 100)))
	ledger.Set("pending_bytes", value.IntegerText("9500"))
	ledger.Set("active", value.Text(strings.Repeat("a", 64)))
	ledger.Set("folded", nativeMap("count", 0, "tokens", stringArray(strings.Repeat("a", 16))))
	stage2WriteValue(t, g.Profile, path, ledger)
	if _, err := g.LoadGrants(path, value.Text(stage2Agent)); err != nil {
		t.Fatal(err)
	}
	ledger.Set("active", stringArray())
	stage2WriteValue(t, g.Profile, path, ledger)
	_, err := g.LoadGrants(path, value.Text(stage2Agent))
	stage2ErrorClass(t, err, "TypeError")
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	_, err = g.LoadGrants(path, value.Text(stage2Agent))
	var failure *GrantLedgerError
	if !errors.As(err, &failure) || failure.Reason != "missing" {
		t.Fatal(err)
	}
}
func TestStage2GrantEvidenceConcrete(t *testing.T) {
	ctx := context.Background()
	g, config, state, path, ledger := stage2GrantFixture(t)
	check := func(want, kind string) {
		t.Helper()
		status, k, err := g.GrantEvidence(ctx, config, state, state.Get("inbox_attempt"))
		if err != nil || status != want || nativeText(k) != kind {
			t.Fatal(status, k, err)
		}
	}
	check("present", "inbox_delivery")
	ledger.Get("grants").Set(strings.Repeat("b", 64), nativeMap("identity", nativeMap("native_owner", stage2Turn, "boundary", "ordinary_prompt"), "limit_bytes", 0, "output_bytes", 0, "native_allowance_bytes", 0))
	stage2WriteValue(t, g.Profile, path, ledger)
	check("present", "mixed")
	lock, err := os.OpenFile(filepath.Join(nativeText(config.Get("state_dir")), stage2Agent+".lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Close()
	if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}
	check("busy", "unknown")
	status, err := g.GrantState(ctx, config, state, state.Get("inbox_attempt"))
	if err != nil || status != "unknown" {
		t.Fatal(status, err)
	}
	if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); err != nil {
		t.Fatal(err)
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err = os.Symlink("missing", path); err != nil {
		t.Fatal(err)
	}
	check("unknown", "unknown")
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	g.MemoryStateDir = func(*value.Value) (*value.Value, error) {
		return value.Text(filepath.Join(t.TempDir(), "not-created")), nil
	}
	status, k, err := g.Evidence(ctx, config, state, nil, value.Text(stage2Turn), false)
	if err != nil || status != "absent" || k != nil {
		t.Fatal(status, k, err)
	}
	g.MemoryStateDir = nil
	_, _, err = g.Evidence(ctx, config, state, value.Text("truthy-nonobject"), nil, true)
	stage2ErrorClass(t, err, "AttributeError")
	stage2WriteValue(t, g.Profile, path, ledger)
	loaderCalled := false
	g.Loader = func(string, *value.Value) (*value.Value, error) { loaderCalled = true; return value.Mapping(), nil }
	check("unknown", "unknown")
	if !loaderCalled {
		t.Fatal("external loader was not reached")
	}
	missingProfile := GrantBindings{}
	_, _, err = missingProfile.GrantEvidence(ctx, config, state, nil)
	var unavailable *value.ProfileUnavailable
	if !errors.As(err, &unavailable) {
		t.Fatal(err)
	}
}
func TestStage2PreGrantConcrete(t *testing.T) {
	p := stage2ServicesProfile()
	dir := t.TempDir()
	if err := os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	g := GrantBindings{Profile: p}
	config := nativeMap("state_dir", dir, "harness", "codex")
	state := stage2State()
	attempt := state.Get("inbox_attempt")
	recorded := nativeMap("recall_phase", "required_search", "grant", "not_checked")
	budget := nativeMap("schema", CodexLedgerSchema, "session_id", stage2Agent, "inbox_activation", nativeMap(stage2Turn, true), "turns", value.Mapping())
	path := filepath.Join(dir, stage2Agent+".memory-budget.json")
	check := func(want bool) {
		t.Helper()
		ok, err := g.PreGrantLedgerAbsent(context.Background(), config, state, attempt, recorded)
		if err != nil || ok != want {
			t.Fatal(ok, err)
		}
	}
	stage2WriteValue(t, p, path, budget)
	check(true)
	budget.Get("inbox_activation").Set(stage2Turn, value.IntegerText("1"))
	stage2WriteValue(t, p, path, budget)
	check(false)
	budget.Get("inbox_activation").Set(stage2Turn, value.Boolean(true))
	budget.Get("turns").Set(stage2Turn, value.Mapping())
	stage2WriteValue(t, p, path, budget)
	check(false)
	nativePop(budget.Get("turns"), stage2Turn)
	stage2WriteValue(t, p, path, budget)
	marker := filepath.Join(dir, stage2Agent+".inbox-recall.initialized")
	if err := os.Symlink("absent", marker); err != nil {
		t.Fatal(err)
	}
	check(false)
	if err := os.Remove(marker); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(dir, 0770); err != nil {
		t.Fatal(err)
	}
	check(false)
}

func TestStage2CodexHealthConcrete(t *testing.T) {
	state := stage2State()
	state.Get("agent").Set("metadata", nativeMap("state", "busy"))
	config := nativeMap("harness", "codex")
	calls := 0
	b := CodexHealthBindings{Now: func() time.Time { return time.Unix(10, 250000000) }, Alive: func(*value.Value) (bool, error) { return true, nil }, Endpoint: func(context.Context, *value.Value, *value.Value) (*value.Value, error) {
		return nativeMap("endpoint", "unix:///synthetic", "owner", "process"), nil
	}, Inspector: func(context.Context, *value.Value, *value.Value, *value.Value, *value.Value) (*value.Value, error) {
		calls++
		return nativeMap("status_type", "idle", "updated_at", value.IntegerText(strings.Repeat("9", 40)), "queue_count", nil, "queued_ids", "ab", "queued_present", 1), nil
	}}
	health, err := b.ProbeCodex(context.Background(), config, state, true)
	if err != nil || !value.SameText(health.Get("status_type"), "idle") || !value.SameText(health.Get("task_metadata").Get("freshness"), "stale_busy") || !nativeEqual(health.Get("queued_ids"), stringArray("a", "b")) || health.Get("queued_present").Kind != value.Null || state.Get("native_health") != health {
		t.Fatal(health, err)
	}
	stage2OrderedKeys(t, health, "schema", "session", "native_session_id", "process", "process_liveness", "process_alive", "status", "status_type", "native_readiness", "task_metadata", "updated_at", "queue_count", "queued_ids", "queued_present", "observed_at", "observed_at_epoch", "next_action")
	b.Now = func() time.Time { return time.Unix(11, 0) }
	health.Set("observed_at_epoch", value.Boolean(true))
	cached, err := b.ProbeCodex(context.Background(), config, state, false)
	if err != nil || cached != health || calls != 1 {
		t.Fatal(cached, err, calls)
	}
	b.Inspector = func(context.Context, *value.Value, *value.Value, *value.Value, *value.Value) (*value.Value, error) {
		return nativeMap("status_type", "active", "updated_at", 12, "queue_count", nil, "queued_ids", 4), nil
	}
	health, err = b.ProbeCodex(context.Background(), config, state, true)
	if err != nil || !value.SameText(health.Get("reason"), "TypeError") || !value.SameText(health.Get("status_type"), "unknown") || health.Get("updated_at").Integer != "12" || health.Get("queue_count").Kind != value.Null || len(health.Get("queued_ids").Items) != 0 {
		t.Fatal(health, err)
	}
	b.Endpoint = func(context.Context, *value.Value, *value.Value) (*value.Value, error) {
		return nil, &value.Error{Class: "UnicodeDecodeError"}
	}
	_, err = b.ProbeCodex(context.Background(), config, state, true)
	stage2ErrorClass(t, err, "UnicodeDecodeError")
	b.Endpoint = func(context.Context, *value.Value, *value.Value) (*value.Value, error) { return nil, nil }
	b.Now = func() time.Time { return time.Unix(71, 0) }
	out, err := b.ProbeCodex(context.Background(), config, state, false)
	if err != nil || out != nil {
		t.Fatal(out, err)
	}
	b.Alive = func(*value.Value) (bool, error) { return false, nil }
	health, err = b.ProbeCodex(context.Background(), config, state, true)
	if err != nil || !value.SameText(health.Get("process_liveness"), "dead") {
		t.Fatal(health, err)
	}
	state.Get("agent").Set("metadata", value.Text("bad"))
	_, err = b.ProbeCodex(context.Background(), config, state, true)
	stage2ErrorClass(t, err, "AttributeError")
}
func TestStage2ServiceDefaultsAndEndpoint(t *testing.T) {
	p := stage2ServicesProfile()
	host := NewFileHost(FileHostOptions{Profile: p})
	engine := NewRecoveryEngine(host, nil, RecoveryHooks{}, t.TempDir())
	h := engine.Hooks
	if h.ObserveTime == nil || h.GrantEvidence == nil || h.GrantState == nil || h.PreGrantLedgerAbsent == nil || h.ProbeCodex == nil || NewProcessHooks("").ObserveTime == nil {
		t.Fatal("missing concrete service default")
	}
	root := t.TempDir()
	dir := filepath.Join(root, "900")
	if err := os.MkdirAll(dir, 0700); err != nil {
		t.Fatal(err)
	}
	b := CodexHealthBindings{ProcRoot: root}
	for _, tc := range []struct{ argv, owner string }{{"codex\x00app-server\x00--listen\x00unix:///private\x00", "process"}, {"codex\x00--remote\x00unix:///private\x00", "uid"}} {
		if err := os.WriteFile(filepath.Join(dir, "cmdline"), []byte(tc.argv), 0600); err != nil {
			t.Fatal(err)
		}
		endpoint, err := b.endpoint(context.Background(), nativeMap("harness", "codex"), nativeMap("pid", 900))
		if err != nil || !value.SameText(endpoint.Get("owner"), tc.owner) {
			t.Fatal(endpoint, err)
		}
	}
	if err := os.WriteFile(filepath.Join(dir, "cmdline"), []byte{0xff}, 0600); err != nil {
		t.Fatal(err)
	}
	_, err := b.endpoint(context.Background(), nativeMap("harness", "codex"), nativeMap("pid", 900))
	stage2ErrorClass(t, err, "UnicodeDecodeError")
}
