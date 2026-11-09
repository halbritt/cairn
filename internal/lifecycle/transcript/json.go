package transcript

import (
	"errors"
	"math"
	"math/big"
	"slices"
	"strings"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// These are consumer sites, not a production profile or an admitted domain.
// SourceRoutes must supply and qualify each site's actual reader/writer policy.
const (
	SiteLastJSONLRecord  = "transcript.last_jsonl_record"
	SiteDaemonStartClaim = "transcript.daemon_start_claim_evidence"
	SiteStrictNativeLine = "transcript.strict_native_line"
	SiteFindCodexRefusal = "transcript.find_codex_refusal_locators"
	SiteQueueMarker      = "transcript.codex_queue_rollout_proof"
)

type Record = value.Value

func ParsePlainJSONObject(raw []byte, p *value.SourceReaderProfile) (*Record, error) {
	return decodeObject(p, SiteFindCodexRefusal, raw)
}
func ParsePlainBytesJSONObject(raw []byte, p *value.SourceReaderProfile) (*Record, error) {
	return decodeObject(p, SiteLastJSONLRecord, raw)
}
func decodeObject(p *value.SourceReaderProfile, site string, raw []byte) (*Record, error) {
	var v *Record
	var e error
	if site == SiteStrictNativeLine {
		v, e = p.DecodeWithObjectPolicy(site, raw, value.DuplicateReject)
	} else {
		v, e = p.Decode(site, raw)
	}
	if e != nil {
		return nil, e
	}
	if v == nil || v.Kind != value.Object {
		return nil, invalid("native record is not an object")
	}
	return v, nil
}
func StrictNativeLine(raw []byte, p *value.SourceReaderProfile) (*Record, error) {
	v, e := decodeObject(p, SiteStrictNativeLine, raw)
	if e != nil && sourceReadError(e, "UnicodeDecodeError", "ValueError", "JSONDecodeError") {
		return nil, invalid("native record is not strict JSON", e)
	}
	return v, e
}
func sourceReadError(err error, classes ...string) bool {
	class := value.ErrorClass(err)
	for _, expected := range classes {
		if class == expected {
			return true
		}
	}
	return false
}
func strictSkip(err error) bool { var te *TranscriptError; return errors.As(err, &te) }

func field(v *Record, key string) *Record { return v.Get(key) }
func object(v *Record) *Record {
	if v == nil || v.Kind != value.Object {
		return nil
	}
	return v
}
func text(v *Record) string {
	if v == nil || v.Kind != value.String {
		return ""
	}
	for _, r := range v.String {
		if !utf8.ValidRune(r) {
			return ""
		}
	}
	return string(v.String)
}
func stringIs(v *Record, s string) bool {
	return v != nil && v.Kind == value.String && slices.Equal(v.String, []rune(s))
}
func nonemptyString(v *Record) bool { return v != nil && v.Kind == value.String && len(v.String) > 0 }
func nonblankString(v *Record) bool {
	if v == nil || v.Kind != value.String {
		return false
	}
	for _, r := range v.String {
		if !pythonWhitespace(r) {
			return true
		}
	}
	return false
}
func isNull(v *Record) bool         { return v == nil || v.Kind == value.Null }
func isBool(v *Record, b bool) bool { return v != nil && v.Kind == value.Bool && v.Boolean == b }
func keys(m *Record, expected ...string) bool {
	if m == nil || m.Kind != value.Object || len(m.Members) != len(expected) {
		return false
	}
	for _, key := range expected {
		if m.Get(key) == nil {
			return false
		}
	}
	return true
}

// Record comparisons follow Python container equality, including numeric bool
// equality and the json decoder's shared NaN constant inside parsed containers.
// Values remain Foundation nodes; no string/key flattening or JSON fork occurs.
func sameRecord(a, b *Record) bool {
	type pair struct{ a, b *Record }
	stack := []pair{{a, b}}
	for len(stack) > 0 {
		p := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		a, b := p.a, p.b
		if a == b {
			continue
		}
		if a == nil || b == nil {
			return false
		}
		if numericKind(a) && numericKind(b) {
			if !sameNumber(a, b) {
				return false
			}
			continue
		}
		if a.Kind != b.Kind {
			return false
		}
		switch a.Kind {
		case value.Null:
		case value.String:
			if !slices.Equal(a.String, b.String) {
				return false
			}
		case value.Array:
			if len(a.Items) != len(b.Items) {
				return false
			}
			for i := range a.Items {
				stack = append(stack, pair{a.Items[i], b.Items[i]})
			}
		case value.Object:
			if len(a.Members) != len(b.Members) {
				return false
			}
			for _, am := range a.Members {
				var match *Record
				found := false
				for _, bm := range b.Members {
					if slices.Equal(am.Key, bm.Key) {
						match = bm.Value
						found = true
						break
					}
				}
				if !found {
					return false
				}
				stack = append(stack, pair{am.Value, match})
			}
		default:
			return false
		}
	}
	return true
}
func numericKind(v *Record) bool {
	return v.Kind == value.Bool || v.Kind == value.Int || v.Kind == value.Float
}
func sameNumber(a, b *Record) bool {
	if a.Kind == value.Float && b.Kind == value.Float {
		return a.Number == b.Number || math.IsNaN(a.Number) && math.IsNaN(b.Number)
	}
	rat := func(v *Record) *big.Rat {
		switch v.Kind {
		case value.Bool:
			if v.Boolean {
				return big.NewRat(1, 1)
			}
			return big.NewRat(0, 1)
		case value.Int:
			n, ok := new(big.Int).SetString(v.Integer, 10)
			if ok {
				return new(big.Rat).SetInt(n)
			}
		case value.Float:
			return new(big.Rat).SetFloat64(v.Number)
		}
		return nil
	}
	x, y := rat(a), rat(b)
	return x != nil && y != nil && x.Cmp(y) == 0
}

func pythonStrip(s string) string { return strings.TrimFunc(s, pythonWhitespace) }
