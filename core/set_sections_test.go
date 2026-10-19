package core

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestSetSectionsPreservesOutsideBytesMetadataAndRetry(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:set-sections"})
	d := projectNote(uuid.NewString())
	d.Body = "Handoff: cairn / section edits\n\nPreamble stays verbatim.\n\n## Goal\nOriginal goal.\n\n## Current State\n```sh\n## NotAHeadingInsideFence\necho ok\n```\n\n## Remaining\n- Old item\n"
	d.Kind, d.Sensitivity = "note", "shareable"
	d.Pins = &Applicability{TaskClass: "build"}
	d.Entities = []EntityRef{{Kind: "file", Name: "core/set_sections.go"}}
	old, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	req := SetSectionsRequest{
		RequestID:       uuid.NewString(),
		RecordID:        old.RecordID,
		ExpectedVersion: 1,
		Repo:            d.Scope.Repo,
		Sections: map[string]string{
			"Remaining":  "- Updated item 1\n- Updated item 2\n",
			"Next Steps": "Run make test-integration",
		},
	}
	rev, err := s.SetSections(ctx, req)
	if err != nil || rev != (Revision{old.RecordID, 2}) {
		t.Fatalf("set-sections: %+v %v", rev, err)
	}
	got, err := s.Get(ctx, old.RecordID)
	wantBody := "Handoff: cairn / section edits\n\nPreamble stays verbatim.\n\n## Goal\nOriginal goal.\n\n## Current State\n```sh\n## NotAHeadingInsideFence\necho ok\n```\n\n## Remaining\n- Updated item 1\n- Updated item 2\n\n## Next Steps\nRun make test-integration"
	wantDraft := old.Draft
	wantDraft.Body = wantBody
	if err != nil || !reflect.DeepEqual(got.Draft, wantDraft) {
		t.Fatalf("got body:\n%q\nwant:\n%q\nerr=%v", got.Body, wantBody, err)
	}
	// Repeating the same section values on the next version produces identical bytes.
	req2 := SetSectionsRequest{
		RequestID:       uuid.NewString(),
		RecordID:        old.RecordID,
		ExpectedVersion: 2,
		Repo:            d.Scope.Repo,
		Sections:        req.Sections,
	}
	rev2, err := s.SetSections(ctx, req2)
	if err != nil || rev2.Version != 3 {
		t.Fatalf("second set-sections: %+v %v", rev2, err)
	}
	got2, err := s.Get(ctx, old.RecordID)
	if err != nil || got2.Body != wantBody {
		t.Fatalf("second set-sections drifted:\n%q\nwant:\n%q", got2.Body, wantBody)
	}
	// Retrying the original request ID returns its original revision even after later edits.
	retry, err := s.SetSections(ctx, req)
	if err != nil || retry != rev {
		t.Fatalf("retry: %+v %v", retry, err)
	}
}

func TestSetSectionsValidationAndFenceSafety(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:set-sections-validation"})
	for _, tc := range []struct {
		name     string
		body     string
		sections map[string]string
	}{
		{"empty map", "## Goal\nOld", map[string]string{}},
		{"leading hash in key", "## Goal\nOld", map[string]string{"## Goal": "New"}},
		{"blank section body", "## Goal\nOld", map[string]string{"Goal": "  \n"}},
		{"unclosed fence in note", "## Goal\n```go\nunclosed", map[string]string{"Goal": "New"}},
		{"unclosed fence in update", "## Goal\nOld", map[string]string{"Goal": "```go\nunclosed"}},
		{"duplicate heading in note", "## Goal\nOne\n\n## Goal\nTwo", map[string]string{"Goal": "New"}},
		{"oversize result", "## Goal\nOld", map[string]string{"Goal": strings.Repeat("x", 65530)}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			d := projectNote(uuid.NewString())
			d.Body = tc.body
			note, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
			if err != nil {
				t.Fatal(err)
			}
			_, err = s.SetSections(ctx, SetSectionsRequest{
				RequestID:       uuid.NewString(),
				RecordID:        note.RecordID,
				ExpectedVersion: 1,
				Repo:            d.Scope.Repo,
				Sections:        tc.sections,
			})
			requireCode(t, err, "INVALID_REQUEST")
		})
	}
}
