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

// CLI tests call run as well as opening explicit fixture stores. Both routes
// must select the test database before any test can reach the ordinary CLI.
func TestMain(m *testing.M) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	directory := ""
	if dsn == "" {
		var err error
		directory, err = os.MkdirTemp("", "cairn-cli-test-")
		if err != nil {
			fmt.Fprintln(os.Stderr, "cannot create CLI test isolation directory:", err)
			os.Exit(2)
		}
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
	if directory != "" {
		if err := os.RemoveAll(directory); err != nil {
			fmt.Fprintln(os.Stderr, "cannot remove CLI test isolation directory:", err)
			if code == 0 {
				code = 2
			}
		}
	}
	os.Exit(code)
}

func TestCLIStoreIsolationAtProcessStart(t *testing.T) {
	for _, mode := range []string{"explicit", "blocked"} {
		t.Run(mode, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			command := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestCLIStoreIsolationChild$")
			for _, entry := range os.Environ() {
				if !strings.HasPrefix(entry, "CAIRN_DATABASE_URL=") && !strings.HasPrefix(entry, "CAIRN_TEST_DATABASE_URL=") && !strings.HasPrefix(entry, "CAIRN_CLI_ISOLATION_PROBE=") {
					command.Env = append(command.Env, entry)
				}
			}
			// An inherited ordinary DSN is deliberately different. The child never
			// connects to it, even when integration tests have no database selected.
			command.Env = append(command.Env, "CAIRN_DATABASE_URL=host=/absent-cli-inherited dbname=must_not_be_used sslmode=disable", "CAIRN_CLI_ISOLATION_PROBE="+mode)
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
		if overridden, err := databaseURL(); err != nil || overridden != os.Getenv("CAIRN_DATABASE_URL") {
			t.Fatal("test-specific database override was ignored")
		}
	})
	if restored, err := databaseURL(); err != nil || restored != dsn {
		t.Fatal("test-specific database override was not restored")
	}
}
