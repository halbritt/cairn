package coordination

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

type selectedFenceFixture struct {
	t      *testing.T
	root   string
	host   *selectedFenceHost
	scope  SelectedUnitFenceScope
	units  map[string]map[string]string
	listed string
}

func newSelectedFenceFixture(t *testing.T) *selectedFenceFixture {
	t.Helper()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	f := &selectedFenceFixture{t: t, root: root, units: map[string]map[string]string{}}
	f.write("machine", strings.Repeat("a", 32)+"\n")
	f.write("proc/sys/kernel/random/boot_id", "11111111-1111-4111-8111-111111111111\n")
	f.write("selected-executable", "#!/bin/sh\nexit 0\n")
	if err := os.Chmod(filepath.Join(root, "selected-executable"), 0500); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(root, "selected-executable"))
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw)
	f.scope = SelectedUnitFenceScope{UID: uint32(os.Getuid()), Units: []string{"selected.service"},
		Executables: []WatchCensusPin{{Path: filepath.Join(root, "selected-executable"), SHA256: hex.EncodeToString(sum[:])}},
		Database:    "owned_fixture", DatabaseSystemID: "12345", Window: 30 * time.Second}
	f.host = &selectedFenceHost{procRoot: filepath.Join(root, "proc"), cgroupRoot: filepath.Join(root, "cgroup"), machineFile: filepath.Join(root, "machine")}
	f.host.dbRead = func(context.Context, SelectedUnitFenceScope) (map[string]string, error) {
		return map[string]string{"identity": selectedFenceHash("unit-fixture-only"), "other-backends": "0", "visibility": "pg_read_all_stats-or-superuser", "transaction": "read-only"}, nil
	}
	f.host.query = func(_ context.Context, args ...string) (string, error) {
		if args[0] == "list-units" {
			return f.listed, nil
		}
		p, ok := f.units[args[len(args)-1]]
		if !ok {
			return "", selectedFenceUnknown("unit fixture has no selected member")
		}
		var lines []string
		for _, key := range selectedFenceProperties {
			if value, exists := p[key]; exists {
				lines = append(lines, key+"="+value)
			}
		}
		return strings.Join(lines, "\n") + "\n", nil
	}
	f.unit("selected.service", "/selected")
	return f
}

func (f *selectedFenceFixture) write(name, body string) {
	f.t.Helper()
	path := filepath.Join(f.root, name)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		f.t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0600); err != nil {
		f.t.Fatal(err)
	}
}

func (f *selectedFenceFixture) unit(name, group string) {
	f.write("definition-"+name, "[Service]\nRestart=no\n")
	f.write("cgroup"+group+"/cgroup.events", "populated 0\nfrozen 0\n")
	f.write("cgroup"+group+"/cgroup.procs", "")
	f.units[name] = map[string]string{"Id": name, "LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "UnitFileState": "masked", "MainPID": "0", "ControlGroup": group,
		"Restart": "no", "FragmentPath": filepath.Join(f.root, "definition-"+name), "DropInPaths": "", "ExecStart": "synthetic-unit-parser-fixture", "Triggers": "", "TriggeredBy": "", "NeedDaemonReload": "no", "Transient": "no", "InvocationID": "", "StateChangeTimestampMonotonic": "100"}
}

func (f *selectedFenceFixture) capture() *SelectedUnitFenceReceipt {
	f.t.Helper()
	r, err := captureSelectedUnitFence(context.Background(), f.scope, f.host)
	if err != nil {
		f.t.Fatal(err)
	}
	return r
}

func TestSelectedUnitFenceReceiptOriginScopeAndCopy(t *testing.T) {
	f := newSelectedFenceFixture(t)
	r := f.capture()
	if err := r.Recheck(context.Background(), f.scope); err != nil {
		t.Fatal(err)
	}
	facts := r.Facts()
	if facts["external_coverage"] != "selected-units-only" || facts["outside_scope"] != "unknown" || facts["database_coverage"] != "selected" {
		t.Fatal("selected-only evidence boundary missing")
	}
	facts["scope"] = "caller-forged"
	facts["stopped"] = "true"
	if err := r.Recheck(context.Background(), f.scope); err != nil {
		t.Fatal("editing evidence copy changed private receipt")
	}
	copy := *r
	if err := copy.Recheck(context.Background(), f.scope); err == nil || copy.Facts() != nil {
		t.Fatal("copied receipt acquired origin")
	}
	var zero SelectedUnitFenceReceipt
	if zero.Recheck(context.Background(), f.scope) == nil || zero.Facts() != nil {
		t.Fatal("zero value minted a receipt")
	}
	if _, err := json.Marshal(r); err == nil {
		t.Fatal("receipt was serialized for handoff")
	}
	if _, err := json.Marshal(*r); err == nil {
		t.Fatal("receipt value was serialized for handoff")
	}
	if err := json.Unmarshal([]byte(`{"stopped":true,"complete":true,"Inspected":true,"Facts":{"scope":"caller"}}`), &zero); err == nil {
		t.Fatal("caller JSON minted origin")
	}
	if zero.Recheck(context.Background(), f.scope) == nil {
		t.Fatal("JSON replay became usable")
	}
}

func TestSelectedUnitFenceLifecycleWithoutDatabase(t *testing.T) {
	for _, mutation := range []string{"none", "selected-database", "active-unit"} {
		t.Run(mutation, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			f.scope.Database, f.scope.DatabaseSystemID = "", ""
			f.host.dbRead = func(context.Context, SelectedUnitFenceScope) (map[string]string, error) {
				t.Fatal("unselected database reader was called")
				return nil, errors.New("unreachable")
			}
			r := f.capture()
			facts := r.Facts()
			if facts["database_coverage"] != "not-selected" || facts["outside_scope"] != "unknown" {
				t.Fatal("unselected database coverage was not explicit")
			}
			for key := range facts {
				if strings.HasPrefix(key, "database:") {
					t.Fatal("unselected database acquired writer evidence")
				}
			}
			facts["database_coverage"] = "selected"
			copy := *r
			if copy.Recheck(context.Background(), f.scope) == nil {
				t.Fatal("copied lifecycle receipt acquired origin")
			}
			expected := f.scope
			switch mutation {
			case "selected-database":
				expected.Database, expected.DatabaseSystemID = "caller-selected", "12345"
			case "active-unit":
				f.units["selected.service"]["ActiveState"] = "active"
			}
			err := r.Recheck(context.Background(), expected)
			if mutation == "none" {
				if err != nil || r.Facts()["database_coverage"] != "not-selected" {
					t.Fatal("lifecycle recheck failed or caller facts altered coverage")
				}
			} else if err == nil || r.Recheck(context.Background(), f.scope) == nil {
				t.Fatal("lifecycle drift or database scope change did not invalidate")
			}
		})
	}
}

func TestSelectedUnitFenceDatabaseSelectionRefuses(t *testing.T) {
	for _, test := range []struct {
		name, database, systemID string
		connection               *pgx.Conn
	}{
		{"name-only", "selected", "", nil},
		{"system-only", "", "12345", nil},
		{"name-with-connection", "selected", "", &pgx.Conn{}},
		{"system-with-connection", "", "12345", &pgx.Conn{}},
		{"unselected-connection", "", "", &pgx.Conn{}},
		{"selected-without-connection", "selected", "12345", nil},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			f.scope.Database, f.scope.DatabaseSystemID = test.database, test.systemID
			if _, err := CaptureSelectedUnitFence(context.Background(), f.scope, test.connection); err == nil {
				t.Fatal("partial selection or mismatched connection accepted")
			}
		})
	}
}

func TestSelectedUnitFenceScopeAndWindowRefuse(t *testing.T) {
	for _, which := range []string{"uid", "unit", "duplicate", "database", "system-id", "window", "executable", "command"} {
		t.Run(which, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			s := f.scope
			switch which {
			case "uid":
				s.UID++
			case "unit":
				s.Units = []string{"--caller-flag"}
			case "duplicate":
				s.Units = []string{"selected.service", "selected.service"}
			case "database":
				s.Database = ""
			case "system-id":
				s.DatabaseSystemID = "caller-complete"
			case "window":
				s.Window = time.Hour
			case "executable":
				s.Executables = nil
			case "command":
				s.CommandSHA256 = []string{"private-argv-must-not-be-accepted"}
			}
			if _, err := captureSelectedUnitFence(context.Background(), s, f.host); err == nil {
				t.Fatal("unknown scope captured")
			}
		})
	}
	f := newSelectedFenceFixture(t)
	r := f.capture()
	r.origin.deadline = time.Now().Add(-time.Second)
	if r.Recheck(context.Background(), f.scope) == nil {
		t.Fatal("expired observation accepted")
	}
	r = f.capture()
	r.origin.start++
	if r.Recheck(context.Background(), f.scope) == nil {
		t.Fatal("origin generation replay accepted")
	}
	r = f.capture()
	changed := f.scope
	changed.Units = []string{"other.service"}
	if r.Recheck(context.Background(), changed) == nil || r.Recheck(context.Background(), f.scope) == nil {
		t.Fatal("wrong-scope invalidation was replayable")
	}
	if _, err := CaptureSelectedUnitFence(context.Background(), f.scope, nil); err == nil {
		t.Fatal("missing authenticated connection did not keep public capture unsupported")
	}
}

func TestSelectedUnitFenceUnitStatesAndTriggersRefuse(t *testing.T) {
	for _, field := range []string{"ActiveState", "SubState", "MainPID", "UnitFileState", "LoadState", "NeedDaemonReload", "ControlGroup", "StateChangeTimestampMonotonic", "TriggeredBy"} {
		t.Run(field, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			values := map[string]string{"ActiveState": "active", "SubState": "running", "MainPID": "123", "UnitFileState": "unknown", "LoadState": "not-found", "NeedDaemonReload": "yes", "ControlGroup": "relative", "StateChangeTimestampMonotonic": "unknown", "TriggeredBy": "unknown.timer"}
			f.units["selected.service"][field] = values[field]
			if _, err := captureSelectedUnitFence(context.Background(), f.scope, f.host); err == nil {
				t.Fatal("unsafe or unknown selected unit accepted")
			}
		})
	}
	f := newSelectedFenceFixture(t)
	f.unit("selected.timer", "/timer")
	f.units["selected.service"]["TriggeredBy"] = "selected.timer"
	f.units["selected.timer"]["Triggers"] = "selected.service"
	r := f.capture()
	f.units["selected.timer"]["UnitFileState"] = "enabled"
	if r.Recheck(context.Background(), f.scope) == nil {
		t.Fatal("restart trigger became enabled")
	}
	for _, raw := range []string{"Id=x\nId=y\n", "CallerComplete=true\n", "Id\n", "Id=bad\x00name\n"} {
		if _, err := selectedFenceParseProperties(raw); err == nil {
			t.Fatal("malformed systemd record accepted")
		}
	}
	for _, raw := range []string{"caller status\n", "cairn-wake-a.service loaded inactive dead\ncairn-wake-a.service loaded inactive dead\n"} {
		if _, err := selectedFenceUnitList(raw); err == nil {
			t.Fatal("unknown transient membership accepted")
		}
	}
}

func TestSelectedUnitFenceDefinitionTransientHostAndFinalDrift(t *testing.T) {
	for _, mutation := range []string{"definition", "dropin", "transient", "machine", "boot", "cgroup", "executable", "permission"} {
		t.Run(mutation, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			r := f.capture()
			switch mutation {
			case "definition":
				f.write("definition-selected.service", "changed definition\n")
			case "dropin":
				f.write("dropin", "changed restart policy\n")
				f.units["selected.service"]["DropInPaths"] = filepath.Join(f.root, "dropin")
			case "transient":
				f.unit("cairn-wake-new.service", "/new")
				f.listed = "cairn-wake-new.service loaded inactive dead\n"
			case "machine":
				f.write("machine", strings.Repeat("b", 32)+"\n")
			case "boot":
				f.write("proc/sys/kernel/random/boot_id", "22222222-2222-4222-8222-222222222222\n")
			case "cgroup":
				f.write("cgroup/selected/cgroup.events", "populated 1\nfrozen 0\n")
			case "executable":
				if err := os.Chmod(filepath.Join(f.root, "selected-executable"), 0700); err != nil {
					t.Fatal(err)
				}
				f.write("selected-executable", "changed image\n")
			case "permission":
				f.host.query = func(context.Context, ...string) (string, error) { return "", os.ErrPermission }
			}
			if r.Recheck(context.Background(), f.scope) == nil || r.Facts()["receipt_invalidated"] != "true" {
				t.Fatal("changed or unreadable fact did not invalidate")
			}
		})
	}
	f := newSelectedFenceFixture(t)
	reads := 0
	query := f.host.query
	f.host.query = func(ctx context.Context, args ...string) (string, error) {
		if args[0] == "show" && args[len(args)-1] == "selected.service" {
			reads++
			if reads == 4 {
				f.units["selected.service"]["StateChangeTimestampMonotonic"] = "101"
			}
		}
		return query(ctx, args...)
	}
	r, err := captureSelectedUnitFence(context.Background(), f.scope, f.host)
	if err != nil {
		t.Fatal(err)
	}
	if r.Recheck(context.Background(), f.scope) == nil {
		t.Fatal("second final read drift was ignored")
	}
	f.units["selected.service"]["StateChangeTimestampMonotonic"] = "100"
	if r.Recheck(context.Background(), f.scope) == nil {
		t.Fatal("invalidated receipt revived after facts returned")
	}
}

func TestSelectedUnitFenceCgroupUnknownIsNotEmpty(t *testing.T) {
	for _, mutation := range []string{"missing", "regular-file", "missing-events", "permission", "populated", "duplicate", "missing-populated", "procs", "nested", "symlink"} {
		t.Run(mutation, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			group := "/selected"
			switch mutation {
			case "missing":
				group = "/missing"
			case "regular-file":
				f.write("cgroup/file", "")
				group = "/file"
			case "missing-events":
				if err := os.Remove(filepath.Join(f.root, "cgroup/selected/cgroup.events")); err != nil {
					t.Fatal(err)
				}
			case "permission":
				if err := os.Chmod(filepath.Join(f.root, "cgroup/selected/cgroup.events"), 0); err != nil {
					t.Fatal(err)
				}
			case "populated":
				f.write("cgroup/selected/cgroup.events", "populated 1\nfrozen 0\n")
			case "duplicate":
				f.write("cgroup/selected/cgroup.events", "populated 0\npopulated 0\n")
			case "missing-populated":
				f.write("cgroup/selected/cgroup.events", "frozen 0\n")
			case "procs":
				f.write("cgroup/selected/cgroup.procs", "123\n")
			case "nested":
				f.write("cgroup/selected/child/cgroup.events", "populated 1\nfrozen 0\n")
				f.write("cgroup/selected/child/cgroup.procs", "123\n")
			case "symlink":
				if err := os.Symlink(filepath.Join(f.root, "cgroup/selected"), filepath.Join(f.root, "cgroup/link")); err != nil {
					t.Fatal(err)
				}
				group = "/link"
			}
			if _, err := selectedFenceGroup(f.host.cgroupRoot, group); err == nil {
				t.Fatal("unknown or occupied cgroup accepted as empty")
			}
		})
	}
}

func (f *selectedFenceFixture) process(pid int, uid uint32, executable string) selectedFenceProcScan {
	f.t.Helper()
	fields := make([]string, 20)
	for i := range fields {
		fields[i] = "0"
	}
	fields[0], fields[1], fields[2], fields[3], fields[19] = "S", "1", "1", "1", "100"
	f.write(fmt.Sprintf("proc/%d/stat", pid), fmt.Sprintf("%d (fixture process) %s\n", pid, strings.Join(fields, " ")))
	f.write(fmt.Sprintf("proc/%d/status", pid), fmt.Sprintf("Uid:\t%d\t%d\t%d\t%d\n", uid, uid, uid, uid))
	f.write(fmt.Sprintf("proc/%d/cmdline", pid), "fixture\x00private-argument-canary\x00")
	comm := filepath.Base(executable)
	f.write(fmt.Sprintf("proc/%d/comm", pid), comm[:min(15, len(comm))]+"\n")
	f.write(fmt.Sprintf("proc/%d/cgroup", pid), "0::/user.slice/unrelated.scope\n")
	if err := os.Symlink(executable, filepath.Join(f.host.procRoot, strconv.Itoa(pid), "exe")); err != nil {
		f.t.Fatal(err)
	}
	info, err := os.Stat(executable)
	if err != nil {
		f.t.Fatal(err)
	}
	s := info.Sys().(*syscall.Stat_t)
	return selectedFenceProcScan{root: f.host.procRoot, uid: f.scope.UID, executables: []selectedFenceExecutable{{executable, s.Dev, s.Ino}}, read: os.ReadFile}
}

func TestSelectedUnitFenceProcGenerationUIDPermissionAndMatching(t *testing.T) {
	for _, mutation := range []string{"live", "command", "command-drift", "generation", "uid", "permission", "zombie"} {
		t.Run(mutation, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			scan := f.process(123, f.scope.UID, f.scope.Executables[0].Path)
			switch mutation {
			case "command":
				scan.executables = nil
				raw, err := os.ReadFile(filepath.Join(f.host.procRoot, "123/cmdline"))
				if err != nil {
					t.Fatal(err)
				}
				sum := sha256.Sum256(raw)
				scan.commands = []string{hex.EncodeToString(sum[:])}
			case "generation":
				scan.read = func(path string) ([]byte, error) {
					if filepath.Base(path) == "cmdline" {
						raw, err := os.ReadFile(filepath.Join(f.host.procRoot, "123/stat"))
						if err != nil {
							return nil, err
						}
						if strings.HasSuffix(string(raw), "100\n") {
							f.write("proc/123/stat", strings.TrimSuffix(string(raw), "100\n")+"101\n")
						}
					}
					return os.ReadFile(path)
				}
			case "command-drift":
				reads := 0
				scan.read = func(path string) ([]byte, error) {
					if filepath.Base(path) == "cmdline" {
						reads++
						if reads == 2 {
							return []byte("changed-command\x00"), nil
						}
					}
					return os.ReadFile(path)
				}
			case "uid":
				calls := 0
				scan.read = func(path string) ([]byte, error) {
					if filepath.Base(path) == "status" {
						calls++
						if calls == 1 {
							return []byte("Uid:\t9999\t9999\t9999\t9999\n"), nil
						}
					}
					return os.ReadFile(path)
				}
			case "permission":
				scan.read = func(path string) ([]byte, error) {
					if filepath.Base(path) == "cmdline" {
						return nil, os.ErrPermission
					}
					return os.ReadFile(path)
				}
			case "zombie":
				raw, err := os.ReadFile(filepath.Join(f.host.procRoot, "123/stat"))
				if err != nil {
					t.Fatal(err)
				}
				f.write("proc/123/stat", strings.Replace(string(raw), ") S ", ") Z ", 1))
			}
			matched, err := scan.process(123)
			if mutation == "live" || mutation == "command" {
				if err != nil || !matched {
					t.Fatal("selected live identity not detected")
				}
			} else if err == nil {
				t.Fatal("process change or unreadable identity ignored")
			}
			if err != nil && strings.Contains(err.Error(), "private-argument-canary") {
				t.Fatal("private argv leaked into diagnostic")
			}
		})
	}
	f := newSelectedFenceFixture(t)
	r := f.capture()
	f.process(123, f.scope.UID, f.scope.Executables[0].Path)
	if r.Recheck(context.Background(), f.scope) == nil {
		t.Fatal("fresh matching process did not invalidate receipt")
	}
}

func TestSelectedFenceChildProcess(t *testing.T) {
	if os.Getenv("CAIRN_SELECTED_FENCE_CHILD") != "1" {
		return
	}
	fmt.Println("ready")
	_, _ = io.Copy(io.Discard, os.Stdin)
}

func TestSelectedUnitFenceOwnedChildStopJoin(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestSelectedFenceChildProcess$")
	cmd.Env = append(os.Environ(), "CAIRN_SELECTED_FENCE_CHILD=1")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	joined := false
	t.Cleanup(func() {
		_ = stdin.Close()
		if !joined {
			_ = cmd.Process.Kill()
			_ = cmd.Wait()
		}
	})
	ready, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil || ready != "ready\n" || cmd.Process.Pid <= 1 {
		t.Fatal("owned child readiness failed")
	}
	info, err := os.Stat(fmt.Sprintf("/proc/%d/exe", cmd.Process.Pid))
	if err != nil {
		t.Fatal("owned child image unavailable")
	}
	identity := info.Sys().(*syscall.Stat_t)
	path, err := os.Readlink(fmt.Sprintf("/proc/%d/exe", cmd.Process.Pid))
	if err != nil {
		t.Fatal("owned child executable unavailable")
	}
	scan := selectedFenceProcScan{root: "/proc", uid: uint32(os.Getuid()), executables: []selectedFenceExecutable{{path, identity.Dev, identity.Ino}}, read: os.ReadFile}
	matched, err := scan.process(cmd.Process.Pid)
	if err != nil || !matched {
		t.Fatal("actual owned live process was not detected")
	}
	if err := stdin.Close(); err != nil {
		t.Fatal(err)
	}
	if err := cmd.Wait(); err != nil {
		t.Fatal("owned child did not stop normally")
	}
	joined = true
	if _, err := os.Stat(fmt.Sprintf("/proc/%d", cmd.Process.Pid)); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("joined owned child PID remains")
	}
	t.Logf("owned_pid=%d stop=EOF exit=0 joined=true proc_absent=true; no installed-service action", cmd.Process.Pid)
}

func TestSelectedUnitFenceOwnedPostgresFacts(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires maintained disposable PostgreSQL fixture")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	config, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("owned database configuration unavailable")
	}
	if !filepath.IsAbs(config.Host) {
		t.Fatal("owned Unix PostgreSQL required")
	}
	admin, err := pgx.ConnectConfig(ctx, config)
	if err != nil {
		t.Fatal("owned database connection unavailable")
	}
	defer admin.Close(ctx)
	name := fmt.Sprintf("selected_fence_%d", os.Getpid())
	quoted := pgx.Identifier{name}.Sanitize()
	if _, err := admin.Exec(ctx, "CREATE DATABASE "+quoted); err != nil {
		t.Fatal("owned database creation failed")
	}
	defer func() {
		if _, err := admin.Exec(context.Background(), "DROP DATABASE "+quoted+" WITH (FORCE)"); err != nil {
			t.Error("owned database cleanup failed")
		}
	}()
	selected := config.Copy()
	selected.Database = name
	db, err := pgx.ConnectConfig(ctx, selected)
	if err != nil {
		t.Fatal("selected owned connection failed")
	}
	defer db.Close(context.Background())
	if _, err := db.Exec(ctx, "CREATE TABLE public.semantic_job(id integer)"); err != nil {
		t.Fatal("owned public-schema decoy setup failed")
	}
	var systemID string
	if err := db.QueryRow(ctx, "SELECT system_identifier::text FROM pg_control_system()").Scan(&systemID); err != nil {
		t.Fatal("owned cluster identity unavailable")
	}
	f := newSelectedFenceFixture(t)
	f.scope.Database, f.scope.DatabaseSystemID = name, systemID
	f.host.db = db
	f.host.dbRead = nil
	if _, err := selectedFenceDatabase(ctx, db, f.scope); err == nil {
		t.Fatal("public-schema decoy supplied Cairn relation identity")
	}
	ddl, err := os.ReadFile("../../../core/schema/056_semantic_passages.sql")
	if err != nil {
		t.Fatal(err)
	}
	_, definition, found := strings.Cut(string(ddl), "CREATE TABLE cairn.semantic_job (")
	table, _, ended := strings.Cut(definition, ");")
	if !found || !ended {
		t.Fatal("maintained semantic_job schema unavailable")
	}
	if _, err := db.Exec(ctx, "CREATE SCHEMA cairn; CREATE TABLE cairn.memory_record(record_id uuid PRIMARY KEY); CREATE TABLE cairn.semantic_job ("+table+"); INSERT INTO cairn.memory_record VALUES ('00000000-0000-4000-8000-000000000001'); INSERT INTO cairn.semantic_job(record_id) SELECT record_id FROM cairn.memory_record"); err != nil {
		t.Fatal("owned maintained Cairn semantic_job schema setup failed")
	}
	if _, err := db.Exec(ctx, "BEGIN"); err != nil {
		t.Fatal("owned caller transaction setup failed")
	}
	if _, err := selectedFenceDatabase(ctx, db, f.scope); err == nil || db.PgConn().TxStatus() != 'T' {
		t.Fatal("reader accepted or rolled back an existing caller transaction")
	}
	if _, err := CaptureSelectedUnitFence(ctx, f.scope, db); err == nil || db.PgConn().TxStatus() != 'T' {
		t.Fatal("public reader did not refuse a borrowed transaction before host queries")
	}
	if _, err := db.Exec(ctx, "ROLLBACK"); err != nil {
		t.Fatal("owned caller transaction cleanup failed")
	}
	r := f.capture()
	if err := r.Recheck(ctx, f.scope); err != nil {
		t.Fatal(err)
	}
	writer, err := pgx.ConnectConfig(ctx, selected)
	if err != nil {
		t.Fatal("owned writer connection failed")
	}
	if _, err := writer.Exec(ctx, "BEGIN"); err != nil {
		t.Fatal("owned writer transaction failed")
	}
	if _, err := writer.Exec(ctx, "UPDATE cairn.semantic_job SET attempts=attempts+1"); err != nil {
		t.Fatal("owned live writer setup failed")
	}
	if r.Recheck(ctx, f.scope) == nil {
		t.Fatal("actual live semantic_job writer accepted")
	}
	if _, err := writer.Exec(ctx, "ROLLBACK"); err != nil {
		t.Fatal("owned writer rollback failed")
	}
	writerPID := writer.PgConn().PID()
	if err := writer.Close(ctx); err != nil {
		t.Fatal("owned writer connection cleanup failed")
	}
	joined := false
	for deadline := time.Now().Add(time.Second); time.Now().Before(deadline); {
		var present bool
		if err := db.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1)", writerPID).Scan(&present); err != nil {
			t.Fatal("owned writer cleanup observation failed")
		}
		if !present {
			joined = true
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if !joined {
		t.Fatal("owned database writer backend did not exit")
	}
	if r.Recheck(ctx, f.scope) == nil {
		t.Fatal("writer-invalidated receipt was replayed")
	}
	if _, err := selectedFenceDatabase(ctx, db, f.scope); err != nil {
		t.Fatal(err)
	}
	wrong := f.scope
	wrong.DatabaseSystemID = "999"
	if _, err := selectedFenceDatabase(ctx, db, wrong); err == nil {
		t.Fatal("wrong database cluster selection accepted")
	}
	role := fmt.Sprintf("selected_fence_limited_%d", os.Getpid())
	roleSQL := pgx.Identifier{role}.Sanitize()
	if _, err := admin.Exec(ctx, "CREATE ROLE "+roleSQL+" LOGIN"); err != nil {
		t.Fatal("owned restricted role setup failed")
	}
	defer func() {
		if _, err := admin.Exec(context.Background(), "DROP ROLE "+roleSQL); err != nil {
			t.Error("owned restricted role cleanup failed")
		}
	}()
	limitedConfig := selected.Copy()
	limitedConfig.User = role
	limited, err := pgx.ConnectConfig(ctx, limitedConfig)
	if err != nil {
		t.Fatal("owned restricted connection unavailable")
	}
	if _, err := selectedFenceDatabase(ctx, limited, f.scope); err == nil {
		t.Fatal("restricted activity visibility accepted")
	}
	if err := limited.Close(ctx); err != nil {
		t.Fatal("owned restricted connection cleanup failed")
	}
	var value int
	if err := db.QueryRow(ctx, "SELECT attempts FROM cairn.semantic_job").Scan(&value); err != nil || value != 0 {
		t.Fatal("readonly observer changed semantic_job")
	}
	t.Log("owned_pg=true actual_writer_refused=true unknown_visibility_refused=true readonly=true; no production connection")
}

func TestSelectedUnitFenceCanonicalScopeCopiesInputs(t *testing.T) {
	f := newSelectedFenceFixture(t)
	r := f.capture()
	original := slices.Clone(f.scope.Units)
	f.scope.Units[0] = "caller-changed.service"
	if r.Recheck(context.Background(), SelectedUnitFenceScope{UID: f.scope.UID, Units: original, Executables: f.scope.Executables, Database: f.scope.Database, DatabaseSystemID: f.scope.DatabaseSystemID, Window: f.scope.Window}) != nil {
		t.Fatal("caller slice mutation changed private captured scope")
	}
}

func TestSelectedUnitFenceConcurrentRecheck(t *testing.T) {
	f := newSelectedFenceFixture(t)
	r := f.capture()
	errors := make(chan error, 8)
	for range 8 {
		go func() {
			facts := r.Facts()
			facts["scope"] = "caller-copy"
			errors <- r.Recheck(context.Background(), f.scope)
		}()
	}
	for range 8 {
		if err := <-errors; err != nil {
			t.Fatal(err)
		}
	}
}

func TestSelectedUnitFenceMissingDatabaseFactsAndPrivateEvidence(t *testing.T) {
	f := newSelectedFenceFixture(t)
	f.host.dbRead = func(context.Context, SelectedUnitFenceScope) (map[string]string, error) { return nil, nil }
	if _, err := captureSelectedUnitFence(context.Background(), f.scope, f.host); err == nil {
		t.Fatal("missing database facts became a receipt")
	}
	f = newSelectedFenceFixture(t)
	canary := "unit-private-body-and-argv-canary"
	f.units["selected.service"]["ExecStart"] = canary
	f.write("definition-selected.service", canary)
	r := f.capture()
	raw, err := json.Marshal(r.Facts())
	if err != nil || strings.Contains(string(raw), canary) {
		t.Fatal("private unit body or argv leaked into evidence")
	}
	f.units["selected.service"]["ActiveState"] = "active"
	if err := r.Recheck(context.Background(), f.scope); err == nil || strings.Contains(err.Error(), canary) {
		t.Fatal("private unit data leaked or active service accepted")
	}
}

// Property shapes recorded by the read-only three-host audit
// c9cb91d8-dea9-4ba0-bf35-0635a7b41067/v1. Definition files remain owned fixtures.
func TestSelectedUnitFenceRecordedStoppedProperties(t *testing.T) {
	for _, test := range []struct{ host, unit, load, enabled, restart string }{
		{"proximal", "cairn-wake-agy.service", "loaded", "disabled", "on-failure"},
		{"proximal", "cairn-store.service", "masked", "masked", "no"},
		{"archon", "cairn-presence.service", "loaded", "enabled", "on-failure"},
		{"static", "selected.service", "loaded", "static", "always"},
		{"indirect", "selected.service", "loaded", "indirect", "no"},
		{"runtime-mask", "selected.service", "masked", "masked-runtime", "no"},
	} {
		t.Run(test.host, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			f.unit(test.unit, "/fixture")
			f.scope.Units = []string{test.unit}
			p := f.units[test.unit]
			p["ControlGroup"], p["LoadState"], p["UnitFileState"], p["Restart"] = "", test.load, test.enabled, test.restart
			if test.load == "masked" {
				p["FragmentPath"] = "/dev/null"
				// Actual proximal masked cairn-store omits ExecStart entirely.
				delete(p, "ExecStart")
			}
			r := f.capture()
			if r.Facts()["cgroup:"+test.unit] != "manager-reported-empty" {
				t.Fatal("manager absence was not recorded separately from an inspected cgroup")
			}
			if err := r.Recheck(context.Background(), f.scope); err != nil {
				t.Fatal(err)
			}
			p["MainPID"] = "5"
			if r.Recheck(context.Background(), f.scope) == nil {
				t.Fatal("empty cgroup hid a restarted service")
			}
		})
	}
	for _, field := range []string{"InvocationID", "ControlGroup", "MainPID", "Restart", "ExecStart"} {
		t.Run("missing-"+field, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			delete(f.units["selected.service"], field)
			if _, err := captureSelectedUnitFence(context.Background(), f.scope, f.host); err == nil {
				t.Fatal("missing service fact was treated as manager-reported absence")
			}
		})
	}
}

func TestSelectedUnitFenceMaskedSymlinkIdentity(t *testing.T) {
	for _, mutation := range []string{"none", "replacement", "target", "regular"} {
		t.Run(mutation, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			p := f.units["selected.service"]
			p["LoadState"], p["UnitFileState"], p["ControlGroup"] = "masked", "masked", ""
			mask := filepath.Join(f.root, "mask.service")
			if err := os.Symlink("/dev/null", mask); err != nil {
				t.Fatal(err)
			}
			p["FragmentPath"] = mask
			r := f.capture()
			if !censusDigest(r.Facts()["definition:selected.service"]) {
				t.Fatal("symlink identity was not pinned")
			}
			if mutation != "none" {
				replacement := filepath.Join(f.root, "replacement")
				if mutation == "regular" {
					f.write("replacement", "[Service]\n")
				} else {
					target := "/dev/null"
					if mutation == "target" {
						target = f.scope.Executables[0].Path
					}
					if err := os.Symlink(target, replacement); err != nil {
						t.Fatal(err)
					}
				}
				if err := os.Rename(replacement, mask); err != nil {
					t.Fatal(err)
				}
			}
			err := r.Recheck(context.Background(), f.scope)
			if (err == nil) != (mutation == "none") {
				t.Fatalf("mask identity result for %s: %v", mutation, err)
			}
		})
	}
}

func TestSelectedUnitFenceFreshUnitAndRecursiveTriggerPasses(t *testing.T) {
	for _, unit := range []string{"selected.service", "deep.timer"} {
		for _, phase := range []int{2, 3, 4} {
			for _, field := range []string{"ActiveState", "InvocationID", "StateChangeTimestampMonotonic", "Restart"} {
				t.Run(fmt.Sprintf("%s/pass%d/%s", unit, phase, field), func(t *testing.T) {
					f := newSelectedFenceFixture(t)
					f.unit("selected.timer", "/timer")
					f.unit("deep.timer", "/deep")
					f.units["selected.service"]["TriggeredBy"] = "selected.timer"
					f.units["selected.timer"]["TriggeredBy"] = "deep.timer"
					f.units["deep.timer"]["Triggers"] = "selected.service"
					for _, p := range f.units {
						p["UnitFileState"] = "enabled"
						p["Restart"] = "on-failure"
					}
					query, reads := f.host.query, 0
					old := f.units[unit][field]
					f.host.query = func(ctx context.Context, args ...string) (string, error) {
						if args[0] == "show" && args[len(args)-1] == unit {
							reads++
							if reads == phase {
								f.units[unit][field] = map[string]string{"ActiveState": "active", "InvocationID": strings.Repeat("a", 32), "StateChangeTimestampMonotonic": "101", "Restart": "always"}[field]
							}
						}
						return query(ctx, args...)
					}
					r, err := captureSelectedUnitFence(context.Background(), f.scope, f.host)
					if phase == 2 {
						if err == nil || r != nil {
							t.Fatal("capture ignored changed second observation")
						}
						return
					}
					if err != nil {
						t.Fatal(err)
					}
					if r.Recheck(context.Background(), f.scope) == nil {
						t.Fatal("fresh recheck ignored activation/property/generation change")
					}
					f.units[unit][field] = old
					if r.Recheck(context.Background(), f.scope) == nil {
						t.Fatal("restoring old facts revived invalidated receipt")
					}
				})
			}
		}
	}
}

func TestSelectedUnitFenceTalosEmptyUnitsStillChecksManagerAndProcesses(t *testing.T) {
	f := newSelectedFenceFixture(t)
	f.scope.Units, f.scope.Database, f.scope.DatabaseSystemID = nil, "", ""
	r := f.capture()
	if err := r.Recheck(context.Background(), f.scope); err != nil {
		t.Fatal(err)
	}
	f.process(123, f.scope.UID, f.scope.Executables[0].Path)
	if r.Recheck(context.Background(), f.scope) == nil {
		t.Fatal("zero units skipped executable absence check")
	}
	f = newSelectedFenceFixture(t)
	f.scope.Units = nil
	f.host.query = func(context.Context, ...string) (string, error) { return "", os.ErrPermission }
	if _, err := captureSelectedUnitFence(context.Background(), f.scope, f.host); err == nil {
		t.Fatal("zero units accepted an unreadable manager")
	}
	f = newSelectedFenceFixture(t)
	f.scope.Units = nil
	f.unit("cairn-wake-new.service", "/new")
	f.units["cairn-wake-new.service"]["ActiveState"] = "active"
	f.listed = "cairn-wake-new.service loaded active running\n"
	if _, err := captureSelectedUnitFence(context.Background(), f.scope, f.host); err == nil {
		t.Fatal("zero units skipped transient wake-unit inspection")
	}
	f.scope.Executables = nil
	if _, err := selectedFenceScope(f.scope); err == nil {
		t.Fatal("empty executable selection accepted")
	}
}

func TestSelectedUnitFenceOwnPIDRequiresVerifiedGeneration(t *testing.T) {
	self, err := readProcessStatAt("/proc", os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	for _, which := range []string{"self", "reused-self", "other-pid"} {
		t.Run(which, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			pid, start := os.Getpid(), self.start
			if which == "reused-self" {
				start++
			}
			if which == "other-pid" {
				pid++
			}
			scan := f.process(pid, f.scope.UID, f.scope.Executables[0].Path)
			name := fmt.Sprintf("proc/%d/stat", pid)
			raw, err := os.ReadFile(filepath.Join(f.root, name))
			if err != nil {
				t.Fatal(err)
			}
			f.write(name, strings.TrimSuffix(string(raw), "100\n")+strconv.FormatUint(start, 10)+"\n")
			err = scan.empty(context.Background())
			if (err == nil) != (which == "self") {
				t.Fatalf("own PID exclusion %s: %v", which, err)
			}
		})
	}
}

func TestSelectedUnitFencePermissionExclusionNeedsAllStableNonmatches(t *testing.T) {
	cases := []string{"unrelated", "command", "argv0", "argv0-basename", "comm", "selected-group", "wake-group", "escaped-wake-group", "exe-access-changed", "generation", "uid"}
	for _, field := range []string{"cmdline", "comm", "cgroup"} {
		for _, phase := range []string{"first-unreadable", "last-unreadable", "changed", "empty"} {
			cases = append(cases, field+"/"+phase)
		}
	}
	for _, which := range cases {
		t.Run(which, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			scan := f.process(123, f.scope.UID, f.scope.Executables[0].Path)
			scan.units = []string{"selected.service"}
			f.write("proc/123/cmdline", "/usr/bin/ssh-agent\x00-c\x00")
			f.write("proc/123/comm", "ssh-agent\n")
			f.write("proc/123/cgroup", "0::/user.slice/user-1000.slice/user@1000.service/init.scope\n")
			switch which {
			case "command":
				raw, err := os.ReadFile(filepath.Join(f.host.procRoot, "123/cmdline"))
				if err != nil {
					t.Fatal(err)
				}
				sum := sha256.Sum256(raw)
				scan.commands = []string{hex.EncodeToString(sum[:])}
			case "argv0":
				f.write("proc/123/cmdline", f.scope.Executables[0].Path+"\x00")
			case "argv0-basename":
				f.write("proc/123/cmdline", filepath.Base(f.scope.Executables[0].Path)+"\x00")
			case "comm":
				f.write("proc/123/comm", "selected-execut\n")
			case "selected-group":
				f.write("proc/123/cgroup", "0::/user.slice/selected.service/child\n")
			case "wake-group":
				f.write("proc/123/cgroup", "0::/user.slice/cairn-wake-worker-01.service\n")
			case "escaped-wake-group":
				f.write("proc/123/cgroup", "0::/user.slice/cairn\\x2dwake\\x2dworker\\x2d01.service\n")
			}
			links := 0
			scan.readlink = func(path string) (string, error) {
				links++
				if which == "exe-access-changed" && links == 2 {
					return os.Readlink(path)
				}
				return "", os.ErrPermission
			}
			counts := map[string]int{}
			scan.read = func(path string) ([]byte, error) {
				name := filepath.Base(path)
				counts[name]++
				field, phase, _ := strings.Cut(which, "/")
				if name == field {
					switch phase {
					case "first-unreadable":
						if counts[name] == 1 {
							return nil, os.ErrPermission
						}
					case "last-unreadable":
						if counts[name] == 2 {
							return nil, os.ErrPermission
						}
					case "changed":
						if counts[name] == 2 {
							return []byte("changed\n"), nil
						}
					case "empty":
						return nil, nil
					}
				}
				if which == "generation" && name == "cmdline" && counts[name] == 2 {
					raw, err := os.ReadFile(filepath.Join(f.host.procRoot, "123/stat"))
					if err != nil {
						t.Fatal(err)
					}
					f.write("proc/123/stat", strings.TrimSuffix(string(raw), "100\n")+"101\n")
				}
				if which == "uid" && name == "status" && counts[name] == 2 {
					return []byte("Uid:\t9999\t9999\t9999\t9999\n"), nil
				}
				return os.ReadFile(path)
			}
			matched, err := scan.process(123)
			if which == "unrelated" {
				if err != nil || matched {
					t.Fatalf("stable unrelated non-dumpable process refused: %v", err)
				}
				for _, field := range []string{"cmdline", "comm", "cgroup", "status"} {
					if counts[field] != 2 {
						t.Fatalf("%s did not receive two actual reads", field)
					}
				}
			} else if err == nil {
				t.Fatal("possible match or unstable/unreadable process excluded")
			}
		})
	}
}

func TestSelectedUnitFenceZombieNeedsStableCommAndGroup(t *testing.T) {
	for _, state := range []string{"Z", "X"} {
		for _, which := range []string{"unrelated", "comm", "selected-group", "wake-group", "comm-changed", "group-changed", "comm-unreadable", "group-unreadable"} {
			t.Run(state+"/"+which, func(t *testing.T) {
				f := newSelectedFenceFixture(t)
				scan := f.process(123, f.scope.UID, f.scope.Executables[0].Path)
				scan.units = []string{"selected.service"}
				raw, err := os.ReadFile(filepath.Join(f.host.procRoot, "123/stat"))
				if err != nil {
					t.Fatal(err)
				}
				f.write("proc/123/stat", strings.Replace(string(raw), ") S ", ") "+state+" ", 1))
				f.write("proc/123/comm", "xdg-open\n")
				f.write("proc/123/cmdline", "")
				switch which {
				case "comm":
					f.write("proc/123/comm", "selected-execut\n")
				case "selected-group":
					f.write("proc/123/cgroup", "0::/selected.service\n")
				case "wake-group":
					f.write("proc/123/cgroup", "0::/cairn-wake-worker-01.service\n")
				}
				scan.readlink = func(string) (string, error) {
					t.Fatal("zombie exclusion followed executable")
					return "", os.ErrNotExist
				}
				counts := map[string]int{}
				scan.read = func(path string) ([]byte, error) {
					name := filepath.Base(path)
					counts[name]++
					if name == "comm" && strings.HasPrefix(which, "comm-") || name == "cgroup" && strings.HasPrefix(which, "group-") {
						if strings.HasSuffix(which, "unreadable") {
							return nil, os.ErrPermission
						}
						if counts[name] == 2 {
							return []byte("changed\n"), nil
						}
					}
					return os.ReadFile(path)
				}
				matched, err := scan.process(123)
				if which == "unrelated" {
					if err != nil || matched || counts["comm"] != 2 || counts["cgroup"] != 2 {
						t.Fatalf("stable unrelated zombie exclusion: %v", err)
					}
				} else if err == nil {
					t.Fatal("possible selected/unstable zombie excluded")
				}
			})
		}
	}
}

func TestSelectedUnitFenceProcessDisappearanceNeedsAbsentDirectory(t *testing.T) {
	for _, which := range []string{"ENOENT-absent", "ESRCH-absent", "ENOENT-existing", "ESRCH-existing", "permission-absent", "io-absent", "after-status", "after-exe", "missing-field"} {
		t.Run(which, func(t *testing.T) {
			f := newSelectedFenceFixture(t)
			scan := f.process(123, f.scope.UID, f.scope.Executables[0].Path)
			base := filepath.Join(f.host.procRoot, "123")
			scan.read = func(path string) ([]byte, error) {
				if which == "missing-field" && filepath.Base(path) == "cmdline" {
					return nil, os.ErrNotExist
				}
				if filepath.Base(path) != "status" || which == "after-exe" {
					return os.ReadFile(path)
				}
				if strings.HasSuffix(which, "absent") || which == "after-status" {
					raw, err := os.ReadFile(path)
					if err != nil {
						t.Fatal(err)
					}
					if err := os.RemoveAll(base); err != nil {
						t.Fatal(err)
					}
					if which == "after-status" {
						return raw, nil
					}
				}
				switch {
				case strings.HasPrefix(which, "ENOENT"):
					return nil, os.ErrNotExist
				case strings.HasPrefix(which, "ESRCH"):
					return nil, syscall.ESRCH
				case which == "permission-absent":
					return nil, os.ErrPermission
				case which == "io-absent":
					return nil, syscall.EIO
				}
				return os.ReadFile(path)
			}
			if which == "after-exe" {
				scan.readlink = func(string) (string, error) {
					if err := os.RemoveAll(base); err != nil {
						t.Fatal(err)
					}
					return "", syscall.ESRCH
				}
			}
			matched, err := scan.process(123)
			absent := which == "ENOENT-absent" || which == "ESRCH-absent" || which == "after-status" || which == "after-exe"
			if matched || (err == nil) != absent {
				t.Fatalf("absence must require disappearing PID and absent directory (%s): %v", which, err)
			}
		})
	}
}

func TestSelectedUnitFenceProximalReadOnly(t *testing.T) {
	if os.Getenv("CAIRN_SELECTED_FENCE_LIVE_READONLY") != "1" {
		t.Skip("NOT RUN: explicit proximal read-only evidence gate is not selected")
	}
	hostname, err := os.Hostname()
	if err != nil || hostname != "proximal" {
		t.Skip("NOT RUN: this evidence step is admitted only on proximal")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	self, err := readProcessStatAt("/proc", os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	exe, err := os.Readlink(fmt.Sprintf("/proc/%d/exe", os.Getpid()))
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(fmt.Sprintf("/proc/%d/exe", os.Getpid()))
	if err != nil {
		t.Fatal(err)
	}
	identity := info.Sys().(*syscall.Stat_t)
	scan := selectedFenceProcScan{root: "/proc", uid: uint32(os.Getuid()), selfStart: self.start,
		executables: []selectedFenceExecutable{{exe, identity.Dev, identity.Ino}}, read: os.ReadFile}
	t.Run("own-generation", func(t *testing.T) {
		if matched, err := scan.process(os.Getpid()); err != nil || matched {
			t.Fatalf("verified own generation was not excluded: %v", err)
		}
		t.Log("actual_proc=true self_generation_verified=true; no process action")
	})
	for _, category := range []string{"non-dumpable", "unrelated-zombie"} {
		t.Run(category, func(t *testing.T) {
			entries, err := os.ReadDir("/proc")
			if err != nil {
				t.Fatal(err)
			}
			for _, entry := range entries {
				pid, err := strconv.Atoi(entry.Name())
				if err != nil || pid <= 0 || pid == os.Getpid() {
					continue
				}
				base := filepath.Join("/proc", entry.Name())
				raw, err := os.ReadFile(filepath.Join(base, "status"))
				if err != nil {
					continue
				}
				uids, err := selectedFenceUID(raw)
				if err != nil || !slices.Contains(uids[:], scan.uid) {
					continue
				}
				raw, err = os.ReadFile(filepath.Join(base, "comm"))
				if err != nil {
					continue
				}
				comm := strings.TrimSuffix(string(raw), "\n")
				state, err := readProcessStatAt("/proc", pid)
				if err != nil {
					continue
				}
				if category == "non-dumpable" {
					if !slices.Contains([]string{"systemd", "(sd-pam)", "ssh-agent"}, comm) || state.state == "Z" || state.state == "X" {
						continue
					}
					if _, err := os.Readlink(filepath.Join(base, "exe")); !errors.Is(err, os.ErrPermission) {
						continue
					}
				} else if !slices.Contains([]string{"xdg-open", "uwsm-app"}, comm) || (state.state != "Z" && state.state != "X") {
					continue
				}
				if matched, err := scan.process(pid); err != nil || matched {
					t.Fatalf("selected read-only %s candidate refused, pid=%d: %v", category, pid, err)
				}
				t.Logf("actual_proc=true category=%s pid=%d stable_nonmatch=true; no process action", category, pid)
				return
			}
			t.Skip("NOT RUN: no current audited-class process available for " + category)
		})
	}
	host := &selectedFenceHost{}
	for _, name := range []string{"cairn-wake-agy.service", "cairn-store.service", "agent-usage-alert.service"} {
		t.Run(name, func(t *testing.T) {
			raw, err := host.systemd(ctx, "show", "--all", "--property="+strings.Join(selectedFenceProperties, ","), name)
			if err != nil {
				t.Fatal(err)
			}
			properties, err := selectedFenceParseProperties(raw)
			if err != nil {
				t.Fatal(err)
			}
			if properties["LoadState"] == "not-found" || properties["ActiveState"] != "inactive" || properties["SubState"] != "dead" {
				t.Skip("NOT RUN: candidate is not an already-inactive installed service")
			}
			if err := selectedFenceStoppedUnit(name, properties); err != nil {
				t.Fatal(err)
			}
			definition, err := selectedFenceDefinition(properties["FragmentPath"], properties["LoadState"])
			if err != nil || definition == "" {
				t.Fatalf("actual stopped-unit definition refused: %v", err)
			}
			if group := properties["ControlGroup"]; group != "" {
				if _, err := selectedFenceGroup("/sys/fs/cgroup", group); err != nil {
					t.Fatal(err)
				}
			}
			again, err := host.systemd(ctx, "show", "--all", "--property="+strings.Join(selectedFenceProperties, ","), name)
			if err != nil {
				t.Fatal(err)
			}
			last, err := selectedFenceParseProperties(again)
			if err != nil || selectedFenceHash(last) != selectedFenceHash(properties) {
				t.Fatal("live stopped-unit properties changed during explicit evidence step")
			}
			lastDefinition, err := selectedFenceDefinition(last["FragmentPath"], last["LoadState"])
			if err != nil || lastDefinition != definition {
				t.Fatal("live stopped-unit definition changed during explicit evidence step")
			}
			t.Logf("actual_manager=true inactive_dead=true MainPID=0 empty_cgroup=%t load=%s enabled=%s restart=%s; read-only component evidence, not full Capture", properties["ControlGroup"] == "", properties["LoadState"], properties["UnitFileState"], properties["Restart"])
		})
	}
}
