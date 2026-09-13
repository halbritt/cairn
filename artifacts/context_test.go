package artifacts

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func fixture(t *testing.T) (*core.Store, core.Grant, core.Record, core.Package, string) {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	s, err := core.Open(context.Background(), dsn, core.Channel{Principal: "operator:tests", Operator: true, Instrumented: true})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	if err = s.Migrate(context.Background()); err != nil {
		t.Fatal(err)
	}
	root, err := s.Bootstrap(context.Background(), core.BootstrapRequest{RequestID: "82bf5bce-dc6c-4d03-a49f-1677bdcc9a32", Reason: "Install synthetic test operator root"})
	if err != nil {
		t.Fatal(err)
	}
	repo := uuid.NewString()
	record, err := s.Create(context.Background(), core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "context purge fixture", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}})
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := s.Compile(context.Background(), core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 64000}, core.Destination{Name: "local", AllowLocal: true})
	if err != nil {
		t.Fatal(err)
	}
	if err = s.ClaimRun(context.Background(), pkg.ReceiptID); err != nil {
		t.Fatal(err)
	}
	return s, root, record, pkg, t.TempDir()
}
func forget(t *testing.T, s *core.Store, root core.Grant, record core.Record) core.Deletion {
	t.Helper()
	p, err := s.PreviewDeletion(context.Background(), record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	d, err := s.Forget(context.Background(), core.ForgetRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, GrantID: root.ID, PreviewID: p.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	return d
}
func contextEffect(t *testing.T, d core.Deletion) core.DeletionEffect {
	t.Helper()
	for _, e := range d.Effects {
		if e.TargetType == "managed_context" {
			return e
		}
	}
	t.Fatal("managed context effect missing")
	return core.DeletionEffect{}
}

func TestPurgeRefusesReplacedDirectoryAndRecovers(t *testing.T) {
	ctx := context.Background()
	s, root, record, pkg, parent := fixture(t)
	path, err := WriteContext(ctx, s, pkg, parent)
	if err != nil {
		t.Fatal(err)
	}
	d := forget(t, s, root, record)
	moved := filepath.Join(parent, "moved-original")
	if err = os.Rename(path, moved); err != nil {
		t.Fatal(err)
	}
	if err = os.Mkdir(path, 0700); err != nil {
		t.Fatal(err)
	}
	sentinel := filepath.Join(path, "context.txt")
	if err = os.WriteFile(sentinel, []byte("unrelated replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	_, err = PurgeDeletion(ctx, s, d.DeletionID)
	if core.Code(err) != "ARTIFACT_CHANGED" {
		t.Fatalf("replacement not detected: %v", err)
	}
	if body, err := os.ReadFile(sentinel); err != nil || string(body) != "unrelated replacement" {
		t.Fatalf("replacement changed: %v", err)
	}
	status, err := s.DeletionStatus(ctx, d.DeletionID)
	if err != nil {
		t.Fatal(err)
	}
	if e := contextEffect(t, status); e.Status != "failed" || e.Attempts != 1 {
		t.Fatalf("failure missing: %+v", e)
	}
	if err = os.Remove(sentinel); err != nil {
		t.Fatal(err)
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err = os.Rename(moved, path); err != nil {
		t.Fatal(err)
	}
	status, err = PurgeDeletion(ctx, s, d.DeletionID)
	if err != nil {
		t.Fatal(err)
	}
	if e := contextEffect(t, status); e.Status != "completed" || e.Attempts != 2 {
		t.Fatalf("recovery missing: %+v", e)
	}
}

func TestPurgeUnlinksSymlinkWithoutFollowingItsTarget(t *testing.T) {
	ctx := context.Background()
	s, root, record, pkg, parent := fixture(t)
	path, err := WriteContext(ctx, s, pkg, parent)
	if err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(parent, "keep.txt")
	if err = os.WriteFile(outside, []byte("keep unrelated data"), 0600); err != nil {
		t.Fatal(err)
	}
	slot := filepath.Join(path, "context.txt")
	if err = os.Remove(slot); err != nil {
		t.Fatal(err)
	}
	if err = os.Symlink(outside, slot); err != nil {
		t.Fatal(err)
	}
	d := forget(t, s, root, record)
	if _, err = PurgeDeletion(ctx, s, d.DeletionID); err != nil {
		t.Fatal(err)
	}
	if _, err = os.Lstat(slot); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("symlink retained: %v", err)
	}
	if body, err := os.ReadFile(outside); err != nil || string(body) != "keep unrelated data" {
		t.Fatalf("symlink target changed: %v", err)
	}
}

func TestPurgeWaitsForReservedWriterAndRemovesInterruptedBytes(t *testing.T) {
	ctx := context.Background()
	s, root, record, pkg, parent := fixture(t)
	path := filepath.Join(parent, pkg.ReceiptID)
	if err := os.Mkdir(path, 0700); err != nil {
		t.Fatal(err)
	}
	locked, err := lockDirectory(ctx, path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if locked != nil {
			if err := locked.close(); err != nil {
				t.Error(err)
			}
		}
	})
	ownership := uuid.NewString()
	if err = locked.root.WriteFile(".cairn-context-owner", []byte(ownership), 0600); err != nil {
		t.Fatal(err)
	}
	req := core.ManagedContextRequest{OwnershipID: ownership, RequestID: pkg.ReceiptID, ReceiptID: pkg.ReceiptID, Directory: path, DirectoryDevice: locked.device, DirectoryInode: locked.inode}
	preview, err := s.PreviewDeletion(ctx, record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.RegisterManagedContext(ctx, req); err != nil {
		t.Fatal(err)
	}
	_, err = s.Forget(ctx, core.ForgetRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, GrantID: root.ID, PreviewID: preview.PreviewID})
	if core.Code(err) != "STALE_PREVIEW" {
		t.Fatalf("new copy did not invalidate preview: %v", err)
	}
	d := forget(t, s, root, record)
	// A bounded worker attempt cannot pass a live writer's directory lock.
	timeout, cancel := context.WithTimeout(ctx, 50*time.Millisecond)
	_, err = PurgeDeletion(timeout, s, d.DeletionID)
	cancel()
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("purge passed held writer lock: %v", err)
	}
	// Model a writer that left only part of its registered bytes before dying.
	if err = locked.root.WriteFile("context.txt", []byte("partial sensitive bytes"), 0600); err != nil {
		t.Fatal(err)
	}
	if err = locked.close(); err != nil {
		t.Fatal(err)
	}
	locked = nil
	if _, err = PurgeDeletion(ctx, s, d.DeletionID); err != nil {
		t.Fatal(err)
	}
	if _, err = os.Lstat(filepath.Join(path, "context.txt")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("partial bytes survived: %v", err)
	}
	_, err = s.RegisterManagedContext(ctx, req)
	if core.Code(err) != "PAYLOAD_UNAVAILABLE" {
		t.Fatalf("registration retry revived forgotten content: %v", err)
	}
	if _, err = WriteContext(ctx, s, pkg, parent); err == nil {
		t.Fatal("late writer reused purged run directory")
	}
}

func TestInvalidReceiptDoesNotCreateDirectories(t *testing.T) {
	parent := filepath.Join(t.TempDir(), "not-created")
	_, err := WriteContext(context.Background(), nil, core.Package{ReceiptID: "../unrelated"}, parent)
	if core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("invalid receipt accepted: %v", err)
	}
	if _, err = os.Stat(parent); !os.IsNotExist(err) {
		t.Fatalf("invalid receipt touched filesystem: %v", err)
	}
}

func TestPurgeRequiresOriginalOwnershipMarker(t *testing.T) {
	ctx := context.Background()
	s, root, record, pkg, parent := fixture(t)
	path, err := WriteContext(ctx, s, pkg, parent)
	if err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(path, ".cairn-context-owner")
	original, err := os.ReadFile(marker)
	if err != nil {
		t.Fatal(err)
	}
	d := forget(t, s, root, record)
	if err = os.WriteFile(marker, []byte(uuid.NewString()), 0600); err != nil {
		t.Fatal(err)
	}
	_, err = PurgeDeletion(ctx, s, d.DeletionID)
	if core.Code(err) != "ARTIFACT_CHANGED" {
		t.Fatalf("changed marker accepted: %v", err)
	}
	if _, err = os.Stat(filepath.Join(path, "context.txt")); err != nil {
		t.Fatalf("changed slot removed: %v", err)
	}
	if err = os.WriteFile(marker, original, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = PurgeDeletion(ctx, s, d.DeletionID); err != nil {
		t.Fatal(err)
	}
}
