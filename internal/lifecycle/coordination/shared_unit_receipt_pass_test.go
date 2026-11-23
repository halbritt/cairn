package coordination

import (
	"context"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// These are policy parity tests of the census-only reader, not native or
// authenticated qualification. Genuine producer process gates run separately.
func TestUnitReceiptPassSelectionParity(t *testing.T) {
	for _, mutation := range []string{"none", "extra", "schema", "role", "config", "entrypoint", "timeout", "nan", "shell", "provenance", "roles", "alias-name", "alias-target", "closure", "member-shape", "member-bytes", "member-limit", "member-digest", "member-disk", "binding-schema", "descriptor", "empty-coordinators", "snapshot", "binary-digest", "binary-mode", "member-parent"} {
		t.Run(mutation, func(t *testing.T) {
			f := newCompatFixture(t)
			raw, err := os.ReadFile(f.receiptPath)
			if err != nil {
				t.Fatal(err)
			}
			receipt, err := value.DecodeBytes(raw, 0)
			if err != nil {
				t.Fatal(err)
			}
			selection := *f.compat
			member := receipt.Get("members").Get("coordination.py")
			switch mutation {
			case "extra":
				receipt.Set("extra", value.Text("unknown"))
			case "schema":
				receipt.Set("schema", value.Text("unknown"))
			case "role":
				selection.Role = "unknown"
			case "config":
				selection.ConfigPath = filepath.Join(f.root, "other.json")
			case "entrypoint":
				selection.Entrypoint = "other.py"
			case "timeout":
				selection.HookTimeout = 16
			case "nan":
				selection.HookTimeout = math.NaN()
			case "shell":
				selection.Shell = "bin/sh"
			case "provenance":
				receipt.Set("source_revision", value.Boolean(true))
			case "roles":
				receipt.Get("roles").Set("memory", value.Text("coordination.py"))
			case "alias-name":
				receipt.Get("aliases").Set("../other.py", value.Text("coordination.py"))
			case "alias-target":
				receipt.Get("aliases").Set("other.py", value.Text("unknown.py"))
			case "closure":
				receipt.Get("aliases").Set("other.py", value.Text("coordination.py"))
			case "member-shape":
				member.Set("extra", value.Text("unknown"))
			case "member-bytes":
				member.Set("bytes", value.Boolean(true))
			case "member-limit":
				member.Set("bytes", value.IntegerText("2097153"))
			case "member-digest":
				member.Set("sha256", value.Text(strings.Repeat("0", 64)))
			case "member-disk":
				writePrivateFile(t, filepath.Join(nativeText(receipt.Get("snapshot_root")), "coordination.py"), []byte("changed bytes"), 0400)
			case "binding-schema":
				receipt.Get("binding").Set("schema", value.Text("cairn.inbox-recall-binding/2"))
			case "descriptor":
				receipt.Get("memory_config").Set("sha256", value.Text("invalid"))
			case "empty-coordinators":
				receipt.Set("coordination_configs", &value.Value{Kind: value.Array})
			case "snapshot":
				receipt.Set("snapshot_root", value.Text(filepath.Join(f.root, "wrong-snapshot")))
			case "binary-digest":
				receipt.Get("binary").Set("sha256", value.Text(strings.Repeat("0", 64)))
			case "binary-mode":
				if err := os.Chmod(nativeText(receipt.Get("binary").Get("path")), 0600); err != nil {
					t.Fatal(err)
				}
			case "member-parent":
				if err := os.Chmod(nativeText(receipt.Get("snapshot_root")), 0755); err != nil {
					t.Fatal(err)
				}
			}
			generic := selection.validateReceiptSelection(receipt)
			r := censusVerifiedRead(t, f.root)
			tracked := r.verifyUnitReceipt(&selection, receipt)
			if (generic == nil) != (tracked == nil) || (tracked == nil) != (mutation == "none") {
				t.Fatalf("generic/census policy diverged: generic=%v census=%v", generic, tracked)
			}
			if selection.receiptVerified {
				t.Fatal("census validation set generic authority state")
			}
			if mutation == "none" {
				before := r.local.verified.hashReads
				if before != len(CompatibilityClosureMembers)+1 {
					t.Fatalf("closure/image not actually tracked: %d", before)
				}
				if err := r.verifyUnitReceipt(&selection, receipt); err != nil || r.local.verified.hashReads != before {
					t.Fatal("second receipt validation repeated build hashing", err)
				}
				if err := r.readback(context.Background()); err != nil || r.local.verified.hashReads != before*2 {
					t.Fatal("receipt bytes did not receive fresh readback", err)
				}
			}
		})
	}
}

// TestUnitReceiptPassGoOnlyParity keeps the watcher's tracked pass and the
// generic validator on one /2 predicate, and checks that a /2 receipt tracks
// only the image: no Python member is read.
func TestUnitReceiptPassGoOnlyParity(t *testing.T) {
	for _, mutation := range []string{"none", "python-field", "schema-binding", "alias-entrypoint", "config", "provenance", "descriptor", "empty-coordinators", "binary-digest", "binary-mode"} {
		t.Run(mutation, func(t *testing.T) {
			f := newCompatFixture(t)
			raw, err := os.ReadFile(f.receiptPath)
			if err != nil {
				t.Fatal(err)
			}
			old, err := value.DecodeBytes(raw, 0)
			if err != nil {
				t.Fatal(err)
			}
			receipt := value.Mapping(value.Field("schema", value.Text(GoOnlyCompatibilitySchema)))
			for _, key := range []string{"source_revision", "binary", "binding", "memory_config", "coordination_configs"} {
				receipt.Set(key, old.Get(key))
			}
			if err := os.RemoveAll(nativeText(old.Get("snapshot_root"))); err != nil {
				t.Fatal(err)
			}
			selection := *f.compat
			switch mutation {
			case "python-field":
				receipt.Set("members", old.Get("members"))
			case "schema-binding":
				receipt.Get("binding").Set("schema", value.Text("cairn.inbox-recall-binding/2"))
			case "alias-entrypoint":
				selection.Entrypoint = "coordination-hook.py"
			case "config":
				selection.ConfigPath = filepath.Join(f.root, "other.json")
			case "provenance":
				receipt.Set("source_revision", value.Boolean(true))
			case "descriptor":
				receipt.Get("memory_config").Set("sha256", value.Text("invalid"))
			case "empty-coordinators":
				receipt.Set("coordination_configs", &value.Value{Kind: value.Array})
			case "binary-digest":
				receipt.Get("binary").Set("sha256", value.Text(strings.Repeat("0", 64)))
			case "binary-mode":
				if err := os.Chmod(nativeText(receipt.Get("binary").Get("path")), 0600); err != nil {
					t.Fatal(err)
				}
			}
			generic := selection.validateReceiptSelection(receipt)
			r := censusVerifiedRead(t, f.root)
			tracked := r.verifyUnitReceipt(&selection, receipt)
			if (generic == nil) != (tracked == nil) || (tracked == nil) != (mutation == "none") {
				t.Fatalf("generic/census /2 policy diverged: generic=%v census=%v", generic, tracked)
			}
			if mutation == "none" && r.local.verified.hashReads != 1 {
				t.Fatalf("a /2 receipt tracks only its image: %d reads", r.local.verified.hashReads)
			}
		})
	}
}
