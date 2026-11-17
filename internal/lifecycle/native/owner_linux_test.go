package native

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/procpeer"
	"os"
	"path/filepath"
	"testing"
)

func fixturePin(t *testing.T, path string) Pin {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw)
	return Pin{path, hex.EncodeToString(sum[:])}
}

func ownerFixture(t *testing.T) (*Owner, ClaudeConfig) {
	t.Helper()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(root, "source.json")
	if err := os.WriteFile(source, []byte("{\"unrelated\":true}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	image, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(image)
	if err != nil {
		t.Fatal(err)
	}
	image = filepath.Join(root, "selected-image")
	if err := os.WriteFile(image, raw, 0500); err != nil {
		t.Fatal(err)
	}
	cfg := ClaudeConfig{Client: fixturePin(t, image), ClientVersion: "2.1.326", SourceSettings: fixturePin(t, source), Parent: root, Account: root, Work: root}
	// Synthetic negative-observation/cleanup subjects never qualify a native
	// image. Only the explicit installed-client fixture exercises that factory.
	owned := filepath.Join(root, "owned")
	if err := os.Mkdir(owned, 0700); err != nil {
		t.Fatal(err)
	}
	snapshot, err := install.InspectSettings(context.Background(), source)
	if err != nil {
		t.Fatal(err)
	}
	settings, err := install.CopySettings(context.Background(), snapshot, filepath.Join(owned, "settings.json"))
	if err != nil {
		t.Fatal(err)
	}
	file, err := os.Open(image)
	if err != nil {
		t.Fatal(err)
	}
	info, err := file.Stat()
	if err != nil {
		t.Fatal(err)
	}
	creator, err := procpeer.ProcessRefOf(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	lifetime, cancel := context.WithCancel(context.Background())
	o := &Owner{creator: creator, config: cfg, lifetime: lifetime, cancel: cancel, root: owned, settings: settings, image: file, imageInfo: info, phase: "never-launched", generation: 1}
	o.origin = o
	t.Cleanup(func() {
		if err := o.Close(); err != nil {
			t.Error(err)
		}
	})
	return o, cfg
}

func TestNativeFactoryRefusesUnqualifiedImageBeforeCreatingNamespace(t *testing.T) {
	_, cfg := ownerFixture(t)
	before, err := os.ReadDir(cfg.Parent)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := NewClaudeOwner(context.Background(), cfg); err == nil {
		t.Fatal("arbitrary caller-pinned image qualified as Claude")
	}
	after, err := os.ReadDir(cfg.Parent)
	if err != nil || len(after) != len(before) {
		t.Fatal("unqualified factory created a namespace", err)
	}
}

func TestNativeOwnerObservationCannotMintAdmission(t *testing.T) {
	var missing *Owner
	if got := missing.Inspect(context.Background()); got.Verdict != Unknown {
		t.Fatal("missing owner acquired authority", got)
	}
	var zero Owner
	if got := zero.Inspect(context.Background()); got.Verdict != Unknown {
		t.Fatal("empty owner acquired authority", got)
	}
	if err := json.Unmarshal([]byte(`{"phase":"joined","native_ids":[]}`), &zero); err == nil {
		t.Fatal("serialized caller claims created native authority")
	}
	o, _ := ownerFixture(t)
	copy := Owner{origin: o, creator: o.creator, config: o.config, root: o.root, settings: o.settings,
		image: o.image, imageInfo: o.imageInfo, phase: o.phase, generation: o.generation}
	if got := copy.Inspect(context.Background()); got.Verdict != Unknown {
		t.Fatal("copied owner acquired authority", got)
	}
	if _, err := json.Marshal(o); err == nil {
		t.Fatal("native owner capability was serializable")
	}
}

func TestNativeFactoryRefusesUnqualifiedImageWithoutCreatingNamespace(t *testing.T) {
	_, cfg := ownerFixture(t)
	before, err := os.ReadDir(cfg.Parent)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := NewClaudeOwner(context.Background(), cfg); err == nil {
		t.Fatal("arbitrary caller pin qualified a native client")
	}
	after, err := os.ReadDir(cfg.Parent)
	if err != nil || len(before) != len(after) {
		t.Fatal("unsupported image created native state", err)
	}
}

func TestNativeImageRefusesRestoredBytesAndTimestamp(t *testing.T) {
	o, cfg := ownerFixture(t)
	raw, err := os.ReadFile(cfg.Client.Path)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(cfg.Client.Path, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cfg.Client.Path, append(raw, 'x'), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cfg.Client.Path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(cfg.Client.Path, o.imageInfo.Mode()); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(cfg.Client.Path, o.imageInfo.ModTime(), o.imageInfo.ModTime()); err != nil {
		t.Fatal(err)
	}
	if err := checkImage(context.Background(), o.image, o.imageInfo, cfg.Client); err == nil {
		t.Fatal("restored byte/mtime mutation history was accepted")
	}
}

func TestNativeOwnerEmptyLocalStateIsNotQuiescenceAndInspectionCreatesNothing(t *testing.T) {
	o, _ := ownerFixture(t)
	before, err := os.ReadDir(o.root)
	if err != nil {
		t.Fatal(err)
	}
	got := o.Inspect(context.Background())
	if got.Verdict != Unknown || got.Phase != "never-launched" || len(got.NativeIDs) != 0 || got.Reason == "" {
		t.Fatal("unqualified empty state became quiescent", got)
	}
	after, err := os.ReadDir(o.root)
	if err != nil || len(before) != len(after) || len(after) != 1 {
		t.Fatal("pure inspection changed files", before, after, err)
	}
	if err := os.WriteFile(got.Settings, []byte("{\"unrelated\":false}"), 0600); err != nil {
		t.Fatal(err)
	}
	if changed := o.Inspect(context.Background()); changed.Verdict != Unknown || changed.Reason == got.Reason {
		t.Fatal("settings mutation was ignored", changed)
	}
}
