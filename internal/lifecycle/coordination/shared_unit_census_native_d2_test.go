package coordination

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// This records the remaining reader boundary, not authenticated D2 coverage.
// The production session producer places native identity under agent. The
// current drain reader cannot map that unchanged shape to a selected config.
func TestWatchCensusProducedSessionNeedsDrainIdentityMapping(t *testing.T) {
	f := newCensusFixture(t)
	current := f.proposal.Bindings[0]
	writePrivateFile(t, filepath.Join(current.Directory, ".watch.lock"), nil, 0600)
	selected := censusD2RetainPolicy(t, f, f.proposal.Sessions[0])
	raw, err := os.ReadFile(selected.Path)
	if err != nil {
		t.Fatal(err)
	}
	state, err := value.DecodeTextWithObjectPolicy(raw, "utf-8", 0, value.DuplicateReject)
	if err != nil {
		t.Fatal(err)
	}
	if state.Get("native_session_id") != nil || nativeText(state.Get("agent").Get("native_session_id")) == "" {
		t.Fatal("test no longer uses the unchanged producer identity shape")
	}
	profile := stage2ServicesProfile()
	cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
	cli.Host.Call = func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
		t.Fatal("unmapped local identity must refuse before authenticated API reads")
		return nil, nil
	}
	catalog := censusTestJSON(t, filepath.Join(f.root, "catalog.json"), f.catalog)
	before := censusTestSnapshot(t, f.root)
	receipt, report, err := cli.captureUnitCensusDrain(context.Background(), current,
		&UnitControlCensus{Catalog: catalog, Proposal: f.proposal})
	if receipt != nil || err == nil || !strings.Contains(err.Error(), "D2 session binding or unit coverage is unknown") || report.FixtureReady {
		t.Fatalf("unknown producer/drain correspondence was accepted: %+v %v", report, err)
	}
	if len(report.DrainRequirements) != 1 || report.DrainRequirements[0].Session.Path != selected.Path ||
		report.DrainRequirements[0].Session.SHA256 != selected.SHA256 || report.DrainRequirements[0].Field != "inbox_result_policy" {
		t.Fatalf("combined reader lost the census requirement before refusing: %+v", report)
	}
	if !reflect.DeepEqual(before, censusTestSnapshot(t, f.root)) {
		t.Fatal("refusing combined reader changed producer files or retained policy")
	}
}
