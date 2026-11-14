package coordination

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

type unboundDrainFixture struct {
	Catalog         WatchCensusPin
	Proposal        WatchCensusProposal
	Receipt, Config WatchCensusPin
	Root            string
}

func readUnboundDrainFixture(t *testing.T, variable string) unboundDrainFixture {
	t.Helper()
	path := os.Getenv(variable)
	if path == "" {
		t.Skip("requires NEW genuine R02/R04 producer material: " + variable)
	}
	var f unboundDrainFixture
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := censusDecode(raw, &f); err != nil {
		t.Fatal(err)
	}
	if len(f.Proposal.Bindings) != 1 {
		t.Fatal("fixture needs its actual selected directory")
	}
	writePrivateFile(t, filepath.Join(f.Proposal.Bindings[0].Directory, ".watch.lock"), nil, 0600)
	return f
}

func (f unboundDrainFixture) capture(t *testing.T, cli *CLI) *WatchDrainReceipt {
	t.Helper()
	receipt, _, err := cli.captureUnitCensusDrain(context.Background(), f.Proposal.Bindings[0], &UnitControlCensus{Catalog: f.Catalog, Proposal: f.Proposal})
	if err != nil || receipt == nil {
		t.Fatal("genuine unbound drain capture refused", err)
	}
	return receipt
}

// These status doubles test finite plumbing and refusal, not authentication or
// native qualification. The separate process entry below uses the real API.
func unboundDrainReadCLI(t *testing.T) (*CLI, *int) {
	t.Helper()
	cli := watchNativeIdentityReadCLI(t)
	call := cli.Host.Call
	count := 0
	cli.Host.Call = func(ctx context.Context, cfg *value.Value, op string, body *value.Value, options NativeCallOptions) (*value.Value, error) {
		count++
		return call(ctx, cfg, op, body, options)
	}
	return cli, &count
}

func TestR06UnboundDrainAssociationProcess(t *testing.T) {
	f := readUnboundDrainFixture(t, "CAIRN_R06_UNBOUND_FIXTURE")
	ctx := context.Background()
	cli, calls := unboundDrainReadCLI(t)
	t.Run("capture-and-independent-recheck-preserve-bytes", func(t *testing.T) {
		before := censusTestSnapshot(t, f.Root)
		receipt := f.capture(t, cli)
		count := *calls
		if err := receipt.recheck(ctx); err != nil || *calls <= count {
			t.Fatal("receipt did not independently reread selection/status", err, *calls)
		}
		if !reflect.DeepEqual(before, censusTestSnapshot(t, f.Root)) {
			t.Fatal("capture/recheck changed the genuine unbound material")
		}
		if !nativeNone(mustCurrentnessRead(t, f.Config.Path).Get("inbox_recall_binding")) {
			t.Fatal("unbound profile was rewritten as bound")
		}
	})
	t.Run("public-capture-has-no-private-association", func(t *testing.T) {
		count := *calls
		if _, err := cli.CaptureWatchDrain(ctx, f.Proposal.Bindings[0], []*value.Value{}); err == nil || !strings.Contains(err.Error(), "private verified census association") || *calls != count {
			t.Fatal("public caller injected an unbound association", err)
		}
	})
	for _, kind := range []string{"missing", "duplicate"} {
		t.Run("capture-"+kind, func(t *testing.T) {
			var catalog WatchCensusCatalog
			raw, err := os.ReadFile(f.Catalog.Path)
			if err != nil {
				t.Fatal(err)
			}
			if err := censusDecode(raw, &catalog); err != nil {
				t.Fatal(err)
			}
			if kind == "missing" {
				catalog.UnboundReceipts = nil
			} else {
				catalog.UnboundReceipts = append(catalog.UnboundReceipts, f.Receipt)
			}
			directory, err := os.MkdirTemp(f.Root, "drain-catalog-")
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = os.RemoveAll(directory) })
			pin := censusTestJSON(t, filepath.Join(directory, "catalog.json"), catalog)
			count := *calls
			if _, _, err := cli.captureUnitCensusDrain(ctx, f.Proposal.Bindings[0], &UnitControlCensus{Catalog: pin, Proposal: f.Proposal}); err == nil || *calls != count {
				t.Fatal("invalid association reached status reads", err)
			}
		})
	}
	t.Run("lost-private-association", func(t *testing.T) {
		receipt := f.capture(t, cli)
		receipt.census.associationFacts = nil
		count := *calls
		if err := receipt.recheck(ctx); err == nil || !strings.Contains(err.Error(), "private verified census association") || *calls != count {
			t.Fatal("lost private association survived recheck", err)
		}
	})
	for _, kind := range []string{"receipt", "memory", "source-member", "binary", "resource", "same-byte-inode", "same-byte-memory", "credential-inode", "alias"} {
		t.Run("recheck-"+kind, func(t *testing.T) {
			receipt := f.capture(t, cli)
			path := f.Receipt.Path
			produced := mustCurrentnessRead(t, f.Receipt.Path)
			switch kind {
			case "memory":
				path = nativeText(produced.Get("memory_config").Get("path"))
			case "source-member":
				path = filepath.Join(nativeText(produced.Get("snapshot_root")), "coordination.py")
			case "binary":
				path = nativeText(produced.Get("binary").Get("path"))
			case "resource":
				cfg := mustCurrentnessRead(t, f.Config.Path)
				path = filepath.Join(nativeText(cfg.Get("state_dir")), "unknown.json")
				writePrivateFile(t, path, []byte("{}"), 0600)
				t.Cleanup(func() { _ = os.Remove(path) })
			case "same-byte-inode", "same-byte-memory", "credential-inode", "alias":
				path = f.Config.Path
				if kind == "same-byte-memory" {
					path = nativeText(produced.Get("memory_config").Get("path"))
				}
				if kind == "credential-inode" {
					path = nativeText(mustCurrentnessRead(t, f.Config.Path).Get("token_file"))
				}
				backup := filepath.Join(f.Root, "drain-original-config.json")
				info, err := os.Stat(path)
				if err != nil {
					t.Fatal(err)
				}
				var raw []byte
				if kind != "credential-inode" {
					raw, err = os.ReadFile(path)
					if err != nil {
						t.Fatal(err)
					}
				}
				if err := os.Rename(path, backup); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					_ = os.Remove(path)
					if err := os.Rename(backup, path); err != nil {
						t.Error(err)
					}
				})
				if kind == "alias" {
					if err := os.Symlink(backup, path); err != nil {
						t.Fatal(err)
					}
				} else {
					writePrivateFile(t, path, raw, info.Mode().Perm())
				}
			}
			if kind == "receipt" || kind == "memory" || kind == "source-member" || kind == "binary" {
				info, err := os.Stat(path)
				if err != nil {
					t.Fatal(err)
				}
				raw, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(path, 0600); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					if err := os.Chmod(path, 0600); err != nil {
						t.Error(err)
					}
					if err := os.WriteFile(path, raw, 0600); err != nil {
						t.Error(err)
					}
					if err := os.Chmod(path, info.Mode().Perm()); err != nil {
						t.Error(err)
					}
				})
				if err := os.WriteFile(path, append(raw, ' '), 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(path, info.Mode().Perm()); err != nil {
					t.Fatal(err)
				}
			}
			count := *calls
			err := receipt.recheck(ctx)
			if err == nil || !strings.Contains(err.Error(), "D6 drain receipt invalidated") || *calls != count {
				t.Fatal("changed/unknown association reached status reads", err)
			}
			if (kind == "same-byte-memory" || kind == "credential-inode") && !strings.Contains(err.Error(), "association lost or changed") {
				t.Fatal("byte-identical replacement was not detected by identity", err)
			}
		})
	}
	t.Run("drift-during-final-status-read", func(t *testing.T) {
		receipt := f.capture(t, cli)
		read := cli.Host.Call
		t.Cleanup(func() { cli.Host.Call = read })
		cli.Host.Call = func(ctx context.Context, cfg *value.Value, op string, body *value.Value, options NativeCallOptions) (*value.Value, error) {
			if op == "event-watch" {
				writePrivateFile(t, f.Receipt.Path, []byte("{}"), 0600)
			}
			return read(ctx, cfg, op, body, options)
		}
		raw, err := os.ReadFile(f.Receipt.Path)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { writePrivateFile(t, f.Receipt.Path, raw, 0400) })
		if err := receipt.recheck(ctx); err == nil || !strings.Contains(err.Error(), "census pin changed") {
			t.Fatal("in-read drift survived", err)
		}
	})
	t.Run("status-unknown-is-not-settlement", func(t *testing.T) {
		receipt := f.capture(t, cli)
		read := cli.Host.Call
		t.Cleanup(func() { cli.Host.Call = read })
		cli.Host.Call = func(context.Context, *value.Value, string, *value.Value, NativeCallOptions) (*value.Value, error) {
			return nil, errors.New("NOT_FOUND")
		}
		if err := receipt.recheck(ctx); err == nil || !strings.Contains(err.Error(), "D4 wake-attempts visibility is unknown") {
			t.Fatal("unknown status survived", err)
		}
		if _, _, err := cli.captureUnitCensusDrain(ctx, f.Proposal.Bindings[0], &UnitControlCensus{Catalog: f.Catalog, Proposal: f.Proposal}); err == nil {
			t.Fatal("unknown status authorized capture")
		}
	})
	t.Run("own-preparation-only", func(t *testing.T) {
		receipt := f.capture(t, cli)
		current := f.Proposal.Bindings[0]
		var next []install.File
		for name, pin := range current.Pins {
			next = append(next, install.File{Name: name, Path: filepath.Join(current.Directory, name), SHA256: pin})
		}
		retained := filepath.Join(filepath.Dir(current.Directory), "drain-retained")
		plan := map[string]any{"schema": "cairn.unit-control/2", "current": map[string]any{"directory": current.Directory, "pins": current.Pins}, "next": next, "retained": retained, "census": map[string]any{"Catalog": f.Catalog, "Proposal": f.Proposal, "Retained": []WatchBindings{}}}
		raw, err := json.Marshal(plan)
		if err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(f.Root, "drain-control-plan.json")
		receipt.census.planPath, receipt.census.planPin = path, writePrivateFile(t, path, raw, 0600)
		t.Cleanup(func() { _ = os.Remove(path) })
		swap, err := openWatchBindingsSwap(ctx, current, retained, next)
		if err != nil {
			t.Fatal(err)
		}
		defer swap.parent.Close()
		defer mustRelease(t, swap.lease)
		t.Cleanup(func() { _ = os.RemoveAll(retained) })
		if err := swap.prepare(); err != nil {
			t.Fatal(err)
		}
		if err := receipt.recheckPrepared(ctx, swap); err != nil {
			t.Fatal(err)
		}
		if err := receipt.census.recheck(ctx, swap); err != nil {
			t.Fatal(err)
		}
		unknown := filepath.Join(filepath.Dir(current.Directory), "unexplained")
		if err := os.Mkdir(unknown, 0700); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = os.Remove(unknown) })
		if err := receipt.recheckPrepared(ctx, swap); err == nil {
			t.Fatal("own preparation hid another directory")
		}
	})
}

// Real API/PG read operations against a NEW fixture; no native session, client,
// active hook layer, authenticated retained D2 success or readiness is claimed.
func TestR06UnboundDrainAuthenticatedProcess(t *testing.T) {
	f := readUnboundDrainFixture(t, "CAIRN_R06_UNBOUND_AUTH_FIXTURE")
	profile := stage2ServicesProfile()
	cli := &CLI{Host: NewFileHost(FileHostOptions{Profile: profile}), Profile: profile}
	call := cli.Host.Call
	reads := map[string]int{}
	cli.Host.Call = func(ctx context.Context, cfg *value.Value, op string, body *value.Value, options NativeCallOptions) (*value.Value, error) {
		if op != "wake-attempts" && op != "event-watch" {
			t.Fatalf("empty unbound fixture attempted a non-read operation: %s", op)
		}
		result, err := call(ctx, cfg, op, body, options)
		if err == nil {
			reads[op]++
		}
		return result, err
	}
	receipt := f.capture(t, cli)
	if err := receipt.recheck(context.Background()); err != nil {
		t.Fatal(err)
	}
	if reads["wake-attempts"] != 2 || reads["event-watch"] != 2 {
		t.Fatal("capture and receipt recheck did not each authenticate status reads", reads)
	}
	t.Logf("actual authenticated read operations, capture+recheck: %v", reads)
	if len(receipt.d2Sessions) != 0 {
		t.Fatal("empty fixture acquired retained D2 correspondence")
	}
	if len(receipt.Facts()) == 0 {
		t.Fatal("actual authenticated reads produced no receipt facts")
	}
}
