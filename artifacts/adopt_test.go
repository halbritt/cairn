package artifacts

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"syscall"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5"
)

// finishRun records the outcome that a finished historical run would have.
func finishRun(t *testing.T, s *core.Store, receipt string) {
	t.Helper()
	zero := 0
	if _, err := s.RecordOutcome(context.Background(), core.OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: receipt, ExitCode: &zero, DurationMS: 25, ProcessState: "exited",
		StdoutSHA256: strings.Repeat("a", 64), StderrSHA256: strings.Repeat("b", 64)}); err != nil {
		t.Fatal(err)
	}
}

// historicalRun lays out a run directory as the first runner did: a private
// directory named by the receipt, an unmarked context.txt of the rendered
// package, and the outcome file. Nothing is registered.
func historicalRun(t *testing.T, pkg core.Package, parent string) string {
	t.Helper()
	canonical, err := filepath.EvalSymlinks(parent)
	if err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(canonical, pkg.ReceiptID)
	if err = os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	rendered, err := pkg.Render()
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(dir, "context.txt"), []byte(rendered), 0600); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(dir, "outcome.json"), []byte(`{"process_state":"exited"}`), 0600); err != nil {
		t.Fatal(err)
	}
	return dir
}

type entry struct {
	Mode   os.FileMode
	Size   int64
	Digest string
	Link   string
	MTime  int64
	Inode  uint64
}

// snapshot records every name in the directory, including its identity and
// modification time, so that "nothing changed" is a comparison and not a hope.
func snapshot(t *testing.T, dir string) map[string]entry {
	t.Helper()
	names, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	result := map[string]entry{".": {Mode: statMode(t, dir)}}
	for _, name := range names {
		path := filepath.Join(dir, name.Name())
		info, err := os.Lstat(path)
		if err != nil {
			t.Fatal(err)
		}
		e := entry{Mode: info.Mode(), Size: info.Size(), MTime: info.ModTime().UnixNano(), Inode: info.Sys().(*syscall.Stat_t).Ino}
		switch {
		case info.Mode()&os.ModeSymlink != 0:
			e.Link, _ = os.Readlink(path)
		case info.Mode().IsRegular():
			data, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			sum := sha256.Sum256(data)
			e.Digest = hex.EncodeToString(sum[:])
		}
		result[name.Name()] = e
	}
	return result
}

func statMode(t *testing.T, path string) os.FileMode {
	t.Helper()
	info, err := os.Lstat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info.Mode()
}

func custodyTargets(t *testing.T, s *core.Store, record core.Record, receipt string) map[string]string {
	t.Helper()
	preview, err := s.PreviewDeletion(context.Background(), record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	found := map[string]string{}
	for _, target := range preview.DeletionTargets {
		if target.TargetID == receipt {
			found[target.TargetType] = target.Status
		}
	}
	return found
}

// custodyRows counts the receipt's managed custody rows directly, so a refusal
// is checked against the table and not against a workflow that could hide it.
func custodyRows(t *testing.T, receipt string) int {
	t.Helper()
	ctx := context.Background()
	conn, err := pgx.Connect(ctx, os.Getenv("CAIRN_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(ctx)
	var count int
	if err = conn.QueryRow(ctx, `SELECT count(*) FROM cairn.managed_context WHERE receipt_id=$1`, receipt).Scan(&count); err != nil {
		t.Fatal(err)
	}
	return count
}

func useRows(t *testing.T, s *core.Store, repo string) string {
	t.Helper()
	report, err := s.UseReport(context.Background(), core.UseReportRequest{Repo: repo, Limit: 100})
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(report.Rows)
	if err != nil {
		t.Fatal(err)
	}
	return string(encoded)
}

func TestAdoptHistoricalContextEntersForgettingAndPurge(t *testing.T) {
	ctx := context.Background()
	s, root, record, pkg, parent := fixture(t)
	finishRun(t, s, pkg.ReceiptID)
	dir := historicalRun(t, pkg, parent)
	before := snapshot(t, dir)
	history := useRows(t, s, record.Scope.Repo)
	if got := custodyTargets(t, s, record, pkg.ReceiptID); got["run_artifacts"] != "not_possible" || got["managed_context"] != "" {
		t.Fatalf("an unregistered historical launch is a residual before adoption: %+v", got)
	}

	adopted, err := AdoptContext(ctx, s, pkg.ReceiptID, dir)
	if err != nil {
		t.Fatal(err)
	}
	if adopted.CustodyOrigin != "adopted" || adopted.AlreadyAdopted || adopted.Directory != dir || adopted.ObservedBytes != before["context.txt"].Size {
		t.Fatalf("adoption result: %+v", adopted)
	}
	// The file and the run's other artifacts are untouched; the only new name is the private marker.
	after := snapshot(t, dir)
	marker, ok := after[".cairn-context-owner"]
	if !ok || marker.Mode != 0600 {
		t.Fatalf("ownership marker missing or not private: %+v", marker)
	}
	if body, err := os.ReadFile(filepath.Join(dir, ".cairn-context-owner")); err != nil || string(body) != adopted.OwnershipID {
		t.Fatalf("marker does not carry the recorded ownership: %q %v", body, err)
	}
	delete(after, ".cairn-context-owner")
	if after["."].Mode != before["."].Mode {
		t.Fatalf("directory mode changed: %v -> %v", before["."].Mode, after["."].Mode)
	}
	after["."], before["."] = entry{}, entry{}
	if !reflect.DeepEqual(before, after) {
		t.Fatalf("adoption changed the historical files:\n%+v\n%+v", before, after)
	}
	if now := useRows(t, s, record.Scope.Repo); now != history {
		t.Fatalf("adoption changed recorded delivery, usage or outcome:\n%s\n%s", history, now)
	}

	// The existing workflow now owns the copy.
	if got := custodyTargets(t, s, record, pkg.ReceiptID); got["managed_context"] != "pending" || got["run_artifacts"] != "" {
		t.Fatalf("adopted file missing from the inventory: %+v", got)
	}
	d := forget(t, s, root, record)
	purged, err := PurgeDeletion(ctx, s, d.DeletionID)
	if err != nil {
		t.Fatal(err)
	}
	if effect := contextEffect(t, purged); effect.Status != "completed" || effect.Attempts != 1 {
		t.Fatalf("purge did not complete: %+v", effect)
	}
	if _, err = os.Lstat(filepath.Join(dir, "context.txt")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("the adopted context file was not removed: %v", err)
	}
	for _, kept := range []string{"outcome.json", ".cairn-context-owner"} {
		if _, err = os.Lstat(filepath.Join(dir, kept)); err != nil {
			t.Fatalf("purge removed %s: %v", kept, err)
		}
	}
}

func TestAdoptionRetriesAndInterruptedMarker(t *testing.T) {
	ctx := context.Background()
	s, _, _, pkg, parent := fixture(t)
	finishRun(t, s, pkg.ReceiptID)
	dir := historicalRun(t, pkg, parent)
	first, err := AdoptContext(ctx, s, pkg.ReceiptID, dir)
	if err != nil {
		t.Fatal(err)
	}
	marker := snapshot(t, dir)[".cairn-context-owner"]
	again, err := AdoptContext(ctx, s, pkg.ReceiptID, dir)
	if err != nil || !again.AlreadyAdopted || again.OwnershipID != first.OwnershipID || !again.AdoptedAt.Equal(first.AdoptedAt) {
		t.Fatalf("an identical retry must change nothing: %+v %v", again, err)
	}
	if now := snapshot(t, dir)[".cairn-context-owner"]; now != marker {
		t.Fatalf("a retry rewrote the marker: %+v -> %+v", marker, now)
	}

	// A retry re-verifies the bytes: a file changed since adoption is refused while its custody stays.
	contextFile := filepath.Join(dir, "context.txt")
	original, err := os.ReadFile(contextFile)
	if err != nil {
		t.Fatal(err)
	}
	write(t, contextFile, "changed after adoption", 0600)
	if _, err = AdoptContext(ctx, s, pkg.ReceiptID, dir); core.Code(err) != "ARTIFACT_MISMATCH" {
		t.Fatalf("a changed file after adoption: %v", err)
	}
	write(t, contextFile, string(original), 0600)
	if custodyRows(t, pkg.ReceiptID) != 1 {
		t.Fatal("a refused retry changed custody")
	}

	// Interrupted: the process died after creating the marker and before the store recorded custody.
	s2, _, _, pkg2, parent2 := fixture(t)
	finishRun(t, s2, pkg2.ReceiptID)
	dir2 := historicalRun(t, pkg2, parent2)
	ownership := uuid.NewString()
	if err = os.WriteFile(filepath.Join(dir2, ".cairn-context-owner"), []byte(ownership), 0600); err != nil {
		t.Fatal(err)
	}
	before := snapshot(t, dir2)
	resumed, err := AdoptContext(ctx, s2, pkg2.ReceiptID, dir2)
	if err != nil || resumed.AlreadyAdopted || resumed.OwnershipID != ownership {
		t.Fatalf("the interrupted adoption was not resumed under its marker: %+v %v", resumed, err)
	}
	if !reflect.DeepEqual(before, snapshot(t, dir2)) {
		t.Fatal("resuming rewrote files")
	}

	// Recorded custody whose marker was later removed is not silently repaired.
	if err = os.Remove(filepath.Join(dir, ".cairn-context-owner")); err != nil {
		t.Fatal(err)
	}
	_, err = AdoptContext(ctx, s, pkg.ReceiptID, dir)
	if core.Code(err) != "ARTIFACT_CHANGED" {
		t.Fatalf("a missing marker for recorded custody: %v", err)
	}
	if _, statErr := os.Lstat(filepath.Join(dir, ".cairn-context-owner")); !errors.Is(statErr, os.ErrNotExist) {
		t.Fatal("a marker was recreated for custody it did not create")
	}
}

func TestAdoptionRefusalsLeaveTheRunDirectoryUntouched(t *testing.T) {
	ctx := context.Background()
	s, _, record, pkg, _ := fixture(t)
	finishRun(t, s, pkg.ReceiptID)
	other, err := s.Compile(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: record.Scope.Repo, TaskID: "task", RunID: "other"}, Purpose: "context", AvailableTokens: 64000}, core.Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	otherRendered, _ := other.Render()
	notLaunched, err := s.Compile(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: record.Scope.Repo, TaskID: "task", RunID: "unlaunched"}, Purpose: "context", AvailableTokens: 64000}, core.Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name  string
		code  string
		setup func(t *testing.T, dir string) (string, string) // returns receipt, directory
	}{
		{"edited file", "ARTIFACT_MISMATCH", func(t *testing.T, dir string) (string, string) {
			write(t, filepath.Join(dir, "context.txt"), "edited", 0600)
			return pkg.ReceiptID, dir
		}},
		{"truncated file", "ARTIFACT_MISMATCH", func(t *testing.T, dir string) (string, string) {
			rendered, _ := pkg.Render()
			write(t, filepath.Join(dir, "context.txt"), rendered[:len(rendered)/2], 0600)
			return pkg.ReceiptID, dir
		}},
		{"another receipt's package", "ARTIFACT_MISMATCH", func(t *testing.T, dir string) (string, string) {
			write(t, filepath.Join(dir, "context.txt"), otherRendered, 0600)
			return pkg.ReceiptID, dir
		}},
		{"empty file", "ARTIFACT_MISMATCH", func(t *testing.T, dir string) (string, string) {
			write(t, filepath.Join(dir, "context.txt"), "", 0600)
			return pkg.ReceiptID, dir
		}},
		{"missing file", "NOT_FOUND", func(t *testing.T, dir string) (string, string) {
			must(t, os.Remove(filepath.Join(dir, "context.txt")))
			return pkg.ReceiptID, dir
		}},
		{"group readable file", "ARTIFACT_UNSAFE", func(t *testing.T, dir string) (string, string) {
			must(t, os.Chmod(filepath.Join(dir, "context.txt"), 0640))
			return pkg.ReceiptID, dir
		}},
		{"file symlink", "ARTIFACT_UNSAFE", func(t *testing.T, dir string) (string, string) {
			rendered, _ := pkg.Render()
			outside := filepath.Join(filepath.Dir(dir), "elsewhere.txt")
			write(t, outside, rendered, 0600)
			must(t, os.Remove(filepath.Join(dir, "context.txt")))
			must(t, os.Symlink(outside, filepath.Join(dir, "context.txt")))
			return pkg.ReceiptID, dir
		}},
		{"hard linked file", "ARTIFACT_UNSAFE", func(t *testing.T, dir string) (string, string) {
			must(t, os.Link(filepath.Join(dir, "context.txt"), filepath.Join(filepath.Dir(dir), "alias-"+pkg.ReceiptID)))
			return pkg.ReceiptID, dir
		}},
		{"file slot is a directory", "ARTIFACT_UNSAFE", func(t *testing.T, dir string) (string, string) {
			must(t, os.Remove(filepath.Join(dir, "context.txt")))
			must(t, os.Mkdir(filepath.Join(dir, "context.txt"), 0700))
			return pkg.ReceiptID, dir
		}},
		{"oversized file", "ARTIFACT_UNSAFE", func(t *testing.T, dir string) (string, string) {
			write(t, filepath.Join(dir, "context.txt"), strings.Repeat("x", core.MaxAdoptedContextBytes+1), 0600)
			return pkg.ReceiptID, dir
		}},
		{"group readable directory", "ARTIFACT_UNSAFE", func(t *testing.T, dir string) (string, string) {
			must(t, os.Chmod(dir, 0750))
			return pkg.ReceiptID, dir
		}},
		{"marker symlink", "ARTIFACT_UNSAFE", func(t *testing.T, dir string) (string, string) {
			must(t, os.Symlink("outcome.json", filepath.Join(dir, ".cairn-context-owner")))
			return pkg.ReceiptID, dir
		}},
		{"marker is not a Cairn marker", "ARTIFACT_UNSAFE", func(t *testing.T, dir string) (string, string) {
			write(t, filepath.Join(dir, ".cairn-context-owner"), "not a uuid", 0600)
			return pkg.ReceiptID, dir
		}},
		{"marker with trailing newline", "ARTIFACT_UNSAFE", func(t *testing.T, dir string) (string, string) {
			write(t, filepath.Join(dir, ".cairn-context-owner"), uuid.NewString()+"\n", 0600)
			return pkg.ReceiptID, dir
		}},
		{"group readable marker", "ARTIFACT_UNSAFE", func(t *testing.T, dir string) (string, string) {
			write(t, filepath.Join(dir, ".cairn-context-owner"), uuid.NewString(), 0640)
			return pkg.ReceiptID, dir
		}},
		{"directory named for another receipt", "INVALID_REQUEST", func(t *testing.T, dir string) (string, string) {
			return other.ReceiptID, dir
		}},
		{"receipt that was never launched", "INVALID_REQUEST", func(t *testing.T, dir string) (string, string) {
			renamed := filepath.Join(filepath.Dir(dir), notLaunched.ReceiptID)
			must(t, os.Rename(dir, renamed))
			return notLaunched.ReceiptID, renamed
		}},
		{"not a receipt UUID", "INVALID_REQUEST", func(t *testing.T, dir string) (string, string) {
			return "latest", dir
		}},
		{"symlinked directory path", "ARTIFACT_CHANGED", func(t *testing.T, dir string) (string, string) {
			link := filepath.Join(filepath.Dir(dir), "via-link")
			must(t, os.Symlink(filepath.Dir(dir), link))
			return pkg.ReceiptID, filepath.Join(link, pkg.ReceiptID)
		}},
		{"missing directory", "NOT_FOUND", func(t *testing.T, dir string) (string, string) {
			return pkg.ReceiptID, filepath.Join(filepath.Dir(dir), "absent", pkg.ReceiptID)
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			scratch := t.TempDir()
			dir := historicalRun(t, pkg, scratch)
			receipt, target := c.setup(t, dir)
			var before map[string]entry
			if _, err := os.Lstat(dir); err == nil {
				before = snapshot(t, dir)
			}
			_, err := AdoptContext(ctx, s, receipt, target)
			if core.Code(err) != c.code {
				t.Fatalf("want %s, got %v", c.code, err)
			}
			if before != nil {
				if after := snapshot(t, dir); !reflect.DeepEqual(before, after) {
					t.Fatalf("a refusal changed the run directory:\n%+v\n%+v", before, after)
				}
			}
			if n := custodyRows(t, pkg.ReceiptID); n != 0 {
				t.Fatalf("a refusal recorded custody: %d", n)
			}
		})
	}
}

func TestAdoptionRefusesOtherOwnersAndUnprivilegedChannels(t *testing.T) {
	ctx := context.Background()
	s, _, _, pkg, parent := fixture(t)
	finishRun(t, s, pkg.ReceiptID)
	dir := historicalRun(t, pkg, parent)
	before := snapshot(t, dir)
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	for name, channel := range map[string]core.Channel{
		"another principal": {Principal: "operator:someone-else", Operator: true, Instrumented: true},
		"not instrumented":  {Principal: "operator:tests", Operator: true},
		"not an operator":   {Principal: "operator:tests", Instrumented: true},
	} {
		t.Run(name, func(t *testing.T) {
			store, err := core.Open(ctx, dsn, channel)
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			if _, err = AdoptContext(ctx, store, pkg.ReceiptID, dir); core.Code(err) != "AUTHORITY_DENIED" {
				t.Fatalf("got %v", err)
			}
			if !reflect.DeepEqual(before, snapshot(t, dir)) {
				t.Fatal("a denied adoption changed the directory")
			}
		})
	}
}

func TestAdoptionRefusesRegisteredAndConflictingCustody(t *testing.T) {
	ctx := context.Background()
	s, _, record, pkg, parent := fixture(t)
	finishRun(t, s, pkg.ReceiptID)
	// A run written by the current runner holds registered custody; adoption never replaces it.
	registered, err := WriteContext(ctx, s, pkg, parent)
	if err != nil {
		t.Fatal(err)
	}
	before := snapshot(t, registered)
	_, err = AdoptContext(ctx, s, pkg.ReceiptID, registered)
	if core.Code(err) != "CUSTODY_CONFLICT" {
		t.Fatalf("registered custody: %v", err)
	}
	if !reflect.DeepEqual(before, snapshot(t, registered)) {
		t.Fatal("refusing registered custody changed its directory")
	}

	// A marker that already names that registered run cannot identify a second run.
	second, err := s.Compile(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: record.Scope.Repo, TaskID: "task", RunID: "second"}, Purpose: "context", AvailableTokens: 64000}, core.Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	if err = s.ClaimRun(ctx, second.ReceiptID); err != nil {
		t.Fatal(err)
	}
	finishRun(t, s, second.ReceiptID)
	dir := historicalRun(t, second, parent)
	marker, err := os.ReadFile(filepath.Join(registered, ".cairn-context-owner"))
	if err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(dir, ".cairn-context-owner"), string(marker), 0600)
	copied := snapshot(t, dir)
	_, err = AdoptContext(ctx, s, second.ReceiptID, dir)
	if core.Code(err) != "CUSTODY_CONFLICT" || !reflect.DeepEqual(copied, snapshot(t, dir)) {
		t.Fatalf("a copied marker: %v", err)
	}

	// A completed adoption in one directory conflicts with the same receipt adopted from a copy elsewhere.
	clean := historicalRun(t, second, t.TempDir())
	must(t, os.Remove(filepath.Join(dir, ".cairn-context-owner")))
	if _, err = AdoptContext(ctx, s, second.ReceiptID, dir); err != nil {
		t.Fatal(err)
	}
	_, err = AdoptContext(ctx, s, second.ReceiptID, clean)
	if core.Code(err) != "CUSTODY_CONFLICT" {
		t.Fatalf("a second directory for one receipt: %v", err)
	}
	if _, statErr := os.Lstat(filepath.Join(clean, ".cairn-context-owner")); !errors.Is(statErr, os.ErrNotExist) {
		t.Fatal("a conflicting copy was marked")
	}
}

func TestConcurrentAdoptersSerializeOnTheDirectoryLock(t *testing.T) {
	ctx := context.Background()
	s, _, _, pkg, parent := fixture(t)
	finishRun(t, s, pkg.ReceiptID)
	dir := historicalRun(t, pkg, parent)
	var wg sync.WaitGroup
	results := make([]core.AdoptedContext, 6)
	errs := make([]error, len(results))
	for i := range results {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i], errs[i] = AdoptContext(ctx, s, pkg.ReceiptID, dir)
		}()
	}
	wg.Wait()
	fresh := 0
	ownership := map[string]bool{}
	for i, err := range errs {
		if err != nil {
			t.Fatalf("adopter %d: %v", i, err)
		}
		if !results[i].AlreadyAdopted {
			fresh++
		}
		ownership[results[i].OwnershipID] = true
	}
	if fresh != 1 || len(ownership) != 1 {
		t.Fatalf("exactly one adopter creates custody and all agree on it: %d fresh, %d ownerships", fresh, len(ownership))
	}
	if body, err := os.ReadFile(filepath.Join(dir, ".cairn-context-owner")); err != nil || !ownership[string(body)] {
		t.Fatalf("marker does not match the recorded ownership: %q %v", body, err)
	}
}

func TestAdoptionRacingForgettingRefusesWithoutAdoptingForgottenContent(t *testing.T) {
	ctx := context.Background()
	s, root, record, pkg, parent := fixture(t)
	finishRun(t, s, pkg.ReceiptID)
	dir := historicalRun(t, pkg, parent)
	// Forgetting commits after the owner's check and before registration.
	racing := &forgetAfterCheck{ContextAdopter: s, forget: func() { forget(t, s, root, record) }}
	_, err := AdoptContext(ctx, racing, pkg.ReceiptID, dir)
	if core.Code(err) != "PAYLOAD_UNAVAILABLE" {
		t.Fatalf("got %v", err)
	}
	if n := custodyRows(t, pkg.ReceiptID); n != 0 {
		t.Fatalf("forgotten content was adopted: %d", n)
	}
	// The marker made just before the refusal is not an adoption; every retry refuses the same way.
	if _, err = AdoptContext(ctx, s, pkg.ReceiptID, dir); core.Code(err) != "PAYLOAD_UNAVAILABLE" {
		t.Fatalf("retry after forgetting: %v", err)
	}
	if body, err := os.ReadFile(filepath.Join(dir, "context.txt")); err != nil || len(body) == 0 {
		t.Fatalf("adoption removed or emptied the file: %v", err)
	}
}

type forgetAfterCheck struct {
	ContextAdopter
	forget func()
}

func (f *forgetAfterCheck) CheckContextAdoption(ctx context.Context, req core.AdoptContextRequest) (*core.AdoptedContext, error) {
	existing, err := f.ContextAdopter.CheckContextAdoption(ctx, req)
	if err == nil {
		f.forget()
	}
	return existing, err
}

func TestAdoptedContextSurvivesPurgeRecovery(t *testing.T) {
	ctx := context.Background()
	s, root, record, pkg, parent := fixture(t)
	finishRun(t, s, pkg.ReceiptID)
	dir := historicalRun(t, pkg, parent)
	if _, err := AdoptContext(ctx, s, pkg.ReceiptID, dir); err != nil {
		t.Fatal(err)
	}
	d := forget(t, s, root, record)
	// A replaced directory fails the effect and removes nothing, exactly as for registered custody.
	moved := dir + "-moved"
	must(t, os.Rename(dir, moved))
	must(t, os.Mkdir(dir, 0700))
	write(t, filepath.Join(dir, "context.txt"), "unrelated replacement", 0600)
	if _, err := PurgeDeletion(ctx, s, d.DeletionID); core.Code(err) != "ARTIFACT_CHANGED" {
		t.Fatalf("replacement not detected: %v", err)
	}
	if body, _ := os.ReadFile(filepath.Join(dir, "context.txt")); string(body) != "unrelated replacement" {
		t.Fatal("the replacement's data was touched")
	}
	must(t, os.Remove(filepath.Join(dir, "context.txt")))
	must(t, os.Remove(dir))
	must(t, os.Rename(moved, dir))
	status, err := PurgeDeletion(ctx, s, d.DeletionID)
	if err != nil {
		t.Fatal(err)
	}
	if e := contextEffect(t, status); e.Status != "completed" || e.Attempts != 2 {
		t.Fatalf("recovery: %+v", e)
	}
	if _, err = os.Lstat(filepath.Join(dir, "context.txt")); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("the adopted file survived purge")
	}
}

func TestRemoteFilesystemsAreRefused(t *testing.T) {
	names := map[int64]string{0x6969: "nfs", 0x517B: "smb", 0xFF534D42: "cifs", 0xFE534D42: "smb2", 0xC36400: "ceph"}
	var refused []string
	for kind, name := range names {
		if !remoteFilesystem(kind) {
			t.Fatalf("%s is a network filesystem", name)
		}
		refused = append(refused, name)
	}
	sort.Strings(refused)
	for _, local := range []int64{0xEF53 /* ext4 */, 0x01021994 /* tmpfs */, 0x58465342 /* xfs */, 0x9123683E /* btrfs */} {
		if remoteFilesystem(local) {
			t.Fatalf("local filesystem %#x was refused", local)
		}
	}
	t.Logf("refused: %v", refused)
}

func TestAdoptionRefusesNetworkFilesystemWithoutTouchingTheDirectory(t *testing.T) {
	ctx := context.Background()
	s, _, _, pkg, parent := fixture(t)
	finishRun(t, s, pkg.ReceiptID)
	dir := historicalRun(t, pkg, parent)
	before := snapshot(t, dir)
	original := statfs
	t.Cleanup(func() { statfs = original })
	statfs = func(fd int, stat *syscall.Statfs_t) error {
		if err := original(fd, stat); err != nil {
			return err
		}
		stat.Type = 0x6969 // NFS
		return nil
	}
	_, err := AdoptContext(ctx, s, pkg.ReceiptID, dir)
	if core.Code(err) != "ARTIFACT_UNSAFE" || !strings.Contains(err.Error(), "network filesystem") {
		t.Fatalf("network filesystem: %v", err)
	}
	if !reflect.DeepEqual(before, snapshot(t, dir)) || custodyRows(t, pkg.ReceiptID) != 0 {
		t.Fatal("a refused network filesystem changed state")
	}
}

func write(t *testing.T, path, body string, mode os.FileMode) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), mode); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, mode); err != nil {
		t.Fatal(err)
	}
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}
