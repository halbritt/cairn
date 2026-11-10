package binding

import (
	"strings"
	"testing"
)

func TestBinaryRejectsNoncanonicalPath(t *testing.T) {
	for _, p := range []string{"relative", "/tmp//binary", "/tmp/../binary"} {
		if _, err := openBinary(p); err == nil || !strings.Contains(err.Error(), "canonical") {
			t.Fatalf("noncanonical path %q: %v", p, err)
		}
	}
}
