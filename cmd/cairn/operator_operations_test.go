package main

import (
	"context"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

// Shared operator commands run the catalog's implementation, so their limits,
// decoding and invocation cannot drift from the API's.
func TestOperatorCommandsSharedWithCatalogResolveAndAgreeOnLimits(t *testing.T) {
	shared := 0
	for name, op := range operatorOperations {
		if op.Name != name {
			t.Errorf("operator command %q registered as %q", name, op.Name)
		}
		if op.Invoke == nil {
			t.Errorf("operator command %q has no invocation", name)
		}
		if op.Catalog == "" {
			continue
		}
		shared++
		spec, ok := localapi.LookupOperation(op.Catalog)
		if !ok || !spec.Invocable() {
			t.Errorf("operator command %q references catalog operation %q that cannot run outside HTTP", name, op.Catalog)
			continue
		}
		if op.limit() != localapi.RequestBodyLimit(op.Catalog) {
			t.Errorf("operator command %q limit %d differs from catalog %q limit %d", name, op.limit(), op.Catalog, localapi.RequestBodyLimit(op.Catalog))
		}
	}
	if shared == 0 {
		t.Fatal("no operator command shares the catalog")
	}
	if got := operatorOperations["capture-evidence"].limit(); got != localapi.MaxRequestBodyLimit {
		t.Fatalf("capture-evidence JSON limit %d no longer matches the evidence operation", got)
	}
}

// Every operator command that bypasses the catalog must be absent from it or
// documented as a deliberate semantic difference in the table.
func TestOperatorOnlyCommandsNamingCatalogOperationsAreDeliberate(t *testing.T) {
	deliberate := map[string]bool{"assess-run": true, "recompile": true, "compile": true, "index": true, "expand": true, "supersede": true}
	for name, op := range operatorOperations {
		if op.Catalog != "" {
			continue
		}
		if _, inCatalog := localapi.LookupOperation(name); inCatalog && !deliberate[name] {
			t.Errorf("operator command %q re-implements catalog operation %q without a recorded reason", name, name)
		}
	}
}

func TestOperatorCommandsDecodeLikeTheAPI(t *testing.T) {
	ctx := context.Background()
	for _, name := range []string{"grant", "create", "coordination-review"} {
		op := operatorOperations[name]
		_, err := op.run(ctx, nil, strings.NewReader(`{"bogus_field": 1}`))
		if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "unknown field") {
			t.Errorf("%s: unknown field accepted or misreported: %v", name, err)
		}
		_, err = op.run(ctx, nil, strings.NewReader(`{} {}`))
		if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "one JSON") {
			t.Errorf("%s: trailing request accepted or misreported: %v", name, err)
		}
	}
	_, err := operatorOperations["grant"].run(ctx, nil, strings.NewReader(strings.Repeat(" ", 128*1024+1)))
	if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "128 KiB") {
		t.Errorf("default limit not enforced: %v", err)
	}
	_, err = operatorOperations["verify-restore"].run(ctx, nil, strings.NewReader(strings.Repeat(" ", 20*1024*1024+1)))
	if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "size limit") {
		t.Errorf("custom limit not enforced: %v", err)
	}
}

func TestEveryCatalogOperationHasOfflineHelp(t *testing.T) {
	t.Setenv("HOME", "")
	t.Setenv("CAIRN_HOME", "")
	for _, op := range localapi.Operations() {
		got, err := run(context.Background(), []string{"agent", op.Name, "--help"}, unreadHelpInput{})
		if err != nil {
			t.Errorf("help for %q: %v", op.Name, err)
			continue
		}
		text := string(got.(commandHelp))
		for _, want := range []string{"cairn agent", op.Name, "Help does not read stdin"} {
			if !strings.Contains(text, want) {
				t.Errorf("help for %q lacks %q:\n%s", op.Name, want, text)
			}
		}
	}
	text := string(derivedAgentJSONHelp(mustOperation(t, "create")))
	for _, want := range []string{"request_id", "draft", "Encoded request limit: 512 KiB", "request-id"} {
		if !strings.Contains(text, want) {
			t.Errorf("derived help for create lacks %q:\n%s", want, text)
		}
	}
}

func mustOperation(t *testing.T, name string) localapi.OperationSpec {
	t.Helper()
	spec, ok := localapi.LookupOperation(name)
	if !ok {
		t.Fatalf("unknown operation %s", name)
	}
	return spec
}
