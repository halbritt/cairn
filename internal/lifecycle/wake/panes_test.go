package wake

import (
	"context"
	"errors"
	"maps"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestObservePanes(t *testing.T) {
	for _, harness := range []string{HarnessClaude, HarnessOpenCode} {
		t.Run(harness+"_explicit_session", func(t *testing.T) {
			f, client, request := newHerdrFixture(t, harness)
			session := fixturePaneSession(request)
			f.host["agent_status"] = "working"
			f.host["focused"] = true
			f.host["workspace_id"] = "workspace-1"
			f.host["tab_id"] = "tab-1"
			f.host["terminal_title"] = "\x1b[32mPane title\x1b[0m"
			f.host["terminal_title_stripped"] = "Pane title"
			unrelated := maps.Clone(f.host)
			unrelated["pane_id"] = "other-pane"
			unrelated["agent_session"] = map[string]any{"kind": "id", "value": "other", "agent": harness}
			f.candidates = append(f.candidates, unrelated)

			matches, err := client.ObservePanes(t.Context(), []PaneSession{session}, []string{"HERDR_PANE_ID=watcher"})
			want := []PaneMatch{{
				AgentID: session.AgentID, PaneID: "own-pane", TabID: "tab-1", WorkspaceID: "workspace-1",
				AgentStatus: "working", TerminalTitle: "Pane title", Focused: true,
			}}
			if err != nil || !slices.Equal(matches, want) {
				t.Fatalf("matches = %+v, error = %v, want %+v", matches, err, want)
			}
			assertPaneFixtureCalls(t, f, [][]string{{"agent", "list"}})
		})
	}

	t.Run("codex_rollout_and_foreground", func(t *testing.T) {
		f, client, request := newHerdrFixture(t, HarnessCodex)
		session := fixturePaneSession(request)
		f.host["agent_session"] = nil
		f.host["terminal_title"] = "Fallback title"
		f.host["agent_status"] = "working"
		fds := filepath.Join(client.ProcRoot, strconv.Itoa(request.Process.PID), "fd")
		if err := os.Mkdir(fds, 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink("/tmp/rollout-fixture-"+request.NativeSessionID+".jsonl", filepath.Join(fds, "5")); err != nil {
			t.Fatal(err)
		}
		other := maps.Clone(f.host)
		other["pane_id"] = "other-pane"
		f.candidates = append(f.candidates, other)
		client.Runner = func(ctx context.Context, argv, env []string) ([]byte, []byte, int, error) {
			f.pgid = 742
			if slices.Equal(argv[1:], []string{"pane", "process-info", "--pane", "other-pane"}) {
				f.pgid = 743
			}
			return f.run(ctx, argv, env)
		}

		unopened := session
		unopened.AgentID, unopened.NativeSessionID = "unopened-agent", "unopened-session"
		alsoObserved := session
		alsoObserved.AgentID = "second-cairn-agent"
		matches, err := client.ObservePanes(t.Context(), []PaneSession{unopened, session, alsoObserved}, nil)
		if err != nil || len(matches) != 2 || matches[0].AgentID != session.AgentID || matches[1].AgentID != alsoObserved.AgentID {
			t.Fatalf("matches = %+v, error = %v", matches, err)
		}
		for _, match := range matches {
			if match.PaneID != "own-pane" || match.TerminalTitle != "Fallback title" || match.AgentStatus != "working" {
				t.Fatalf("wrong Codex pane: %+v", match)
			}
		}
		assertPaneFixtureCalls(t, f, [][]string{
			{"agent", "list"},
			{"pane", "process-info", "--pane", "own-pane"},
			{"pane", "process-info", "--pane", "other-pane"},
		})
	})

	t.Run("agy_parent_pane", func(t *testing.T) {
		f, client, request := newHerdrFixture(t, "agy")
		session := fixturePaneSession(request)
		f.host["agent_session"] = map[string]any{"kind": "id", "value": "parent-native", "agent": "agy"}
		f.host["focused"] = true
		parent := map[string]any{
			"agent":   map[string]any{"display_name": "agent-parent", "native_session_id": "parent-native"},
			"process": session.Process,
		}
		if err := os.WriteFile(filepath.Join(session.StateDir, "parent.json"), wakeJSON(t, parent), 0600); err != nil {
			t.Fatal(err)
		}
		matches, err := client.ObservePanes(t.Context(), []PaneSession{session}, nil)
		if err != nil || !slices.Equal(matches, []PaneMatch{{
			AgentID: session.AgentID, PaneID: "own-pane", AgentStatus: "idle", Focused: true,
		}}) {
			t.Fatalf("matches = %+v, error = %v", matches, err)
		}
		assertPaneFixtureCalls(t, f, [][]string{{"agent", "list"}})

		f.calls = nil
		duplicate := maps.Clone(f.host)
		duplicate["pane_id"] = "second-parent-pane"
		f.candidates = append(f.candidates, duplicate)
		matches, err = client.ObservePanes(t.Context(), []PaneSession{session}, nil)
		if err != nil || len(matches) != 0 {
			t.Fatalf("ambiguous parent pane matched: %+v, %v", matches, err)
		}
		assertPaneFixtureCalls(t, f, [][]string{{"agent", "list"}})
	})

	t.Run("ambiguous_session", func(t *testing.T) {
		f, client, request := newHerdrFixture(t, HarnessClaude)
		other := maps.Clone(f.host)
		other["pane_id"], other["focused"], other["agent_status"] = "other-pane", true, "working"
		f.candidates = append(f.candidates, other)
		matches, err := client.ObservePanes(t.Context(), []PaneSession{fixturePaneSession(request)}, nil)
		if err != nil || len(matches) != 0 {
			t.Fatalf("ambiguous pane matched: %+v, %v", matches, err)
		}
		assertPaneFixtureCalls(t, f, [][]string{{"agent", "list"}})
	})

	t.Run("command_timeout", func(t *testing.T) {
		_, client, request := newHerdrFixture(t, HarnessClaude)
		client.Runner = func(ctx context.Context, argv, _ []string) ([]byte, []byte, int, error) {
			deadline, ok := ctx.Deadline()
			if remaining := time.Until(deadline); !ok || remaining <= 0 || remaining > HerdrCommandTimeout {
				t.Errorf("Herdr call deadline = %v, present = %v", remaining, ok)
			}
			if !slices.Equal(argv[1:], []string{"agent", "list"}) {
				t.Errorf("unexpected Herdr command %q", argv)
			}
			return nil, nil, 0, context.DeadlineExceeded
		}
		matches, err := client.ObservePanes(t.Context(), []PaneSession{fixturePaneSession(request)}, nil)
		if !errors.Is(err, context.DeadlineExceeded) || len(matches) != 0 {
			t.Fatalf("matches = %+v, error = %v", matches, err)
		}
	})

	t.Run("command_output_limit", func(t *testing.T) {
		_, client, request := newHerdrFixture(t, HarnessClaude)
		client.Runner = func(_ context.Context, argv, _ []string) ([]byte, []byte, int, error) {
			if !slices.Equal(argv[1:], []string{"agent", "list"}) {
				t.Errorf("unexpected Herdr command %q", argv)
			}
			return make([]byte, HerdrOutputBytes+1), nil, 0, nil
		}
		matches, err := client.ObservePanes(t.Context(), []PaneSession{fixturePaneSession(request)}, nil)
		if err == nil || !strings.Contains(err.Error(), "output exceeded limit") || len(matches) != 0 {
			t.Fatalf("matches = %+v, error = %v", matches, err)
		}
	})
}

func fixturePaneSession(request TerminalWakeRequest) PaneSession {
	return PaneSession{
		AgentID: request.Session.AgentID, Harness: request.Harness, NativeSessionID: request.NativeSessionID,
		Process: request.Process, StateDir: request.StateDir, StatePath: request.StatePath,
	}
}

func assertPaneFixtureCalls(t *testing.T, f *herdrFixture, want [][]string) {
	t.Helper()
	if !reflect.DeepEqual(f.calls, want) || f.readyCalls != 0 {
		t.Fatalf("Herdr commands = %q, inbox reads = %d, want %q and no inbox reads", f.calls, f.readyCalls, want)
	}
}
