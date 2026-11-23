package binding

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeReceiptFixture(t *testing.T, dir, name string, raw []byte) string {
	t.Helper()
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func receiptFixtureDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.Chmod(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	return dir
}

func goOnlyReceiptFields(dir string) map[string]any {
	d := func(name string) map[string]any {
		return map[string]any{"path": filepath.Join(dir, name), "sha256": strings.Repeat("a", 64)}
	}
	binding := d("binding.json")
	binding["schema"] = binaryBindingSchema
	return map[string]any{
		"schema":               goOnlyCompatibilitySchema,
		"source_revision":      "test-rev",
		"binary":               d("cairn"),
		"binding":              binding,
		"memory_config":        d("memory.json"),
		"coordination_configs": []any{d("coordination-0.json")},
	}
}

func encodeReceipt(t *testing.T, fields map[string]any) []byte {
	t.Helper()
	raw, err := json.Marshal(fields)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

// TestGoOnlyReceiptNeedsNoPythonClosure pins the /2 contract: exact fields,
// no member reads, and the canonical role entrypoint without aliases.
func TestGoOnlyReceiptNeedsNoPythonClosure(t *testing.T) {
	dir := receiptFixtureDir(t)
	raw := encodeReceipt(t, goOnlyReceiptFields(dir))
	path := writeReceiptFixture(t, dir, "receipt.json", raw)
	r, err := readCompatibilityReceipt(path, digest(raw))
	if err != nil {
		t.Fatal(err)
	}
	if !r.goOnly() || r.SnapshotRoot != "" || len(r.Members) != 0 || r.Aliases != nil {
		t.Fatalf("unexpected /2 receipt: %+v", r)
	}
	sources, err := r.checkedClosure()
	if err != nil || len(sources) != 0 {
		t.Fatalf("a /2 receipt reads no closure: %v %v", sources, err)
	}
	if r.Aliases["memory-hook.py"] == "memory.py" {
		t.Fatal("a /2 receipt has no entrypoint aliases")
	}

	for name, change := range map[string]func(map[string]any){
		"python member field": func(f map[string]any) { f["members"] = map[string]any{} },
		"snapshot root":       func(f map[string]any) { f["snapshot_root"] = filepath.Join(dir, "snap") },
		"missing binary":      func(f map[string]any) { delete(f, "binary") },
		"binding schema":      func(f map[string]any) { f["binding"].(map[string]any)["schema"] = "cairn.inbox-recall-binding/2" },
		"relative config":     func(f map[string]any) { f["memory_config"].(map[string]any)["path"] = "memory.json" },
		"no coordination":     func(f map[string]any) { f["coordination_configs"] = []any{} },
	} {
		fields := goOnlyReceiptFields(dir)
		change(fields)
		if _, err := decodeCompatibilityReceipt(encodeReceipt(t, fields)); err == nil {
			t.Fatalf("%s: /2 receipt must refuse", name)
		}
	}
}

// TestPythonClosureReceiptStillRequiresEveryMember keeps /1 receipts (every
// generation staged before /2) on the exact member contract: removing
// memory.py from the snapshot fails closure verification.
func TestPythonClosureReceiptStillRequiresEveryMember(t *testing.T) {
	dir := receiptFixtureDir(t)
	codeMap := map[string]string{}
	members := map[string]any{}
	bodies := map[string][]byte{}
	for _, name := range closureMembers {
		body := []byte("# " + name + "\n")
		bodies[name] = body
		codeMap[name] = digest(body)
		members[name] = map[string]any{"sha256": digest(body), "bytes": len(body)}
	}
	encoded, err := asciiCodeMap(codeMap)
	if err != nil {
		t.Fatal(err)
	}
	snapshot := filepath.Join(dir, digest(encoded))
	if err := os.Mkdir(snapshot, 0o700); err != nil {
		t.Fatal(err)
	}
	for name, body := range bodies {
		writeReceiptFixture(t, snapshot, name, body)
	}
	fields := goOnlyReceiptFields(dir)
	fields["schema"] = compatibilitySchema
	fields["snapshot_root"] = snapshot
	fields["members"] = members
	fields["bootstrap"] = "lifecycle_compatibility.py"
	fields["loader"] = "lifecycle_bundle.py"
	fields["roles"] = map[string]any{"memory": "memory.py", "coordination": "coordination.py", "inbox_recall": "inbox_recall.py", "inbox_binding": "inbox_binding.py"}
	fields["aliases"] = map[string]any{}
	raw := encodeReceipt(t, fields)
	r, err := decodeCompatibilityReceipt(raw)
	if err != nil {
		t.Fatal(err)
	}
	if r.goOnly() {
		t.Fatal("/1 receipt decoded as Go-only")
	}
	if sources, err := r.checkedClosure(); err != nil || len(sources) != len(closureMembers) {
		t.Fatalf("complete /1 closure: %d %v", len(sources), err)
	}
	if err := os.Remove(filepath.Join(snapshot, "memory.py")); err != nil {
		t.Fatal(err)
	}
	if _, err := r.checkedClosure(); err == nil {
		t.Fatal("/1 closure without memory.py must refuse")
	}
	// A /1 receipt cannot drop its Python fields by claiming no members.
	delete(fields, "members")
	if _, err := decodeCompatibilityReceipt(encodeReceipt(t, fields)); err == nil {
		t.Fatal("/1 receipt without members must refuse")
	}
}
