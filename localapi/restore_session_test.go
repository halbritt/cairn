package localapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func TestRestoreSessionBlocksWarmAndNewAPIConsumers(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	host, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(host.Close)
	database := "cairn_admission_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err = host.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{database}.Sanitize()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := host.Exec(ctx, "DROP DATABASE "+pgx.Identifier{database}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Error(err)
		}
	})
	restoreDSN := dsn
	if strings.HasPrefix(dsn, "postgres://") || strings.HasPrefix(dsn, "postgresql://") {
		u, err := url.Parse(dsn)
		if err != nil {
			t.Fatal(err)
		}
		u.Path = "/" + database
		u.RawPath = ""
		q := u.Query()
		q.Del("dbname")
		u.RawQuery = q.Encode()
		restoreDSN = u.String()
	} else {
		restoreDSN += " dbname=" + database
	}
	check, err := pgx.Connect(ctx, restoreDSN)
	if err != nil {
		t.Fatal(err)
	}
	var connected string
	readErr := check.QueryRow(ctx, `SELECT current_database()`).Scan(&connected)
	closeErr := check.Close(ctx)
	if readErr != nil || closeErr != nil || connected != database {
		t.Fatalf("isolated fixture target mismatch: database=%s read=%v close=%v", connected, readErr, closeErr)
	}
	admin, err := core.Open(ctx, restoreDSN, core.Channel{Principal: "restore-api:operator", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(admin.Close)
	if err = admin.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err = admin.Bootstrap(ctx, core.BootstrapRequest{RequestID: uuid.NewString(), Reason: "Bootstrap isolated API admission fixture"}); err != nil {
		t.Fatal(err)
	}
	token := "synthetic-restore-api-token"
	digest := sha256.Sum256([]byte(token))
	repo := uuid.NewString()
	identities := []Identity{{TokenSHA256: hex.EncodeToString(digest[:]), Principal: "restore-api:agent", Repo: repo, Role: "agent", Destination: "local"}}
	warm, err := New(ctx, restoreDSN, identities)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(warm.Close)
	request := core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "Private API admission fixture", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}}
	call := func(server *Server, path string, value any) (int, map[string]any) {
		t.Helper()
		body, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest("POST", path, bytes.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+token)
		w := httptest.NewRecorder()
		server.ServeHTTP(w, req)
		var reply map[string]any
		if err = json.Unmarshal(w.Body.Bytes(), &reply); err != nil {
			t.Fatal(err)
		}
		return w.Code, reply
	}
	status, reply := call(warm, "/v1/create", request)
	if status != 200 {
		t.Fatalf("fixture: %v", reply)
	}
	id := reply["data"].(map[string]any)["record_id"]
	if _, err = admin.BeginRestore(ctx, core.BeginRestoreRequest{RequestID: uuid.NewString(), Target: "older-api-backup", Reason: "Pause isolated API fixture before reconciliation"}); err != nil {
		t.Fatal(err)
	}
	cold, err := New(ctx, restoreDSN, identities)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(cold.Close)
	for _, server := range []*Server{warm, cold} {
		for _, item := range []struct {
			path  string
			value any
		}{{"/v1/create", request}, {"/v1/get", map[string]any{"record_id": id}}, {"/v1/compile", core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "t", RunID: "r"}, Purpose: "context", AvailableTokens: 64000}}} {
			status, reply := call(server, item.path, item.value)
			if status != 503 || reply["status"] != "RESTORE_PAUSED" {
				t.Fatalf("%s served paused data: status=%d reply=%v", item.path, status, reply)
			}
		}
		status, _ := call(server, "/v1/resume-restore", map[string]any{})
		if status != 404 {
			t.Fatalf("agent API exposed admission: %d", status)
		}
	}
}
