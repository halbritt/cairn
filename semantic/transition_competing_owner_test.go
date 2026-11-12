package semantic

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/google/uuid"
)

type transitionCompetingOwner struct {
	state       transitionCompetingState
	config      map[string]any
	configPath  string
	configSHA   string
	application string
	dsn         string
	worker      transitionRealWorker
	cmd         *exec.Cmd
	done        chan struct{}
	waitErr     error
	children    []int
	joinedAt    time.Time
	stopAttempt bool
}

func transitionCompetingOwnerConfig(t *testing.T, d *transitionDB, directory, name, collection, database, cliSHA string, worker transitionRealWorker) *transitionCompetingOwner {
	t.Helper()
	home, err := os.MkdirTemp("/tmp", "case8-api-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(home); err != nil {
			t.Error(err)
		}
	})
	identities := filepath.Join(home, "identities.json")
	token := "disposable-case8-" + uuid.NewString()
	identity := []map[string]string{{"token_sha256": transitionHash(token), "principal": "case8-" + name, "repo": collection, "role": "agent", "destination": "local"}}
	if err := os.WriteFile(identities, []byte(transitionJSON(identity)), 0600); err != nil {
		t.Fatal(err)
	}
	u, err := url.Parse(d.dsn)
	if err != nil {
		t.Fatal(err)
	}
	application := "case8-" + name
	q := u.Query()
	q.Set("application_name", application)
	u.RawQuery = q.Encode()
	cli := os.Getenv("CAIRN_TRANSITION_CLI")
	config := map[string]any{
		"name": name, "collection_configuration": collection, "database": database,
		"database_identity": transitionHash(d.dsn), "selected_dsn_sha256": transitionHash(u.String()),
		"application_name": application, "program": cli, "program_sha256": cliSHA,
		"argv": []string{"serve", "--socket", filepath.Join(home, "api.sock"), "--identities", identities,
			"--machine-id", "case8-fixture", "--embedding-command", worker.Embed},
		"home": home, "identities_sha256": transitionHash(transitionJSON(identity)),
		"rank": worker.Rank, "rank_identity": worker.RankIdentity,
		"embed": worker.Embed, "embed_identity": worker.EmbedIdentity, "launchers_sha256": worker.LauncherSHA,
		"mode": "embed", "start_path": "transitionCompetingOwner.start",
		"activation":     "test-owned registered start method and physical configuration; not an installed unit",
		"stop_owner_pid": os.Getpid(), "scope": "closed disposable fixture inventory only",
	}
	data := []byte(transitionJSON(config) + "\n")
	path := filepath.Join(directory, name+".json")
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	owner := &transitionCompetingOwner{
		state:  transitionCompetingState{Name: name, Collection: collection, Model: worker.EmbedIdentity, Joined: true, Reaped: true},
		config: config, configPath: path, configSHA: transitionHash(string(data)),
		application: application, dsn: u.String(), worker: worker,
	}
	t.Cleanup(func() {
		if owner.state.Started && !owner.state.Joined && !owner.stopAttempt {
			if err := owner.stop(); err != nil {
				t.Error(err)
			}
		}
	})
	return owner
}

func (o *transitionCompetingOwner) start(t *testing.T) {
	t.Helper()
	if !o.state.Restartable || (o.state.Started && (!o.state.Joined || !o.state.Reaped)) {
		t.Fatalf("definition %s is disabled or still active", o.state.Name)
	}
	data, err := os.ReadFile(o.configPath)
	if err != nil || transitionHash(string(data)) != o.configSHA || transitionHash(transitionJSON(o.config)+"\n") != o.configSHA {
		t.Fatalf("writer configuration changed: %s: %v", o.state.Name, err)
	}
	args := o.config["argv"].([]string)
	log, err := os.OpenFile(filepath.Join(os.Getenv("CAIRN_TRANSITION_REPORT_DIR"), "case8-owner-"+o.state.Name+".log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { log.Close() })
	o.cmd = exec.Command(o.config["program"].(string), args...)
	for _, entry := range os.Environ() {
		if !strings.HasPrefix(entry, "CAIRN_HOME=") && !strings.HasPrefix(entry, "CAIRN_DATABASE_URL=") {
			o.cmd.Env = append(o.cmd.Env, entry)
		}
	}
	o.cmd.Env = append(o.cmd.Env, "CAIRN_HOME="+o.config["home"].(string), "CAIRN_DATABASE_URL="+o.dsn)
	o.cmd.Stdout, o.cmd.Stderr = log, log
	if err := o.cmd.Start(); err != nil {
		t.Fatal(err)
	}
	o.state.Started, o.state.Joined, o.state.Reaped = true, false, false
	o.stopAttempt, o.children, o.joinedAt = false, nil, time.Time{}
	o.state.PID = o.cmd.Process.Pid
	o.done = make(chan struct{})
	go func() { o.waitErr = o.cmd.Wait(); close(o.done) }()
	if o.state.PID <= 1 {
		t.Fatal("unsafe created API owner PID")
	}
	image, err := os.ReadFile(fmt.Sprintf("/proc/%d/exe", o.state.PID))
	if err != nil || transitionHash(string(image)) != o.config["program_sha256"] {
		t.Fatalf("actual API image differs from configured test binary: %v", err)
	}
}

func (o *transitionCompetingOwner) stop() error {
	if !o.state.Started || o.state.Joined {
		return nil
	}
	o.stopAttempt = true
	if o.state.PID <= 1 || o.cmd == nil || o.cmd.Process.Pid != o.state.PID {
		return fmt.Errorf("refusing unsafe fixture API stop: %s", o.state.Name)
	}
	if err := o.cmd.Process.Signal(syscall.SIGTERM); err != nil && err != os.ErrProcessDone {
		return err
	}
	select {
	case <-o.done:
		if o.waitErr != nil {
			return fmt.Errorf("API owner %s exited unsuccessfully: %w", o.state.Name, o.waitErr)
		}
	case <-time.After(10 * time.Second):
		if err := o.cmd.Process.Kill(); err != nil && err != os.ErrProcessDone {
			return err
		}
		select {
		case <-o.done:
		case <-time.After(2 * time.Second):
			return fmt.Errorf("owned API %s did not reap after direct-child kill", o.state.Name)
		}
		return fmt.Errorf("owned API %s exceeded the joined shutdown bound", o.state.Name)
	}
	for _, pid := range append(append([]int(nil), o.children...), o.state.PID) {
		if pid <= 1 {
			return fmt.Errorf("unsafe owned reap PID %d", pid)
		}
		if _, err := os.Stat(fmt.Sprintf("/proc/%d", pid)); !os.IsNotExist(err) {
			return fmt.Errorf("owned process %d remains after API join: %v", pid, err)
		}
	}
	o.state.Joined, o.state.Reaped, o.joinedAt = true, true, time.Now()
	return nil
}

func (o *transitionCompetingOwner) sampledChildren(t *testing.T, selected transitionRealSelection, real bool) bool {
	t.Helper()
	select {
	case <-o.done:
		t.Fatalf("API owner %s exited before observation: %v", o.state.Name, o.waitErr)
	default:
	}
	entries, err := os.ReadDir("/proc")
	if err != nil {
		t.Fatal(err)
	}
	var children []int
	for _, entry := range entries {
		pid, err := strconv.Atoi(entry.Name())
		if err != nil || pid <= 1 {
			continue
		}
		parent, _, err := transitionProcessStatus(pid)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			t.Fatal(err)
		}
		if parent == o.state.PID {
			children = append(children, pid)
		}
	}
	if len(children) != 2 {
		return false
	}
	if real {
		data, err := os.ReadFile(selected.SamplePIDs)
		if os.IsNotExist(err) {
			return false
		}
		if err != nil {
			t.Fatal(err)
		}
		var ready map[string][]int
		if err = json.Unmarshal(data, &ready); err != nil {
			t.Fatal(err)
		}
		for _, pid := range children {
			found := false
			for _, list := range ready {
				for _, sampled := range list {
					found = found || pid == sampled
				}
			}
			if !found {
				return false
			}
		}
	}
	sort.Ints(children)
	o.children = children
	return true
}

func (o *transitionCompetingOwner) publicationWait(t *testing.T, d *transitionDB) bool {
	t.Helper()
	select {
	case <-o.done:
		t.Fatalf("API owner %s exited before publication observation: %v", o.state.Name, o.waitErr)
	default:
	}
	return d.count(t, `SELECT count(*) FROM pg_stat_activity a
 WHERE a.datname=current_database() AND a.application_name=$1 AND a.state='active'
 AND EXISTS(SELECT 1 FROM pg_locks l WHERE l.pid=a.pid AND l.locktype='advisory' AND NOT l.granted)`, o.application) == 1
}

func (o *transitionCompetingOwner) noBackendCalls(t *testing.T, d *transitionDB) bool {
	t.Helper()
	return d.count(t, `SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND application_name=$1`, o.application) == 0
}

func transitionCompetingDatabaseName(t *testing.T, d *transitionDB) string {
	t.Helper()
	var database string
	if err := d.pool.QueryRow(context.Background(), `SELECT current_database()`).Scan(&database); err != nil {
		t.Fatal(err)
	}
	return database
}
