package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"syscall"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// These private process entry points are selected by check_r06_unit_census.py.
// They are not installed CLI operations or a native refresh protocol.
func TestR06UnitCensusProcess(t *testing.T) {
	path := os.Getenv("CAIRN_R06_CENSUS_PLAN")
	if path == "" {
		t.Skip("private disposable process entry point")
	}
	var plan struct {
		Mode, Root, Result string
		Catalog            WatchCensusPin
		Proposal           WatchCensusProposal
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := censusDecode(raw, &plan); err != nil {
		t.Fatal(err)
	}
	var result any
	if plan.Mode == "create" {
		f := censusFixtureAt(t, plan.Root)
		pin := censusTestJSON(t, filepath.Join(f.root, "catalog.json"), f.catalog)
		result = struct {
			Catalog             WatchCensusPin
			Proposal            WatchCensusProposal
			Root                string
			Manifests, Memories map[string]string
		}{pin, f.proposal, f.root, f.manifests, f.memories}
	} else if plan.Mode == "inspect" {
		profile := stage2ServicesProfile()
		host := NewFileHost(FileHostOptions{Profile: profile})
		host.Call = func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
			t.Fatal("census attempted an API call")
			return nil, nil
		}
		cli := &CLI{Profile: profile, Host: host}
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		census, err := cli.InspectWatchUnitCensus(ctx, plan.Catalog, plan.Proposal)
		message := ""
		if err != nil {
			message = err.Error()
		}
		result = struct {
			Census WatchUnitCensus
			Error  string
		}{census, message}
	} else {
		t.Fatal("unknown private fixture mode")
	}
	raw, err = json.MarshalIndent(result, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(plan.Result, raw, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestR06UnitCensusCallback(t *testing.T) {
	output := os.Getenv("CAIRN_R06_CENSUS_CALLBACK_OUTPUT")
	if output == "" {
		t.Skip("private owned callback entry point")
	}
	if len(os.Args) < 3 {
		t.Fatal("fixture callback requires entrypoint and config paths")
	}
	entry, config := os.Args[len(os.Args)-2], os.Args[len(os.Args)-1]
	pin := func(path string) WatchCensusPin {
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(raw)
		return WatchCensusPin{path, hex.EncodeToString(sum[:])}
	}
	entryPin, configPin := pin(entry), pin(config)
	var cfg struct {
		Binding string `json:"inbox_recall_binding"`
	}
	raw, err := os.ReadFile(config)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		t.Fatal(err)
	}
	bindingPin := pin(cfg.Binding)
	var manifest struct {
		Enabled bool `json:"enabled"`
	}
	raw, err = os.ReadFile(cfg.Binding)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &manifest); err != nil {
		t.Fatal(err)
	}
	current, err := nativeProcessReference("/proc", value.IntegerText(strconv.Itoa(os.Getpid())))
	if err != nil {
		t.Fatal(err)
	}
	group, err := syscall.Getpgid(os.Getpid())
	if err != nil || group <= 1 {
		t.Fatal("unsafe fixture process group", err)
	}
	parent, err := strconv.Atoi(current.Get("parent").Integer)
	if err != nil {
		t.Fatal(err)
	}
	process := WatchCensusProcess{PID: os.Getpid(), PGID: group, Parent: parent, Start: current.Get("start").Integer, Boot: nativeText(current.Get("boot"))}
	if delay := os.Getenv("CAIRN_R06_CENSUS_CALLBACK_DELAY"); delay != "" {
		ms, err := strconv.Atoi(delay)
		if err != nil || ms < 0 || ms > 2000 {
			t.Fatal("invalid bounded callback delay")
		}
		time.Sleep(time.Duration(ms) * time.Millisecond)
	}
	raw, err = json.Marshal(struct {
		Process                     WatchCensusProcess
		Config, EntryPoint, Binding WatchCensusPin
		Enabled                     bool
	}{process, configPin, entryPin, bindingPin, manifest.Enabled})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(output, raw, 0600); err != nil {
		t.Fatal(err)
	}
	ended := make(chan error, 1)
	go func() { _, err := io.Copy(io.Discard, os.Stdin); ended <- err }()
	select {
	case err := <-ended:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(30 * time.Second):
		t.Fatal("owned callback exceeded its bounded wait")
	}
}
