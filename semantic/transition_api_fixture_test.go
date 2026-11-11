package semantic

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/google/uuid"
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
)

// Reused by deterministic handoff and the explicitly selected real case 1.
func transitionAPIProcess(t *testing.T, d *transitionDB) (func(string, ...string) func(), func() bool, func(any) (json.RawMessage, error)) {
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
	collection := "s05-api"
	if os.Getenv("CAIRN_TRANSITION_REAL_SELECTION") != "" {
		collection = "s05-a"
	}
	if err := os.WriteFile(identities, []byte(transitionJSON([]map[string]string{{"token_sha256": transitionHash(token), "principal": "s05-fixture-api", "repo": collection, "role": "agent", "destination": "local"}})), 0600); err != nil {
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
		logPath := filepath.Join(home, name+".log")
		if report := os.Getenv("CAIRN_TRANSITION_REPORT_DIR"); report != "" {
			logPath = filepath.Join(report, "service-"+name+".log")
		}
		log, err := os.Create(logPath)
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
			if cmd.Process.Pid <= 1 {
				t.Fatal("refusing process control for PID <= 1")
			}
			if err := cmd.Process.Signal(syscall.SIGTERM); err != nil && err != os.ErrProcessDone {
				t.Error(err)
			}
			select {
			case err := <-done:
				if err != nil {
					b, _ := os.ReadFile(logPath)
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
				b, _ := os.ReadFile(logPath)
				t.Fatalf("API exited: %v %s", err, b)
			default:
			}
			return probe()
		})
		t.Logf("S05 disposable service %s argv=%s pid=%d", name, transitionJSON(argv), cmd.Process.Pid)
		return stop
	}

	query := func(input any) (json.RawMessage, error) {
		raw, err := json.Marshal(input)
		if err != nil {
			return nil, err
		}
		req, err := http.NewRequest(http.MethodPost, "http://fixture/v1/index", strings.NewReader(string(raw)))
		if err != nil {
			return nil, err
		}
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		// This is an observation budget; StartIndex retains its own two-second limit.
		caller := &http.Client{Transport: transport, Timeout: 5 * time.Second}
		resp, err := caller.Do(req)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()
		raw, err = io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
		if err != nil {
			return nil, err
		}
		if resp.StatusCode != 200 {
			return nil, fmt.Errorf("API index status %d: %s", resp.StatusCode, raw)
		}
		return raw, nil
	}
	return start, probe, query
}
