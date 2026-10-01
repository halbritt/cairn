package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5"
)

// TestAdoptContextMainHelper runs the real main in a child process so exit codes
// and envelopes are the ones an operator sees.
func TestAdoptContextMainHelper(t *testing.T) {
	raw := os.Getenv("CAIRN_TEST_MAIN_ARGS")
	if raw == "" {
		t.Skip("helper process only")
	}
	var args []string
	if err := json.Unmarshal([]byte(raw), &args); err != nil {
		t.Fatal(err)
	}
	os.Args = append([]string{"cairn"}, args...)
	main()
}

type envelope struct {
	Status  string          `json:"status"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
}

func operatorProcess(t *testing.T, args ...string) (int, envelope) {
	t.Helper()
	encoded, _ := json.Marshal(args)
	cmd := exec.Command(os.Args[0], "-test.run=^TestAdoptContextMainHelper$")
	cmd.Env = append(os.Environ(), "CAIRN_TEST_MAIN_ARGS="+string(encoded))
	var stdout bytes.Buffer
	cmd.Stdout = &stdout
	err := cmd.Run()
	code := 0
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		code = exit.ExitCode()
	} else if err != nil {
		t.Fatal(err)
	}
	var reply envelope
	if err = json.Unmarshal(stdout.Bytes(), &reply); err != nil {
		t.Fatalf("no envelope from %v: %q", args, stdout.String())
	}
	return code, reply
}

func disposableDatabase(t *testing.T) string {
	t.Helper()
	base := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if base == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	host, err := pgx.Connect(ctx, base)
	if err != nil {
		t.Fatal(err)
	}
	name := "cairn_adopt_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if _, err = host.Exec(ctx, "CREATE DATABASE "+pgx.Identifier{name}.Sanitize()); err != nil {
		host.Close(ctx)
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := host.Exec(ctx, "DROP DATABASE "+pgx.Identifier{name}.Sanitize()+" WITH (FORCE)"); err != nil {
			t.Error(err)
		}
		host.Close(ctx)
	})
	dsn := base + " dbname=" + name
	if strings.HasPrefix(base, "postgres://") || strings.HasPrefix(base, "postgresql://") {
		u, err := url.Parse(base)
		if err != nil {
			t.Fatal(err)
		}
		u.Path = "/" + name
		dsn = u.String()
	}
	return dsn
}

func TestAdoptContextCommandAdoptsVerifiesAndReportsLikeAnOperator(t *testing.T) {
	ctx := context.Background()
	dsn := disposableDatabase(t)
	t.Setenv("CAIRN_DATABASE_URL", dsn)
	if _, err := run(ctx, []string{"migrate"}, nil); err != nil {
		t.Fatal(err)
	}
	// The invoking OS user is the principal that owns runs made with the same CLI.
	channel := core.Channel{Principal: "local-uid:" + strconv.Itoa(os.Geteuid()), Operator: true, Instrumented: true}
	store, err := core.Open(ctx, dsn, channel)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	root, err := store.Bootstrap(ctx, core.BootstrapRequest{RequestID: uuid.NewString(), Reason: "Bootstrap isolated historical adoption fixture"})
	if err != nil {
		t.Fatal(err)
	}
	repo := uuid.NewString()
	record, err := store.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "historical adoption fixture", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}})
	if err != nil {
		t.Fatal(err)
	}
	finished := func(label string) (core.Package, string) {
		pkg, err := store.Compile(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: label}, Purpose: "context", AvailableTokens: 64000}, core.Destination{Name: "local", AllowLocal: true})
		if err != nil {
			t.Fatal(err)
		}
		if err = store.ClaimRun(ctx, pkg.ReceiptID); err != nil {
			t.Fatal(err)
		}
		zero := 0
		if _, err = store.RecordOutcome(ctx, core.OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: pkg.ReceiptID, ExitCode: &zero, DurationMS: 5, ProcessState: "exited", StdoutSHA256: strings.Repeat("a", 64), StderrSHA256: strings.Repeat("b", 64)}); err != nil {
			t.Fatal(err)
		}
		parent, err := filepath.EvalSymlinks(t.TempDir())
		if err != nil {
			t.Fatal(err)
		}
		dir := filepath.Join(parent, pkg.ReceiptID)
		rendered, err := pkg.Render()
		if err != nil {
			t.Fatal(err)
		}
		if err = os.Mkdir(dir, 0700); err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(filepath.Join(dir, "context.txt"), []byte(rendered), 0600); err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(filepath.Join(dir, "outcome.json"), []byte("{}"), 0600); err != nil {
			t.Fatal(err)
		}
		return pkg, dir
	}
	pkg, dir := finished("historical")
	_, wrongDir := finished("edited")
	if err = os.WriteFile(filepath.Join(wrongDir, "context.txt"), []byte("edited by hand"), 0600); err != nil {
		t.Fatal(err)
	}
	wrongReceipt := filepath.Base(wrongDir)

	for _, args := range [][]string{{"adopt-context"}, {"adopt-context", pkg.ReceiptID}, {"adopt-context", pkg.ReceiptID, dir, "extra"}} {
		if _, err := run(ctx, args, nil); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%v: %v", args, err)
		}
	}
	code, refused := operatorProcess(t, "adopt-context", wrongReceipt, wrongDir)
	if code != 4 || refused.Status != "ARTIFACT_MISMATCH" || !strings.Contains(refused.Message, "retained canonical package") {
		t.Fatalf("an unverifiable file must exit 4 with its reason: %d %+v", code, refused)
	}
	if _, err = os.Lstat(filepath.Join(wrongDir, ".cairn-context-owner")); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("a refused adoption marked the directory")
	}

	code, adopted := operatorProcess(t, "adopt-context", pkg.ReceiptID, dir)
	var result core.AdoptedContext
	if err = json.Unmarshal(adopted.Data, &result); err != nil || code != 0 || adopted.Status != "OK" || result.CustodyOrigin != "adopted" || result.AlreadyAdopted || result.ReceiptID != pkg.ReceiptID {
		t.Fatalf("adoption: %d %+v %v", code, adopted, err)
	}
	code, repeated := operatorProcess(t, "adopt-context", pkg.ReceiptID, dir)
	var again core.AdoptedContext
	if err = json.Unmarshal(repeated.Data, &again); err != nil || code != 0 || !again.AlreadyAdopted || again.OwnershipID != result.OwnershipID {
		t.Fatalf("a repeat must report the same custody: %d %+v %v", code, repeated, err)
	}

	// The same CLI that adopted the file now forgets and purges it.
	preview, err := run(ctx, []string{"preview-delete", record.RecordID}, nil)
	if err != nil {
		t.Fatal(err)
	}
	forgetRequest, _ := json.Marshal(core.ForgetRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, GrantID: root.ID, PreviewID: preview.(core.RetractionPreview).PreviewID})
	deletion, err := run(ctx, []string{"forget"}, bytes.NewReader(forgetRequest))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = run(ctx, []string{"purge-deletion", deletion.(core.Deletion).DeletionID}, nil); err != nil {
		t.Fatal(err)
	}
	if _, err = os.Lstat(filepath.Join(dir, "context.txt")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("the adopted context file survived purge-deletion: %v", err)
	}
	for _, kept := range []string{"outcome.json", ".cairn-context-owner"} {
		if _, err = os.Lstat(filepath.Join(dir, kept)); err != nil {
			t.Fatalf("purge removed %s: %v", kept, err)
		}
	}
	// A forgotten receipt has no payload left to verify against.
	code, forgotten := operatorProcess(t, "adopt-context", wrongReceipt, wrongDir)
	if code != 4 || forgotten.Status != "PAYLOAD_UNAVAILABLE" {
		t.Fatalf("a forgotten receipt: %d %+v", code, forgotten)
	}
}
