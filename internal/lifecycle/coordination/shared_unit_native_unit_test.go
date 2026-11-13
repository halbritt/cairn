package coordination

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"syscall"
	"testing"
)

type nativeUnitFileIdentity struct {
	Mode     os.FileMode
	UID, GID uint32
	Dev, Ino uint64
}

func nativeUnitFileIdentities(t *testing.T, root string) map[string]nativeUnitFileIdentity {
	t.Helper()
	identities := map[string]nativeUnitFileIdentity{}
	if err := filepath.WalkDir(root, func(path string, _ os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		stat := info.Sys().(*syscall.Stat_t)
		identities[path] = nativeUnitFileIdentity{info.Mode(), stat.Uid, stat.Gid, stat.Dev, stat.Ino}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	return identities
}

// This counterexample exercises the existing product seam, not a native client
// or an authenticated drain. The unconditional refusal applies to every harness.
func TestOwnedUnitNativeLayersRequireReadback(t *testing.T) {
	for _, test := range []struct {
		name, role, relative string
	}{
		{"account-settings", "account", "settings.json"},
		{"repository-settings", "repository", ".claude/settings.json"},
		{"repository-content", "repository", "README.md"},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := newCensusFixture(t)
			current := f.proposal.Bindings[0]
			writePrivateFile(t, filepath.Join(current.Directory, ".watch.lock"), nil, 0600)
			catalog := censusTestJSON(t, filepath.Join(f.root, "catalog.json"), f.catalog)
			profile := stage2ServicesProfile()
			cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile})}
			baseline, err := cli.InspectWatchUnitCensus(context.Background(), catalog, f.proposal)
			if err != nil || !baseline.FixtureReady || len(baseline.Members) != 2 || len(baseline.Sessions) != 2 {
				t.Fatalf("empty finite baseline was not valid: %+v: %v", baseline, err)
			}
			root := baseline.Members[0].Account
			if test.role == "repository" {
				root = baseline.Sessions[0].Workspace
			}
			path := filepath.Join(root, test.relative)
			body := []byte("{\"hooks\":{},\"unrelated\":\"preserve\"}\n")
			if test.name == "repository-content" {
				body = []byte("Owned native workspace content must remain visible.\n")
			}
			writePrivateFile(t, path, body, 0600)
			beforeBytes := censusTestSnapshot(t, f.root)
			beforeIdentity := nativeUnitFileIdentities(t, f.root)
			want := test.role + " hook/config coverage is unknown: selected native settings/readback seam is unavailable"
			got, err := cli.InspectWatchUnitCensus(context.Background(), catalog, f.proposal)
			if err == nil || err.Error() != want || got.FixtureReady || got.NativeRefresh != "unobserved" {
				t.Fatalf("selected native layer did not retain its exact refusal: %+v: %v", got, err)
			}
			receipt, combined, err := cli.captureUnitCensusDrain(context.Background(), current,
				&UnitControlCensus{Catalog: catalog, Proposal: f.proposal})
			if err == nil || err.Error() != want || receipt != nil || combined.FixtureReady {
				t.Fatalf("combined /2 path bypassed missing native readback: receipt=%+v census=%+v: %v", receipt, combined, err)
			}
			if !reflect.DeepEqual(beforeBytes, censusTestSnapshot(t, f.root)) ||
				!reflect.DeepEqual(beforeIdentity, nativeUnitFileIdentities(t, f.root)) {
				t.Fatal("refusal changed file bytes, membership, modes, ownership or inode identity")
			}
			t.Logf("NOTREADY %s: standalone and combined /2 refuse before authenticated drain; selected path %s preserved", test.name, path)
		})
	}
}
