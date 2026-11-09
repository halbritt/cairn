package transcript

import (
	"io"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// These routes select synthetic kernel fixtures only. They provide no isolated
// launch, production domain/depth, filesystem, or per-site runtime qualification.
// 4300 is the explicit fixture digit parameter, never a production default.
type fixtureRoutes struct {
	failSite, failClass string
	failWriter          bool
}

func (fixtureRoutes) CheckDefinition(d value.SourceProfileDefinition, sites []string) error {
	if d.ReadIntegerDigits == nil || *d.ReadIntegerDigits != 4300 || d.WriteIntegerDigits == nil || *d.WriteIntegerDigits != 4300 {
		return &value.ProfileUnavailable{}
	}
	for _, site := range sites {
		if _, ok := d.Readers[site]; !ok {
			if _, ok = d.Writers[site]; !ok {
				return &value.ProfileUnavailable{}
			}
		}
	}
	return nil
}
func (f fixtureRoutes) Decode(site string, raw []byte, route value.ReaderRoute, digits int, bytesKernel func([]byte, int) (*value.Value, error), textKernel func([]byte, string, int) (*value.Value, error)) (*value.Value, error) {
	if site == f.failSite {
		if f.failClass != "" {
			return nil, &value.Error{Class: f.failClass, Message: "selected synthetic failure"}
		}
		return nil, &value.ProfileUnavailable{}
	}
	if route.Input == "bytes-json" {
		return bytesKernel(raw, digits)
	}
	return textKernel(raw, route.Codec.Name, digits)
}
func (f fixtureRoutes) Write(_ string, out io.Writer, v *value.Value, route value.WriterRoute, digits int, kernel func(io.Writer, *value.Value, value.WriterRoute, int) error) error {
	if f.failWriter {
		return &value.ProfileUnavailable{}
	}
	return kernel(out, v, route, digits)
}
func (fixtureRoutes) Filesystem() value.FilesystemRoutes { return nil }
func fixtureProfile(t *testing.T) *value.SourceReaderProfile {
	t.Helper()
	digits := 4300
	yes, no := true, false
	p := &value.SourceReaderProfile{Definition: value.SourceProfileDefinition{Schema: "cairn.source-reader-profile/1", State: "definition", ReadIntegerDigits: &digits, WriteIntegerDigits: &digits, Filesystem: &value.Codec{Name: "utf-8", Errors: "surrogateescape"}, Readers: map[string]value.ReaderRoute{}, Writers: map[string]value.WriterRoute{SiteQueueMarker: {Route: "dumps", Codec: &value.Codec{Name: "utf-8", Errors: "strict"}, EnsureASCII: &yes, Compact: &no, TrailingLF: &no, Domain: "synthetic-kernel-fixtures"}}, Domains: map[string]value.DomainSelection{"synthetic-kernel-fixtures": {Class: "synthetic-fixture", Route: "kernel-only"}}}, Routes: fixtureRoutes{}}
	for _, site := range []string{SiteLastJSONLRecord, SiteDaemonStartClaim, SiteStrictNativeLine, SiteFindCodexRefusal} {
		route := value.ReaderRoute{Input: "bytes-json", Domain: "synthetic-kernel-fixtures"}
		if site == SiteStrictNativeLine || site == SiteFindCodexRefusal {
			route.Input = "text-json"
			route.Codec = &value.Codec{Name: "utf-8", Errors: "strict"}
		}
		p.Definition.Readers[site] = route
	}
	return p
}
func parsePlainFixture(t *testing.T, raw []byte) (*Record, error) {
	t.Helper()
	return ParsePlainJSONObject(raw, fixtureProfile(t))
}
func strictFixture(t *testing.T, raw []byte) (*Record, error) {
	t.Helper()
	return StrictNativeLine(raw, fixtureProfile(t))
}
func readNativeFixture(t *testing.T, path string, l Locator) (*Record, error) {
	t.Helper()
	return ReadNativeRecord(path, l, fixtureProfile(t))
}
func readNativeAtFixture(t *testing.T, path string, l Locator, uid uint32) (*Record, error) {
	t.Helper()
	return ReadNativeRecordAt(path, l, uid, fixtureProfile(t))
}
func lastFixture(t *testing.T, path string) *Record {
	t.Helper()
	v, e := LastJSONLRecord(path, fixtureProfile(t))
	if e != nil {
		t.Fatal(e)
	}
	return v
}
func daemonClaimFixture(t *testing.T, cfg ClaudeConfig, state SessionState, attempt Attempt) (map[string]any, error) {
	t.Helper()
	return DaemonStartClaimEvidence(cfg, state, attempt, fixtureProfile(t))
}
func nativeRefusalFixture(t *testing.T, cfg ClaudeConfig, state SessionState, attempt Attempt, ls []Locator) (map[string]any, error) {
	t.Helper()
	return NativeRefusalProof(cfg, state, attempt, ls, fixtureProfile(t))
}
func findFixture(t *testing.T, r Reader, cfg CodexConfig, state SessionState, turn string) []Locator {
	t.Helper()
	ls, e := r.FindCodexRefusalLocators(cfg, state, turn)
	if e != nil {
		t.Fatal(e)
	}
	return ls
}
func queueMarkerFixture(t *testing.T, raw [][]byte, w Wake) bool {
	t.Helper()
	p := fixtureProfile(t)
	records := []*Record{}
	for _, line := range raw {
		v, e := ParsePlainJSONObject(line, p)
		if e != nil {
			t.Fatal(e)
		}
		records = append(records, v)
	}
	present, e := NewReader(p).queueMarkerPresent(records, w)
	if e != nil {
		t.Fatal(e)
	}
	return present
}
func fixtureObject(v any) map[string]any { m, _ := v.(map[string]any); return m }
func fixtureText(v any) string {
	if node, ok := v.(*Record); ok {
		return text(node)
	}
	s, _ := v.(string)
	return s
}
func hasInteger(v *Record, want int64) bool { n, ok := exactInt(v); return ok && n == want }
