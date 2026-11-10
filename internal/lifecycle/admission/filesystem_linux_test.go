package admission

import (
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"os"
	"path/filepath"
	"testing"
)

func TestOSProbeSourcePaths(t *testing.T) {
	d := t.TempDir()
	p := OSProbe{Directory: d}
	target := filepath.Join(d, "target")
	if err := os.Mkdir(target, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("target", filepath.Join(d, "link")); err != nil {
		t.Fatal(err)
	}
	resolved, err := p.Resolve(&value.Value{Kind: value.String, String: []rune("link/missing/../new")})
	if err != nil || string(resolved) != filepath.Join(target, "new") {
		t.Fatalf("source resolve %q: %v", resolved, err)
	}
	name := append([]byte(d+"/"), 0xff)
	if err := os.WriteFile(string(name), nil, 0600); err != nil {
		t.Fatal(err)
	}
	points := PathPoints(string(name))
	if got, err := PathBytes(points); err != nil || got != string(name) {
		t.Fatal("surrogateescape changed filename")
	}
	if found, err := p.Exists(points); err != nil || !found {
		t.Fatalf("surrogateescape lookup %v %v", found, err)
	}
	if _, err := PathBytes([]rune{0xd800}); err == nil {
		t.Fatal("unencodable surrogate replaced")
	}
}
