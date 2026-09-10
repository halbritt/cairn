package core

import (
	"bytes"
	"context"
	"encoding/json"
	"reflect"
	"testing"

	"github.com/google/uuid"
)

func TestRecompileDestinationProtectsBodiesAndPreviews(t *testing.T) {
	for _, mode := range []string{"body", "index"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			op, root := testOperator(t)
			repo := uuid.NewString()
			draft := projectNote(repo)
			draft.Body, draft.Sensitivity = "historical private canary", "shareable"
			note, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
			if err != nil {
				t.Fatal(err)
			}
			req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "inspection", "original"}, Purpose: "context", AvailableTokens: 32000}
			dest := Destination{"hosted", false}
			var original Package
			if mode == "index" {
				idx, e := op.Index(ctx, req, dest)
				err = e
				original = idx.Package
				if err == nil && len(original.Semantic.Index) != 1 {
					t.Fatal("fixture preview not selected")
				}
			} else {
				original, err = op.Compile(ctx, req, dest)
				if err == nil && len(original.Semantic.Selected) != 1 {
					t.Fatal("fixture body not selected")
				}
			}
			if err != nil {
				t.Fatal(err)
			}
			replayReq := RecompileRequest{ReceiptID: original.ReceiptID}
			got, err := op.RecompileForDestination(ctx, replayReq, dest)
			if err != nil {
				t.Fatal(err)
			}
			requireSameHistoricalPackage(t, got, original)
			_, err = op.RecompileForDestination(ctx, replayReq, Destination{"local", true})
			requireCode(t, err, "AUTHORITY_DENIED")
			_, err = op.RecompileForDestination(ctx, replayReq, Destination{"hosted", true})
			requireCode(t, err, "INVALID_REQUEST")
			// Sensitivity is not ordinary-editable. Model a later operator restriction
			// directly in this disposable fixture to protect the read boundary itself.
			if _, err = op.pool.Exec(ctx, `UPDATE cairn.memory_record SET sensitivity='local' WHERE record_id=$1`, note.RecordID); err != nil {
				t.Fatal(err)
			}
			got, err = op.RecompileForDestination(ctx, replayReq, dest)
			requireCode(t, err, "AUTHORITY_DENIED")
			if !reflect.DeepEqual(got, Package{}) {
				t.Fatal("private historical content accompanied refusal")
			}
			if _, err = op.pool.Exec(ctx, `UPDATE cairn.memory_record SET sensitivity='shareable' WHERE record_id=$1`, note.RecordID); err != nil {
				t.Fatal(err)
			}
			preview, err := op.PreviewDeletion(ctx, note.RecordID)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = op.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: note.Version, GrantID: root.ID, PreviewID: preview.PreviewID}); err != nil {
				t.Fatal(err)
			}
			_, err = op.RecompileForDestination(ctx, replayReq, dest)
			requireCode(t, err, "PAYLOAD_UNAVAILABLE")
		})
	}
}

func TestRecompileDestinationCannotExportLocalReceipt(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "recompile-destination"})
	repo := uuid.NewString()
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)}); err != nil {
		t.Fatal(err)
	}
	original, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 32000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	req := RecompileRequest{ReceiptID: original.ReceiptID}
	got, err := s.RecompileForDestination(ctx, req, Destination{"hosted", false})
	requireCode(t, err, "AUTHORITY_DENIED")
	if !reflect.DeepEqual(got, Package{}) {
		t.Fatal("local receipt disclosed on refused export")
	}
	got, err = s.RecompileForDestination(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	requireSameHistoricalPackage(t, got, original)
}

// Compare the external package, not time.Time's process-local location pointers.
func requireSameHistoricalPackage(t *testing.T, got, want Package) {
	t.Helper()
	actual, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	expected, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(actual, expected) {
		t.Fatalf("historical package changed: got %s; want %s", actual, expected)
	}
}
