package main

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5"
)

// CLI tests call run as well as opening explicit fixture stores. Database,
// default API socket and token paths must all stay inside test-owned state.
func TestMain(m *testing.M) {
	// Nest test-binary subprocesses under their parent's directory so even a
	// deliberate os.Exit in a helper is cleaned up by the outer test process.
	directory, err := os.MkdirTemp(os.Getenv("CAIRN_CLI_TEST_TEMP_ROOT"), "cairn-cli-test-")
	if err != nil {
		fmt.Fprintln(os.Stderr, "cannot create CLI test isolation directory:", err)
		os.Exit(2)
	}
	for name, value := range map[string]string{"CAIRN_HOME": directory, "CAIRN_CLI_TEST_TEMP_ROOT": directory} {
		if err := os.Setenv(name, value); err != nil {
			fmt.Fprintln(os.Stderr, "cannot select isolated CLI test home:", err)
			_ = os.RemoveAll(directory)
			os.Exit(2)
		}
	}
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		dsn = (&url.URL{Scheme: "postgresql", Path: "/cairn_test_blocked", RawQuery: url.Values{
			"host": {filepath.Join(directory, "absent")}, "sslmode": {"disable"}, "connect_timeout": {"1"},
		}.Encode()}).String()
	}
	if err := os.Setenv("CAIRN_DATABASE_URL", dsn); err != nil {
		fmt.Fprintln(os.Stderr, "cannot select isolated CLI test database:", err)
		_ = os.RemoveAll(directory)
		os.Exit(2)
	}
	code := m.Run()
	if err := os.RemoveAll(directory); err != nil {
		fmt.Fprintln(os.Stderr, "cannot remove CLI test isolation directory:", err)
		if code == 0 {
			code = 2
		}
	}
	os.Exit(code)
}

func TestCLIStoreIsolationAtProcessStart(t *testing.T) {
	for _, mode := range []string{"explicit", "blocked"} {
		t.Run(mode, func(t *testing.T) {
			inheritedHome := t.TempDir()
			if err := os.WriteFile(filepath.Join(inheritedHome, "agent.token"), []byte("inherited-test-token"), 0600); err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			command := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestCLIStoreIsolationChild$")
			for _, entry := range os.Environ() {
				if !strings.HasPrefix(entry, "CAIRN_DATABASE_URL=") && !strings.HasPrefix(entry, "CAIRN_TEST_DATABASE_URL=") && !strings.HasPrefix(entry, "CAIRN_CLI_ISOLATION_PROBE=") && !strings.HasPrefix(entry, "CAIRN_HOME=") {
					command.Env = append(command.Env, entry)
				}
			}
			// An inherited ordinary DSN is deliberately different. The child never
			// connects to it, even when integration tests have no database selected.
			command.Env = append(command.Env, "CAIRN_DATABASE_URL=host=/absent-cli-inherited dbname=must_not_be_used sslmode=disable", "CAIRN_CLI_ISOLATION_PROBE="+mode, "CAIRN_HOME="+inheritedHome, "CAIRN_CLI_ISOLATION_EXPECTED_USER_HOME="+os.Getenv("HOME"))
			if mode == "explicit" {
				command.Env = append(command.Env, "CAIRN_TEST_DATABASE_URL=host=/absent-cli-explicit dbname=selected_test sslmode=disable")
			}
			if output, err := command.CombinedOutput(); err != nil {
				t.Fatalf("isolated child failed: %v\n%s", err, output)
			}
		})
	}
}

func TestCLIStoreIsolationChild(t *testing.T) {
	mode := os.Getenv("CAIRN_CLI_ISOLATION_PROBE")
	if mode == "" {
		return
	}
	directory, err := dataDirectory()
	if err != nil {
		t.Fatal(err)
	}
	if directory != os.Getenv("CAIRN_CLI_TEST_TEMP_ROOT") || !strings.HasPrefix(filepath.Base(directory), "cairn-cli-test-") || os.Getenv("HOME") != os.Getenv("CAIRN_CLI_ISOLATION_EXPECTED_USER_HOME") {
		t.Fatal("CLI home was not isolated independently of the user home")
	}
	for _, name := range []string{"agent.token", "api.sock"} {
		if _, err := os.Stat(filepath.Join(directory, name)); !os.IsNotExist(err) {
			t.Fatalf("default test %s unexpectedly exists", name)
		}
	}
	profileToken, err := agentProfileToken("isolation-probe", "")
	if err != nil || profileToken != filepath.Join(directory, "event-profiles", "isolation-probe.token") {
		t.Fatal("default profile token escaped the isolated CLI home")
	}
	_, err = run(context.Background(), []string{"agent", "get"}, strings.NewReader(`{"record_id":"00000000-0000-0000-0000-000000000000"}`))
	if core.Code(err) != "CLIENT_SETUP_FAILED" {
		t.Fatalf("default agent route did not refuse its missing test token: %v", err)
	}
	dsn, err := databaseURL()
	if err != nil {
		t.Fatal(err)
	}
	if mode == "explicit" {
		if dsn != os.Getenv("CAIRN_TEST_DATABASE_URL") {
			t.Fatal("CLI did not select the explicit test database")
		}
	} else {
		config, err := pgx.ParseConfig(dsn)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.HasPrefix(filepath.Base(filepath.Dir(config.Host)), "cairn-cli-test-") || len(config.Fallbacks) != 0 {
			t.Fatal("CLI did not select its private absent socket without fallback")
		}
		if _, err := os.Stat(config.Host); !os.IsNotExist(err) {
			t.Fatal("blocked CLI socket directory must not exist")
		}
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_, err = run(ctx, []string{"work", "features", "status"}, strings.NewReader(""))
		if core.Code(err) != "STORE_UNREACHABLE" {
			t.Fatalf("database command did not fail closed: %v", err)
		}
	}
	t.Run("per_test_override", func(t *testing.T) {
		t.Setenv("CAIRN_DATABASE_URL", "host=/absent-cli-per-test dbname=override sslmode=disable")
		overrideHome := t.TempDir()
		t.Setenv("CAIRN_HOME", overrideHome)
		if overridden, err := databaseURL(); err != nil || overridden != os.Getenv("CAIRN_DATABASE_URL") {
			t.Fatal("test-specific database override was ignored")
		}
		if overridden, err := dataDirectory(); err != nil || overridden != overrideHome {
			t.Fatal("test-specific CLI home override was ignored")
		}
	})
	if restored, err := databaseURL(); err != nil || restored != dsn {
		t.Fatal("test-specific database override was not restored")
	}
	if restored, err := dataDirectory(); err != nil || restored != directory {
		t.Fatal("test-specific CLI home override was not restored")
	}
}
