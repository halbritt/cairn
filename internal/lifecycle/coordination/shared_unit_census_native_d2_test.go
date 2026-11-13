package coordination

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Mapping actual producer identity still cannot supply missing API or retained
// attempt/journal evidence. These are refusal tests, not authenticated coverage.
func TestWatchCensusProducedSessionPreservesDrainRefusals(t *testing.T) {
	for _, problem := range []string{"api-unavailable", "no-retained-attempt"} {
		t.Run(problem, func(t *testing.T) {
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
			cli := watchNativeIdentityReadCLI(t)
			want := "D2 exact retained attempt identity is unknown"
			if problem == "api-unavailable" {
				want = "fixture API unavailable"
				cli.Host.Call = func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
					return nil, errors.New(want)
				}
			}
			catalog := censusTestJSON(t, filepath.Join(f.root, "catalog.json"), f.catalog)
			before := censusTestSnapshot(t, f.root)
			receipt, report, err := cli.captureUnitCensusDrain(context.Background(), current,
				&UnitControlCensus{Catalog: catalog, Proposal: f.proposal})
			if receipt != nil || err == nil || !strings.Contains(err.Error(), want) || report.FixtureReady {
				t.Fatalf("missing API/retained evidence was accepted or identity mapping failed: %+v %v", report, err)
			}
			if len(report.DrainRequirements) != 1 || report.DrainRequirements[0].Session.Path != selected.Path ||
				report.DrainRequirements[0].Session.SHA256 != selected.SHA256 || report.DrainRequirements[0].Field != "inbox_result_policy" {
				t.Fatalf("combined reader lost the census requirement before refusing: %+v", report)
			}
			if !reflect.DeepEqual(before, censusTestSnapshot(t, f.root)) {
				t.Fatal("refusing combined reader changed producer files or retained policy")
			}
		})
	}
}
