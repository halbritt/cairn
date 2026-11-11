package semantic

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/google/uuid"
)

// This is an actual disposable combined API process, not a fake HTTP server or
// an installed service. It measures only fixture restart outage on this host.
func transitionWholeAPI(t *testing.T) {
	d := transitionDatabase(t)
	cli := os.Getenv("CAIRN_TRANSITION_CLI")
	if !filepath.IsAbs(cli) {
		t.Fatal("wrapper-built API binary required")
	}
	home, err := os.MkdirTemp("/tmp", "s05-api-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(home); err != nil {
			t.Error(err)
		}
	})
	socket := filepath.Join(home, "api.sock")
	token := "disposable-s05-" + uuid.NewString()
	identities := filepath.Join(home, "identities.json")
	if err := os.WriteFile(identities, []byte(transitionJSON([]map[string]string{{"token_sha256": transitionHash(token), "principal": "s05-fixture-api", "repo": "s05-api", "role": "agent", "destination": "local"}})), 0600); err != nil {
		t.Fatal(err)
	}
	transport := &http.Transport{DisableKeepAlives: true, DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", socket)
	}}
	t.Cleanup(transport.CloseIdleConnections)
	client := &http.Client{Transport: transport, Timeout: 500 * time.Millisecond}
	probe := func() bool {
		req, err := http.NewRequest(http.MethodPost, "http://fixture/v1/version", strings.NewReader("{}"))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		resp, err := client.Do(req)
		if err != nil {
			return false
		}
		defer resp.Body.Close()
		b, err := io.ReadAll(io.LimitReader(resp.Body, 65536))
		if err != nil || resp.StatusCode != 200 {
			return false
		}
		var envelope struct {
			OK bool `json:"ok"`
		}
		return json.Unmarshal(b, &envelope) == nil && envelope.OK
	}
	start := func(name string, args ...string) func() {
		log, err := os.Create(filepath.Join(home, name+".log"))
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { log.Close() })
		argv := append([]string{"serve", "--socket", socket, "--identities", identities, "--machine-id", "s05-fixture"}, args...)
		cmd := exec.Command(cli, argv...)
		for _, entry := range os.Environ() {
			if !strings.HasPrefix(entry, "CAIRN_DATABASE_URL=") && !strings.HasPrefix(entry, "CAIRN_HOME=") {
				cmd.Env = append(cmd.Env, entry)
			}
		}
		cmd.Env = append(cmd.Env, "CAIRN_HOME="+home, "CAIRN_DATABASE_URL="+d.dsn)
		cmd.Stdout = log
		cmd.Stderr = log
		if err = cmd.Start(); err != nil {
			t.Fatal(err)
		}
		done := make(chan error, 1)
		go func() { done <- cmd.Wait(); close(done) }()
		stopped := false
		stop := func() {
			if stopped {
				return
			}
			stopped = true
			if err := cmd.Process.Signal(syscall.SIGTERM); err != nil && err != os.ErrProcessDone {
				t.Error(err)
			}
			select {
			case err := <-done:
				if err != nil {
					b, _ := os.ReadFile(filepath.Join(home, name+".log"))
					t.Errorf("API shutdown: %v %s", err, b)
				}
			case <-time.After(10 * time.Second):
				cmd.Process.Kill()
				<-done
				t.Error("combined API did not join on shutdown")
			}
			if err := syscall.Kill(cmd.Process.Pid, 0); err != syscall.ESRCH {
				t.Error("API process not reaped")
			}
		}
		t.Cleanup(stop)
		transitionWait(t, "combined API "+name, func() bool {
			select {
			case err := <-done:
				b, _ := os.ReadFile(filepath.Join(home, name+".log"))
				t.Fatalf("API exited: %v %s", err, b)
			default:
			}
			return probe()
		})
		t.Logf("S05 disposable service %s argv=%s pid=%d", name, transitionJSON(argv), cmd.Process.Pid)
		return stop
	}

	d.source(t, "s05-api", "whole API source é")
	d.source(t, "other-collection", "database-wide alternate collection")
	python := transitionFixture(t, "api-python-like")
	rust := transitionFixture(t, "api-rust-like")
	stop := start("default-no-semantic")
	if len(python.pids(t, ""))+len(rust.pids(t, "")) != 0 {
		t.Fatal("default selection launched a model")
	}
	transitionRequireCoverage(t, d, python.hash, 2, 0)
	lastUp := time.Now()
	stop()
	if probe() {
		t.Fatal("whole API falsely remained available after process join")
	}
	stop = start("explicit-python-like", "--embedding-command", python.path)
	t.Logf("S05 fixture whole-API default-to-embed observed outage=%s", time.Since(lastUp))
	transitionWait(t, "first API fixture rebuild", func() bool { return d.coverage(t, python.hash).Complete == 2 })
	for n, generation := range []string{"rust-like", "python-like"} {
		lastUp = time.Now()
		stop()
		if probe() {
			t.Fatal("API remained available after fence")
		}
		if n == 0 {
			python.reaped(t)
		} else {
			rust.reaped(t)
		}
		d.exec(t, `UPDATE cairn.s05_phase SET phase=$1`, n+1)
		selected := python
		if n == 0 {
			selected = rust
			// S04 fixed launchers take zero arguments. Preserve the generic
			// embedding selection on both sides; the raw-binary Rust route is
			// exercised separately by the forward/reverse index fixture.
			stop = start(generation, "--embedding-command", rust.path)
		} else {
			stop = start(generation, "--embedding-command", python.path)
		}
		t.Logf("S05 fixture whole-API %s observed outage=%s", generation, time.Since(lastUp))
		rebuild := time.Now()
		transitionWait(t, "API selected generation rebuild", func() bool { return d.coverage(t, selected.hash).Complete == 2 })
		transitionRequireCoverage(t, d, selected.hash, 2, 2)
		old := python.hash
		if n == 1 {
			old = rust.hash
		}
		if d.count(t, `SELECT count(*) FROM cairn.s05_publication WHERE phase=$1 AND model=$2`, n+1, old) != 0 {
			t.Fatal("old API generation published after joined fence")
		}
		t.Logf("S05 fixture %s rebuild=%s embed_identity=%s", generation, time.Since(rebuild), selected.hash)
	}
	stop()
	python.reaped(t)
	if directory := os.Getenv("CAIRN_TRANSITION_REPORT_DIR"); directory != "" {
		facts := map[string]any{"scope": "disposable combined API / deterministic protocol workers", "source_population": 2, "selected_mode": "embed", "python_fixture_identity": python.hash, "rust_fixture_identity": rust.hash, "real_worker_acceptance": false, "rank_identity": "unobserved: real rollback requires accepted S04R"}
		if err := os.WriteFile(filepath.Join(directory, "service-handoff.json"), []byte(fmt.Sprintln(transitionJSON(facts))), 0600); err != nil {
			t.Fatal(err)
		}
	}
}
