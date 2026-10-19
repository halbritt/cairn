package core

import (
	"reflect"
	"testing"
)

func TestCheckSymbolCurrentnessDistinguishesSiblingAndTargetChanges(t *testing.T) {
	before := []byte(`package sample

type Store struct{}

func (s *Store) SetSections(body string) string {
	return body
}

func Helper(x int) int {
	return x + 1
}
`)
	afterSiblingEdit := []byte(`package sample

type Store struct{}

func (s *Store) SetSections(body string) string {
	return body
}

func Helper(x int) int {
	return x + 2
}
`)
	siblingDiff := ParseUnifiedDiff(`--- a/core/sample.go
+++ b/core/sample.go
@@ -9,3 +9,3 @@
 func Helper(x int) int {
-	return x + 1
+	return x + 2
 }
`)
	entities := []EntityRef{
		{Kind: "symbol", Name: "Store.SetSections"},
		{Kind: "symbol", Name: "Helper"},
		{Kind: "file", Name: "core/sample.go"},
	}
	report := CheckSymbolCurrentness(
		entities,
		siblingDiff,
		map[string][]byte{"core/sample.go": before},
		map[string][]byte{"core/sample.go": afterSiblingEdit},
	)
	if !report.Affected || len(report.Impacts) != 3 {
		t.Fatalf("unexpected report: %+v", report)
	}
	if report.Impacts[0].Status != "unaffected" {
		t.Fatalf("expected Store.SetSections to be unaffected by sibling edit, got %+v", report.Impacts[0])
	}
	if report.Impacts[1].Status != "body_modified" {
		t.Fatalf("expected Helper to be body_modified, got %+v", report.Impacts[1])
	}
	if report.Impacts[2].Status != "file_modified" {
		t.Fatalf("expected core/sample.go to be file_modified, got %+v", report.Impacts[2])
	}

	afterSigAndDelete := []byte(`package sample

type Store struct{}

func (s *Store) SetSections(body string, strict bool) string {
	return body
}
`)
	report2 := CheckSymbolCurrentness(
		entities[:2],
		nil,
		map[string][]byte{"core/sample.go": before},
		map[string][]byte{"core/sample.go": afterSigAndDelete},
	)
	if report2.Impacts[0].Status != "signature_modified" {
		t.Fatalf("expected signature_modified, got %+v", report2.Impacts[0])
	}
	if report2.Impacts[1].Status != "deleted" {
		t.Fatalf("expected deleted for Helper, got %+v", report2.Impacts[1])
	}
}

func TestSuggestNoteEntitiesExtractsNormalizedFilesAndSymbols(t *testing.T) {
	body := "Update `Store.SetSections` and `spliceSections()` in core/set_sections.go and `mcpapi/server.go`. Ignore https://example.com/a/b.go and `plainword`."
	got := SuggestNoteEntities(
		body,
		[]string{"core/set_sections.go", "mcpapi/server.go"},
		[]string{"Store.SetSections", "spliceSections"},
	)
	want := []EntityRef{
		{Kind: "file", Name: "core/set_sections.go"},
		{Kind: "file", Name: "mcpapi/server.go"},
		{Kind: "symbol", Name: "Store.SetSections"},
		{Kind: "symbol", Name: "spliceSections"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("SuggestNoteEntities = %+v, want %+v", got, want)
	}
}
