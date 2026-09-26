package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
)

const testCollection = "/home/owner/git/cairn"

func machineHome(t *testing.T, identities string) string {
	t.Helper()
	home := t.TempDir()
	if err := os.Chmod(home, 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CAIRN_HOME", home)
	t.Setenv("XDG_CONFIG_HOME", filepath.Join(home, "config"))
	if identities != "" {
		if err := os.WriteFile(filepath.Join(home, "identities.json"), []byte(identities), 0600); err != nil {
			t.Fatal(err)
		}
	}
	return home
}

const existingIdentities = `[
  {"token_sha256": "` + "1111111111111111111111111111111111111111111111111111111111111111" + `", "principal": "agent/claude", "repo": "/home/owner/git/cairn", "role": "agent", "destination": "hosted"},
  {"principal": "host:observer", "token_sha256": "` + "2222222222222222222222222222222222222222222222222222222222222222" + `", "repo": "/home/owner/git/cairn", "role": "observer", "destination": "local"}
]`

func fakeSystemctl(t *testing.T, home string) string {
	t.Helper()
	script := filepath.Join(home, "systemctl")
	body := "#!/bin/sh\necho \"$@\" >> " + filepath.Join(home, "systemctl.log") + "\ncase \"$2\" in is-active) echo active;; restart) touch " + filepath.Join(home, "restarted") + ";; esac\n"
	if err := os.WriteFile(script, []byte(body), 0700); err != nil {
		t.Fatal(err)
	}
	previous := systemctlProgram
	systemctlProgram = script
	t.Cleanup(func() { systemctlProgram = previous })
	return script
}

func readEnrollmentFile(t *testing.T, path string) enrollment {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("enrollment file mode: %v %v", info, err)
	}
	issued, err := readEnrollment(path)
	if err != nil {
		t.Fatal(err)
	}
	return issued
}

func assertNoTokens(t *testing.T, value any, issued enrollment) {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	for _, profile := range issued.Profiles {
		if strings.Contains(string(encoded), profile.Token) {
			t.Fatalf("result exposes the %s token", profile.Role)
		}
	}
}

func TestMachineProvisionPreservesIdentitiesAndIssuesEnrollment(t *testing.T) {
	home := machineHome(t, existingIdentities)
	out := filepath.Join(home, "box-b.enroll")
	result, err := changeMachine(context.Background(), "provision", []string{"--machine", "box-b", "--upstream", "https://central.example.ts.net:8443/", "--collection", testCollection, "--observer", "--out", out})
	if err != nil {
		t.Fatal(err)
	}
	issued := readEnrollmentFile(t, out)
	assertNoTokens(t, result, issued)
	if issued.Upstream != "https://central.example.ts.net:8443" || issued.Collection != testCollection || len(issued.Profiles) != 2 {
		t.Fatalf("unexpected enrollment: %+v", issued)
	}
	if !result.RestartRequired || result.Restarted || result.IdentityCount != 4 || len(result.Profiles) != 2 {
		t.Fatalf("unexpected result: %+v", result)
	}
	entries, err := readIdentities(filepath.Join(home, "identities.json"))
	if err != nil {
		t.Fatal(err)
	}
	var original []json.RawMessage
	_ = json.Unmarshal([]byte(existingIdentities), &original)
	for i := range original {
		if compactJSON(t, original[i]) != compactJSON(t, entries[i].raw) {
			t.Fatalf("existing identity %d changed: %s", i, entries[i].raw)
		}
	}
	for i, profile := range issued.Profiles {
		got := entries[2+i].value
		want := configuredIdentity{TokenSHA256: tokenDigest(profile.Token), Principal: "machine:box-b/" + profile.Role, Repo: testCollection, Role: profile.Role, Destination: "hosted", Remote: true, MachineID: "box-b"}
		if got != want {
			t.Fatalf("identity %d = %+v, want %+v", i, got, want)
		}
	}
	backup, err := os.ReadFile(result.Backup)
	if err != nil || string(backup) != existingIdentities {
		t.Fatalf("backup does not hold the prior configuration: %v", err)
	}
	if info, _ := os.Stat(filepath.Join(home, "identities.json")); info.Mode().Perm() != 0600 {
		t.Fatalf("identity configuration mode %v", info.Mode())
	}
}

func compactJSON(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(value)
	return string(encoded)
}

func TestMachineProvisionRefusals(t *testing.T) {
	cases := []struct {
		name  string
		setup func(home string)
		args  []string
		want  string
	}{
		{"bad machine id", nil, []string{"--machine", "Box_B"}, "INVALID_REQUEST"},
		{"plain http", nil, []string{"--upstream", "http://central:8443"}, "INVALID_REQUEST"},
		{"upstream path", nil, []string{"--upstream", "https://central/v1"}, "INVALID_REQUEST"},
		{"unknown collection", nil, []string{"--collection", "/elsewhere"}, "INVALID_REQUEST"},
		{"existing out", func(home string) { _ = os.WriteFile(filepath.Join(home, "out"), nil, 0600) }, nil, "INVALID_REQUEST"},
		{"readable config", func(home string) { _ = os.Chmod(filepath.Join(home, "identities.json"), 0644) }, nil, "INVALID_REQUEST"},
		{"capacity", func(home string) {
			var entries []map[string]any
			for i := 0; i < 31; i++ {
				digest := tokenDigest(strings.Repeat("x", i+1))
				entries = append(entries, map[string]any{"token_sha256": digest, "principal": "agent/p" + digest[:8], "repo": testCollection, "role": "agent", "destination": "hosted"})
			}
			body, _ := json.Marshal(entries)
			_ = os.WriteFile(filepath.Join(home, "identities.json"), body, 0600)
		}, []string{"--observer"}, "INVALID_REQUEST"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			home := machineHome(t, existingIdentities)
			if tc.setup != nil {
				tc.setup(home)
			}
			args := map[string]string{"--machine": "box-b", "--upstream": "https://central:8443", "--collection": testCollection, "--out": filepath.Join(home, "out")}
			var extra []string
			for i := 0; i < len(tc.args); i++ {
				if i+1 < len(tc.args) && !strings.HasPrefix(tc.args[i+1], "--") {
					args[tc.args[i]] = tc.args[i+1]
					i++
				} else {
					extra = append(extra, tc.args[i])
				}
			}
			var argv []string
			for name, value := range args {
				argv = append(argv, name, value)
			}
			before, _ := os.ReadFile(filepath.Join(home, "identities.json"))
			_, err := changeMachine(context.Background(), "provision", append(argv, extra...))
			if core.Code(err) != tc.want {
				t.Fatalf("got %v, want %s", err, tc.want)
			}
			after, _ := os.ReadFile(filepath.Join(home, "identities.json"))
			if string(before) != string(after) {
				t.Fatal("refused provisioning changed the identity configuration")
			}
			if tc.name != "existing out" {
				if _, err := os.Stat(filepath.Join(home, "out")); !os.IsNotExist(err) {
					t.Fatal("refused provisioning left an enrollment file")
				}
			}
		})
	}
}

func TestMachineRotateAndRevoke(t *testing.T) {
	home := machineHome(t, existingIdentities)
	ctx := context.Background()
	first := filepath.Join(home, "first")
	if _, err := changeMachine(ctx, "provision", []string{"--machine", "box-b", "--upstream", "https://central", "--collection", testCollection, "--observer", "--out", first}); err != nil {
		t.Fatal(err)
	}
	if _, err := changeMachine(ctx, "provision", []string{"--machine", "box-b", "--upstream", "https://central", "--collection", testCollection, "--out", filepath.Join(home, "again")}); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("second provision: %v", err)
	}
	second := filepath.Join(home, "second")
	rotated, err := changeMachine(ctx, "rotate", []string{"--machine", "box-b", "--upstream", "https://central", "--out", second})
	if err != nil {
		t.Fatal(err)
	}
	old, renewed := readEnrollmentFile(t, first), readEnrollmentFile(t, second)
	assertNoTokens(t, rotated, renewed)
	entries, _ := readIdentities(filepath.Join(home, "identities.json"))
	if len(entries) != 4 || renewed.Collection != testCollection {
		t.Fatalf("rotation changed identity count or collection: %d %q", len(entries), renewed.Collection)
	}
	for i := range renewed.Profiles {
		if renewed.Profiles[i].Principal != old.Profiles[i].Principal || renewed.Profiles[i].Token == old.Profiles[i].Token {
			t.Fatalf("rotation must keep principals and replace tokens: %+v", renewed.Profiles[i].Principal)
		}
		if entries[2+i].value.TokenSHA256 != tokenDigest(renewed.Profiles[i].Token) {
			t.Fatal("rotation did not install the new digest")
		}
	}
	listed, err := listMachines(nil)
	if err != nil || len(listed) != 1 || len(listed[0].Profiles) != 2 {
		t.Fatalf("list: %+v %v", listed, err)
	}
	revoked, err := changeMachine(ctx, "revoke", []string{"--machine", "box-b"})
	if err != nil || len(revoked.Profiles) != 2 || revoked.IdentityCount != 2 {
		t.Fatalf("revoke: %+v %v", revoked, err)
	}
	after, _ := readIdentities(filepath.Join(home, "identities.json"))
	if len(after) != 2 || after[0].value.Principal != "agent/claude" {
		t.Fatalf("revoke left %d identities", len(after))
	}
	if _, err := changeMachine(ctx, "rotate", []string{"--machine", "box-b", "--upstream", "https://central", "--out", filepath.Join(home, "third")}); core.Code(err) != "NOT_FOUND" {
		t.Fatalf("rotating a revoked machine: %v", err)
	}
}

func TestValidateIdentitiesRemoteShape(t *testing.T) {
	entry := func(value configuredIdentity) identityEntry { return identityEntry{value: value} }
	good := configuredIdentity{TokenSHA256: "a", Principal: "machine:box-b/agent", Repo: testCollection, Role: "agent", Destination: "hosted", Remote: true, MachineID: "box-b"}
	if err := validateIdentities([]identityEntry{entry(good)}); err != nil {
		t.Fatal(err)
	}
	for name, mutate := range map[string]func(*configuredIdentity){
		"local principal":    func(v *configuredIdentity) { v.Principal = "agent/worker-01" },
		"other machine":      func(v *configuredIdentity) { v.Principal = "machine:box-c/agent" },
		"local destination":  func(v *configuredIdentity) { v.Destination = "local" },
		"missing machine id": func(v *configuredIdentity) { v.MachineID = "" },
		"not remote":         func(v *configuredIdentity) { v.Remote = false },
	} {
		value := good
		mutate(&value)
		if err := validateIdentities([]identityEntry{entry(value)}); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%s accepted: %v", name, err)
		}
	}
	reserved := configuredIdentity{TokenSHA256: "b", Principal: "machine:box-b/agent", Repo: testCollection, Role: "agent", Destination: "hosted"}
	if err := validateIdentities([]identityEntry{entry(reserved)}); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("local machine: principal accepted: %v", err)
	}
}

// fakeRelay answers version on the socket once the fake systemctl restarts the relay.
func fakeRelay(t *testing.T, home string, tokens map[string]bool, version buildinfo.Info) *sync.Mutex {
	t.Helper()
	var mu sync.Mutex
	socket := filepath.Join(home, "api.sock")
	done := make(chan struct{})
	t.Cleanup(func() { close(done) })
	go func() {
		for {
			select {
			case <-done:
				return
			case <-time.After(20 * time.Millisecond):
			}
			if _, err := os.Stat(filepath.Join(home, "restarted")); err == nil {
				break
			}
		}
		listener, err := net.Listen("unix", socket)
		if err != nil {
			return
		}
		server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			token, _ := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
			mu.Lock()
			known := tokens[token]
			mu.Unlock()
			if r.URL.Path != "/v1/version" || !known {
				w.WriteHeader(401)
				_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": "AUTHORITY_DENIED"})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": version})
		})}
		go server.Serve(listener)
		<-done
		server.Close()
	}()
	return &mu
}

func stampedBuild(revision string, modified bool) buildinfo.Info {
	return buildinfo.Info{Schema: "cairn.build/1", VCS: "git", Revision: revision, Modified: &modified}
}

func useLocalBuild(t *testing.T, info buildinfo.Info) {
	previous := localBuild
	localBuild = func() buildinfo.Info { return info }
	t.Cleanup(func() { localBuild = previous })
}

func provisionForEnroll(t *testing.T, central string, observer bool) (string, enrollment) {
	t.Helper()
	t.Setenv("CAIRN_HOME", central)
	out := filepath.Join(central, "box-b.enroll")
	args := []string{"--machine", "box-b", "--upstream", "https://central:8443", "--collection", testCollection, "--out", out}
	if observer {
		args = append(args, "--observer")
	}
	if _, err := changeMachine(context.Background(), "provision", args); err != nil {
		t.Fatal(err)
	}
	return out, readEnrollmentFile(t, out)
}

func TestMachineEnrollInstallsProfilesRelayAndCollection(t *testing.T) {
	central := machineHome(t, existingIdentities)
	file, issued := provisionForEnroll(t, central, true)
	home := machineHome(t, "")
	fakeSystemctl(t, home)
	tokens := map[string]bool{}
	for _, profile := range issued.Profiles {
		tokens[profile.Token] = true
	}
	build := stampedBuild("7019705db624bfa5af09939b4a30c2f6006e39e0", false)
	useLocalBuild(t, build)
	relayTokens := fakeRelay(t, home, tokens, build)
	result, err := enrollMachine(context.Background(), []string{"--file", file})
	if err != nil {
		t.Fatal(err)
	}
	assertNoTokens(t, result, issued)
	if !result.FileRemoved || len(result.Checks) != 2 || !result.Checks[0].OK || !result.Checks[1].OK || result.Service != "active" {
		t.Fatalf("unexpected enrollment result: %+v", result)
	}
	if _, err := os.Stat(file); !os.IsNotExist(err) {
		t.Fatal("enrollment file was not removed")
	}
	for _, profile := range issued.Profiles {
		path := filepath.Join(home, roleTokenFiles[profile.Role])
		body, err := os.ReadFile(path)
		info, _ := os.Stat(path)
		if err != nil || strings.TrimSpace(string(body)) != profile.Token || info.Mode().Perm() != 0600 {
			t.Fatalf("%s token not installed owner-only", profile.Role)
		}
	}
	unit, err := os.ReadFile(filepath.Join(home, "config", "systemd", "user", relayUnit))
	if err != nil || !strings.Contains(string(unit), " relay --socket "+filepath.Join(home, "api.sock")+" --upstream https://central:8443\n") {
		t.Fatalf("relay unit: %s %v", unit, err)
	}
	log, _ := os.ReadFile(filepath.Join(home, "systemctl.log"))
	if !strings.Contains(string(log), "--user daemon-reload\n--user enable cairn-relay.service\n--user restart cairn-relay.service\n") {
		t.Fatalf("systemctl calls: %s", log)
	}
	if got := defaultRepo(); got == testCollection {
		t.Fatal("operator routes must keep the working-directory default")
	}
	enrolled, err := enrolledMachine()
	if err != nil || enrolled == nil || enrolled.Collection != testCollection || defaultAgentToken(enrolled) != "hosted-agent.token" {
		t.Fatalf("enrolled machine: %+v %v", enrolled, err)
	}
	status, err := machineStatus(context.Background(), nil)
	if err != nil || !status.Enrolled || len(status.Checks) != 2 || !status.Checks[1].OK {
		t.Fatalf("status: %+v %v", status, err)
	}
	assertNoTokens(t, status, issued)

	// Rerunning the same file after deletion is refused; a rotated file for the
	// same machine replaces its tokens without --replace.
	central2 := central
	t.Setenv("CAIRN_HOME", central2)
	rotatedFile := filepath.Join(central2, "rotated.enroll")
	if _, err := changeMachine(context.Background(), "rotate", []string{"--machine", "box-b", "--upstream", "https://central:8443", "--out", rotatedFile}); err != nil {
		t.Fatal(err)
	}
	rotated := readEnrollmentFile(t, rotatedFile)
	t.Setenv("CAIRN_HOME", home)
	relayTokens.Lock()
	for _, profile := range rotated.Profiles {
		tokens[profile.Token] = true
	}
	relayTokens.Unlock()
	again, err := enrollMachine(context.Background(), []string{"--file", rotatedFile})
	if err != nil || again.Tokens[0].State != "replaced" || again.Tokens[1].State != "replaced" {
		t.Fatalf("rotation enrollment: %+v %v", again, err)
	}
	removed, err := unenrollMachine(context.Background(), nil)
	if err != nil || len(removed.Removed) != 4 {
		t.Fatalf("unenroll: %+v %v", removed, err)
	}
	for _, name := range []string{"hosted-agent.token", "hosted-observer.token", "machine.json"} {
		if _, err := os.Lstat(filepath.Join(home, name)); !os.IsNotExist(err) {
			t.Fatalf("unenroll left %s", name)
		}
	}
	if enrolled, err := enrolledMachine(); err != nil || enrolled != nil {
		t.Fatalf("still enrolled: %+v %v", enrolled, err)
	}
}

func TestEnrolledMachineRejectsMalformedConfig(t *testing.T) {
	home := machineHome(t, "")
	_ = os.WriteFile(filepath.Join(home, "machine.json"), []byte("{}"), 0600)
	if _, err := enrolledMachine(); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("malformed machine.json: %v", err)
	}
	if _, err := agentRequest(context.Background(), []string{"version"}, strings.NewReader("{}")); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("agent route ignored malformed machine.json: %v", err)
	}
}

func TestSameBuild(t *testing.T) {
	clean := stampedBuild("abc", false)
	for name, pair := range map[string][2]buildinfo.Info{
		"different revision": {clean, stampedBuild("def", false)},
		"dirty server":       {clean, stampedBuild("abc", true)},
		"dirty local":        {stampedBuild("abc", true), clean},
		"unstamped":          {{Schema: "cairn.build/1"}, {Schema: "cairn.build/1"}},
		"unknown modified":   {{Revision: "abc"}, {Revision: "abc"}},
	} {
		if sameBuild(pair[0], pair[1]) == nil {
			t.Fatalf("%s accepted", name)
		}
	}
	if err := sameBuild(clean, clean); err != nil {
		t.Fatal(err)
	}
}

func TestMachineEnrollRefusals(t *testing.T) {
	central := machineHome(t, existingIdentities)
	file, issued := provisionForEnroll(t, central, false)
	body, _ := os.ReadFile(file)
	run := func(t *testing.T, setup func(home, file string), args ...string) error {
		home := machineHome(t, "")
		fakeSystemctl(t, home)
		copy := filepath.Join(home, "enroll")
		if err := os.WriteFile(copy, body, 0600); err != nil {
			t.Fatal(err)
		}
		if setup != nil {
			setup(home, copy)
		}
		_, err := enrollMachine(context.Background(), append([]string{"--file", copy, "--no-service"}, args...))
		return err
	}
	t.Run("readable file", func(t *testing.T) {
		if err := run(t, func(_, file string) { _ = os.Chmod(file, 0640) }); core.Code(err) != "INVALID_REQUEST" {
			t.Fatal(err)
		}
	})
	t.Run("central host", func(t *testing.T) {
		if err := run(t, func(home, _ string) { _ = os.WriteFile(filepath.Join(home, "identities.json"), []byte("[]"), 0600) }); core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "central host") {
			t.Fatal(err)
		}
	})
	t.Run("live local api", func(t *testing.T) {
		err := run(t, func(home, _ string) {
			listener, err := net.Listen("unix", filepath.Join(home, "api.sock"))
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { listener.Close() })
		})
		if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "already serves") {
			t.Fatal(err)
		}
	})
	t.Run("symlinked token", func(t *testing.T) {
		err := run(t, func(h, _ string) {
			_ = os.Symlink(filepath.Join(h, "elsewhere"), filepath.Join(h, "hosted-agent.token"))
		})
		if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "not a regular file") {
			t.Fatal(err)
		}
	})
	t.Run("other machine enrolled", func(t *testing.T) {
		err := run(t, func(h, _ string) {
			_ = os.WriteFile(filepath.Join(h, "machine.json"), []byte(`{"schema":"cairn.machine/1","machine_id":"box-c","upstream":"https://central","collection":"/c","principals":["machine:box-c/agent"]}`), 0600)
		})
		if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "unenroll") {
			t.Fatal(err)
		}
	})
	t.Run("differing token", func(t *testing.T) {
		var home string
		err := run(t, func(h, _ string) {
			home = h
			_ = os.WriteFile(filepath.Join(h, "hosted-agent.token"), []byte("someone-elses-existing-token\n"), 0600)
		})
		if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "different token") {
			t.Fatal(err)
		}
		if kept, _ := os.ReadFile(filepath.Join(home, "hosted-agent.token")); string(kept) != "someone-elses-existing-token\n" {
			t.Fatal("refused enrollment replaced a token")
		}
	})
	t.Run("unknown field", func(t *testing.T) {
		err := run(t, func(_, file string) {
			_ = os.WriteFile(file, []byte(strings.Replace(string(body), `"schema"`, `"extra": 1, "schema"`, 1)), 0600)
		})
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatal(err)
		}
	})
	t.Run("version mismatch", func(t *testing.T) {
		home := machineHome(t, "")
		fakeSystemctl(t, home)
		copy := filepath.Join(home, "enroll")
		_ = os.WriteFile(copy, body, 0600)
		useLocalBuild(t, stampedBuild("abc", false))
		fakeRelay(t, home, map[string]bool{issued.Profiles[0].Token: true}, stampedBuild("def", false))
		_, err := enrollMachine(context.Background(), []string{"--file", copy})
		if core.Code(err) != "INSTALL_FAILED" || !strings.Contains(err.Error(), "same Cairn build") || !strings.Contains(err.Error(), "rerun cairn machine enroll") {
			t.Fatal(err)
		}
		if _, statErr := os.Stat(copy); statErr != nil {
			t.Fatal("failed enrollment removed the enrollment file")
		}
	})
}
