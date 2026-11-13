package ledger

import (
	"bytes"
	"context"
	"crypto/sha256"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestR06GrantDispositionPositiveActivationStaysStickyAfterDisable(t *testing.T) {
	for _, harness := range []string{"codex", "claude"} {
		t.Run(harness, func(t *testing.T) {
			directory := t.TempDir()
			memory := value.Mapping(value.Field("state_dir", value.Text(directory)), value.Field("context_bytes", value.IntegerText("9500")))
			owner := Activation{Memory: memory, Harness: harness, Session: "11111111-1111-4111-8111-111111111111", Turn: "22222222-2222-4222-8222-222222222222", Enabled: true}
			if harness == "claude" {
				owner.Turn = "claude-channel:owning-prompt"
			}
			enabled, err := DecideActivation(context.Background(), owner)
			if err != nil || !enabled {
				t.Fatalf("positive owner activation = %v, %v", enabled, err)
			}
			suffix := ".memory-budget"
			if harness == "claude" {
				suffix = ".inbox-recall"
			}
			ledgerPath := filepath.Join(directory, owner.Session+suffix+".json")
			markerPath := filepath.Join(directory, owner.Session+suffix+".initialized")
			before, err := os.ReadFile(ledgerPath)
			if err != nil {
				t.Fatal(err)
			}
			marker, err := os.ReadFile(markerPath)
			if err != nil {
				t.Fatal(err)
			}

			independentDir := t.TempDir()
			independent := owner
			independent.Memory = value.Mapping(value.Field("state_dir", value.Text(independentDir)), value.Field("context_bytes", value.IntegerText("9500")))
			independent.Session = "33333333-3333-4333-8333-333333333333"
			if enabled, err := DecideActivation(context.Background(), independent); err != nil || !enabled {
				t.Fatalf("independent positive activation = %v, %v", enabled, err)
			}
			independentPath := filepath.Join(independentDir, independent.Session+suffix+".json")
			independentBefore, err := os.ReadFile(independentPath)
			if err != nil {
				t.Fatal(err)
			}

			owner.Enabled = false
			enabled, err = DecideActivation(context.Background(), owner)
			if err != nil || !enabled {
				t.Fatalf("disabled selection lost retained positive decision: %v, %v", enabled, err)
			}
			after, err := os.ReadFile(ledgerPath)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(before, after) {
				t.Fatalf("disabled selection changed owning ledger: before=%s after=%s", before, after)
			}
			markerAfter, err := os.ReadFile(markerPath)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(marker, markerAfter) {
				t.Fatal("disabled selection changed initialized marker")
			}
			independentAfter, err := os.ReadFile(independentPath)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(independentBefore, independentAfter) {
				t.Fatal("owner operation changed independent positive unit")
			}
		})
	}
}

func TestR06GrantDispositionOrdinaryCodexSaveKeepsRetiredPositive(t *testing.T) {
	directory := t.TempDir()
	memory := value.Mapping(value.Field("state_dir", value.Text(directory)), value.Field("context_bytes", value.IntegerText("9500")))
	session := "11111111-1111-4111-8111-111111111111"
	first := "22222222-2222-4222-8222-222222222222"
	second := "33333333-3333-4333-8333-333333333333"
	owner := Activation{Memory: memory, Harness: "codex", Session: session, Turn: first, Enabled: true}
	if enabled, err := DecideActivation(context.Background(), owner); err != nil || !enabled {
		t.Fatalf("positive activation = %v, %v", enabled, err)
	}
	path := filepath.Join(directory, session+".memory-budget.json")
	marker := r06ReadGrantBytes(t, filepath.Join(directory, session+".memory-budget.initialized"))
	event := func(turn string) *value.Value {
		return value.Mapping(value.Field("hook_event_name", value.Text("UserPromptSubmit")), value.Field("turn_id", value.Text(turn)))
	}
	for i, turn := range []string{first, second} {
		if i == 1 {
			disabled := owner
			disabled.Turn, disabled.Enabled = second, false
			if enabled, err := DecideActivation(context.Background(), disabled); err != nil || enabled {
				t.Fatalf("new disabled turn = %v, %v", enabled, err)
			}
		}
		state, err := CodexBudget(path, session, 9500)
		if err != nil {
			t.Fatal(err)
		}
		grant, eligible, reason := CodexPromptGrant(state, event(turn), 9500, true)
		if !eligible || reason != "eligible" {
			t.Fatalf("ordinary owner grant = %v, %q", eligible, reason)
		}
		AddCodexCounter(grant, "emitted_hook_bytes", []int{37, 11}[i])
		if err := SaveCodexBudget(path, state); err != nil {
			t.Fatal(err)
		}
	}
	state, err := CodexBudget(path, session, 9500)
	if err != nil {
		t.Fatal(err)
	}
	r06GrantKeys(t, state, "schema", "session_id", "active_turn_id", "turns", "pending", "inbox_activation")
	r06GrantKeys(t, state.Get("turns"), first, second)
	r06GrantKeys(t, state.Get("inbox_activation"), first, second)
	if !value.SameText(state.Get("active_turn_id"), second) || state.Get("inbox_activation").Get(first).Kind != value.Bool || !state.Get("inbox_activation").Get(first).Boolean || state.Get("inbox_activation").Get(second).Boolean {
		t.Fatal("ordinary persistence lost owning-turn disposition")
	}
	for i, turn := range []string{first, second} {
		grant := state.Get("turns").Get(turn)
		r06GrantKeys(t, grant, "limit_bytes", "emitted_hook_bytes", "reserved_native_bytes", "granted")
		if grant.Get("limit_bytes").Integer != "9500" || grant.Get("emitted_hook_bytes").Integer != []string{"37", "11"}[i] || grant.Get("reserved_native_bytes").Integer != "0" || grant.Get("granted").Boolean {
			t.Fatalf("turn %s accounting changed: %#v", turn, grant)
		}
	}
	// Cross-read the real nonzero fixture with the Source strict reader/writer.
	// Both paths must retain accounting, membership and activation decisions.
	sourceDir := t.TempDir()
	sourcePath := filepath.Join(sourceDir, filepath.Base(path))
	sourceMarker := filepath.Join(sourceDir, session+".memory-budget.initialized")
	if err := os.WriteFile(sourcePath, r06ReadGrantBytes(t, path), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(sourceMarker, marker, 0600); err != nil {
		t.Fatal(err)
	}
	r06PythonGrantOperation(t, `
directory, session, owner = sys.argv[2:]
ledger = source.GrantLedger(directory, session)
with ledger.locked():
    state = ledger.load_codex(9500)
    assert state['turns'][owner]['emitted_hook_bytes'] == 37
    assert len(state['turns']) == 2
    ledger.save_codex(state)
assert source.activation_decision({'state_dir': directory}, 'codex', session, owner, False) is True
`, sourceDir, session, first)
	before := r06ReadGrantBytes(t, path)
	_, eligible, reason := CodexPromptGrant(state, event(first), 9500, true)
	if eligible || reason != "retired_turn" {
		t.Fatalf("retired owner reopened: %v, %q", eligible, reason)
	}
	if err := SaveCodexBudget(path, state); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(before, r06ReadGrantBytes(t, path)) {
		t.Fatal("retired turn probe changed accounting or disposition")
	}
	owner.Enabled = false
	if enabled, err := DecideActivation(context.Background(), owner); err != nil || !enabled {
		t.Fatalf("ordinary owner save retired positive decision: %v, %v", enabled, err)
	}
	if !bytes.Equal(r06ReadGrantBytes(t, path), r06ReadGrantBytes(t, sourcePath)) {
		t.Fatal("Go/Source ordinary owner persistence differs in retained bytes/accounting/membership")
	}
	if !bytes.Equal(marker, r06ReadGrantBytes(t, sourceMarker)) {
		t.Fatal("Source ordinary owner persistence changed initialized marker")
	}
	if !bytes.Equal(marker, r06ReadGrantBytes(t, filepath.Join(directory, session+".memory-budget.initialized"))) {
		t.Fatal("ordinary owner save changed initialization marker")
	}
}

func TestR06GrantDispositionCompactionKeepsRecentAndOwningPositive(t *testing.T) {
	for _, tc := range []struct {
		name      string
		later     int
		protected bool
	}{
		{"sole_recent_positive", 0, false},
		{"owning_positive_older_than_recent_window", 65, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			directory := t.TempDir()
			memory := value.Mapping(value.Field("state_dir", value.Text(directory)))
			owner := Activation{Memory: memory, Harness: "claude", Session: "11111111-1111-4111-8111-111111111111", Turn: "claude-channel:owning-prompt", Enabled: true}
			if enabled, err := DecideActivation(context.Background(), owner); err != nil || !enabled {
				t.Fatalf("positive owner activation = %v, %v", enabled, err)
			}
			path := filepath.Join(directory, owner.Session+".inbox-recall.json")
			markerPath := filepath.Join(directory, owner.Session+".inbox-recall.initialized")
			marker := r06ReadGrantBytes(t, markerPath)
			for i := 0; i < tc.later; i++ {
				next := owner
				next.Turn, next.Enabled = fmt.Sprintf("claude-channel:later-%02d", i), false
				if enabled, err := DecideActivation(context.Background(), next); err != nil || enabled {
					t.Fatalf("disabled later decision %d = %v, %v", i, enabled, err)
				}
			}
			state, err := InboxGrants(path, owner.Session)
			if err != nil {
				t.Fatal(err)
			}
			turns := map[string]bool{}
			if tc.protected {
				turns[owner.Turn] = true
			}
			if folded := CompactInbox(state, nil, nil, nil, turns); folded != 0 {
				t.Fatalf("empty grant membership folded %d grants", folded)
			}
			r06GrantKeys(t, state, "schema", "session", "grants", "inbox_activation")
			r06GrantKeys(t, state.Get("grants"))
			expected := []string{owner.Turn}
			for i := max(0, tc.later-64); i < tc.later; i++ {
				expected = append(expected, fmt.Sprintf("claude-channel:later-%02d", i))
			}
			r06GrantKeys(t, state.Get("inbox_activation"), expected...)
			for _, key := range expected {
				decision := state.Get("inbox_activation").Get(key)
				if decision.Kind != value.Bool || decision.Boolean != (key == owner.Turn) {
					t.Fatalf("decision %s changed type/value", key)
				}
			}
			if err := SaveInboxValue(path, state); err != nil {
				t.Fatal(err)
			}
			// Source independently creates each activation through its real
			// operation, then compacts with the identical caller-owned turn set.
			sourceDir := t.TempDir()
			r06PythonGrantOperation(t, `
directory, session, owner, later, protected = sys.argv[2:]
memory = {'state_dir': directory}
assert source.activation_decision(memory, 'claude', session, owner, True) is True
for i in range(int(later)):
    assert source.activation_decision(memory, 'claude', session, 'claude-channel:later-%02d' % i, False) is False
ledger = source.GrantLedger(directory, session)
with ledger.locked():
    state = ledger.load_inbox()
    assert ledger.compact_inbox(state, turns={owner} if protected == 'true' else ()) == 0
    ledger.save_inbox(state)
assert source.activation_decision(memory, 'claude', session, owner, False) is True
`, sourceDir, owner.Session, owner.Turn, fmt.Sprint(tc.later), fmt.Sprint(tc.protected))
			if !bytes.Equal(r06ReadGrantBytes(t, path), r06ReadGrantBytes(t, filepath.Join(sourceDir, owner.Session+".inbox-recall.json"))) {
				t.Fatal("Go/Source compaction retained bytes, decision membership or accounting differ")
			}
			if !bytes.Equal(marker, r06ReadGrantBytes(t, filepath.Join(sourceDir, owner.Session+".inbox-recall.initialized"))) {
				t.Fatal("Go/Source initialized marker differs")
			}
			if enabled, err := ResolvedInboxActivation(memory, "claude", owner.Session, owner.Turn); err != nil || !enabled {
				t.Fatalf("compaction lost retained positive: %v, %v", enabled, err)
			}
			owner.Enabled = false
			if enabled, err := DecideActivation(context.Background(), owner); err != nil || !enabled {
				t.Fatalf("disabled manifest retired positive after compaction: %v, %v", enabled, err)
			}
			if !bytes.Equal(marker, r06ReadGrantBytes(t, markerPath)) {
				t.Fatal("compaction changed initialized marker")
			}
		})
	}
}

func r06ReadGrantBytes(t *testing.T, path string) []byte {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func r06GrantKeys(t *testing.T, object *value.Value, expected ...string) {
	t.Helper()
	if object == nil || object.Kind != value.Object || len(object.Members) != len(expected) {
		t.Fatalf("grant key count differs: object=%#v expected=%v", object, expected)
	}
	for _, key := range expected {
		if object.Get(key) == nil {
			t.Fatalf("grant key %q missing", key)
		}
	}
}

func r06PythonGrantOperation(t *testing.T, operation string, args ...string) {
	t.Helper()
	sourcePath, err := filepath.Abs(filepath.Join("..", "..", "..", "integrations", "lifecycle", "grant_ledger.py"))
	if err != nil {
		t.Fatal(err)
	}
	before := r06ReadGrantBytes(t, sourcePath)
	script := `import importlib.util, sys
spec = importlib.util.spec_from_file_location('r06_grant_source', sys.argv[1])
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)
` + operation
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	command := append([]string{"-I", "-S", "-B", "-c", script, sourcePath}, args...)
	output, err := exec.CommandContext(ctx, "python3", command...).CombinedOutput()
	if err != nil {
		t.Fatalf("unmodified Python grant Source failed: %v\n%s", err, output)
	}
	if !bytes.Equal(before, r06ReadGrantBytes(t, sourcePath)) {
		t.Fatal("Python Source changed during comparison")
	}
	t.Logf("unmodified grant_ledger.py sha256=%x; Source operation passed", sha256.Sum256(before))
}
