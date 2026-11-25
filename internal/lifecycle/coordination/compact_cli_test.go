package coordination

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func TestCompactReadBoundsLargeOutputAndHonorsOptOut(t *testing.T) {
	f := stage2Compact(t)
	spool := t.TempDir()
	envMap := map[string]string{"CAIRN_TOOL_OUTPUT_DIR": spool}
	f.c.Env = func(k string) string { return envMap[k] }

	largeBody := "BODY_HEAD_LINE\n" +
		strings.Repeat("inbox body filler line\n", 700) +
		"ERROR: critical inbox instruction line\n" +
		strings.Repeat("inbox tail padding\n", 100) +
		"BODY_TAIL_LINE"
	sum := sha256.Sum256([]byte(largeBody))
	f.c.Host.Call = func(_ context.Context, _ *value.Value, op string, _ *value.Value, _ NativeCallOptions) (*value.Value, error) {
		switch op {
		case "event-renew":
			d := nativeClone(f.delivery)
			d.Set("state", value.Text("leased"))
			return d, nil
		case "history":
			return nativeMap("record_id", stage2Turn, "historical", true, "versions", &value.Value{
				Kind:  value.Array,
				Items: []*value.Value{nativeMap("version", 1, "payload_available", true, "body", largeBody, "body_bytes", len([]byte(largeBody)), "body_sha256", hex.EncodeToString(sum[:]))},
			}), nil
		}
		t.Fatalf("unexpected op %s", op)
		return nil, nil
	}

	// Default 12000-byte bound truncates >12 KB output and spools full JSON.
	code, result, stderr := stage2CLIResult(t, f.c, "inbox", "compact", "read", "abcdef0123456789")
	if code != 0 || stderr != "" {
		t.Fatalf("compact read failed: code=%d stderr=%q", code, stderr)
	}
	if !exactTrue(result.Get("truncated")) {
		t.Fatalf("expected truncated=true, got %v", result)
	}
	fullPath := nativeText(result.Get("full_output_path"))
	if fullPath == "" {
		t.Fatal("missing full_output_path")
	}
	info, err := os.Stat(fullPath)
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("spool file stat/perm: %v %v", info, err)
	}
	saved, err := os.ReadFile(fullPath)
	if err != nil {
		t.Fatal(err)
	}
	decodedFull, err := value.DecodeBytes(saved, 0)
	if err != nil || nativeText(decodedFull.Get("body")) != largeBody {
		t.Fatalf("spooled JSON did not preserve exact body: %v", err)
	}
	excerpt := nativeText(result.Get("excerpt"))
	if !strings.Contains(excerpt, "ERROR: critical inbox instruction line") {
		t.Fatalf("excerpt missing diagnostic line:\n%s", excerpt)
	}

	// --output-max-bytes 0 and --full-output return full body unmodified.
	for _, flag := range [][]string{{"--output-max-bytes", "0"}, {"--full-output"}} {
		args := append([]string{"inbox", "compact", "read"}, flag...)
		args = append(args, "abcdef0123456789")
		code, fullRes, stderr := stage2CLIResult(t, f.c, args...)
		if code != 0 || stderr != "" || nativeText(fullRes.Get("body")) != largeBody {
			t.Fatalf("opt-out %v failed: code=%d stderr=%q", flag, code, stderr)
		}
	}
}
