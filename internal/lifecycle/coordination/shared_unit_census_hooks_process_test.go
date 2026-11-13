package coordination

import (
	"context"
	"encoding/json"
	"os"
	"testing"
)

// Explicitly driven by check_r06_native_hook_census.py with disposable files.
func TestR06NativeHookCensusProcess(t *testing.T) {
	path := os.Getenv("CAIRN_R06_NATIVE_HOOK_CENSUS_PLAN")
	if path == "" {
		t.Skip("private emitted/native-hook census entry point")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var plan struct {
		Catalog  WatchCensusPin
		Proposal WatchCensusProposal
		Result   string
	}
	if err := hookDecode(raw, &plan); err != nil {
		t.Fatal(err)
	}
	profile := stage2ServicesProfile()
	host := NewFileHost(FileHostOptions{Profile: profile})
	cli := &CLI{Profile: profile, Host: host}
	report, inspectErr := cli.InspectWatchUnitCensus(context.Background(), plan.Catalog, plan.Proposal)
	message := ""
	if inspectErr != nil {
		message = inspectErr.Error()
	}
	encoded, err := json.Marshal(struct {
		Census WatchUnitCensus
		Error  string
	}{report, message})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(plan.Result, append(encoded, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
}
