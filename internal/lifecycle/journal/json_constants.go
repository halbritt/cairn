package journal

import (
	"bytes"
	"encoding/json"
	"math"
	"sort"
	"strconv"
)

// These identities model json.decoder._CONSTANTS, not arbitrary float objects.
// Only the default-JSON ingress and the journal JSON reader select them.
type jsonConstant struct{ token string }

var defaultConstants = map[string]*jsonConstant{
	"NaN": {"NaN"}, "Infinity": {"Infinity"}, "-Infinity": {"-Infinity"},
}

// DefaultJSONConstants retains the constant identities of Source's default
// json.loads after the frontend's lossless projection. It must not be applied
// to values constructed by another producer whose identities are unknown.
func DefaultJSONConstants(v any) any {
	switch x := v.(type) {
	case json.Number:
		if c := defaultConstants[string(x)]; c != nil {
			return c
		}
	case map[string]any:
		for k, child := range x {
			x[k] = DefaultJSONConstants(child)
		}
	case []any:
		for i, child := range x {
			x[i] = DefaultJSONConstants(child)
		}
	}
	return v
}

// Mask only complete value tokens, retaining byte offsets for RawMessage
// slices. Strings, keys and malformed token suffixes remain unchanged.
func maskConstants(raw []byte) []byte {
	masked := raw
	quoted, escaped := false, false
	boundary := func(b byte) bool {
		return b == ' ' || b == '\t' || b == '\r' || b == '\n' || b == ',' || b == ':' || b == '[' || b == ']' || b == '}'
	}
	for i := 0; i < len(raw); i++ {
		if quoted {
			if escaped {
				escaped = false
			} else if raw[i] == '\\' {
				escaped = true
			} else if raw[i] == '"' {
				quoted = false
			}
			continue
		}
		if raw[i] == '"' {
			quoted = true
			continue
		}
		if i > 0 && !boundary(raw[i-1]) {
			continue
		}
		for _, pair := range [][2]string{{"NaN", "0e0"}, {"Infinity", "0.000000"}, {"-Infinity", "-0.000000"}} {
			end := i + len(pair[0])
			if end > len(raw) || !bytes.Equal(raw[i:end], []byte(pair[0])) || end < len(raw) && !boundary(raw[end]) {
				continue
			}
			if len(masked) > 0 && &masked[0] == &raw[0] {
				masked = append([]byte(nil), raw...)
			}
			copy(masked[i:end], pair[1])
			i = end - 1
			break
		}
	}
	return masked
}

func jsonValueStart(raw []byte, start int) int {
	for start < len(raw) {
		switch raw[start] {
		case ' ', '\t', '\r', '\n', ':', ',':
			start++
		default:
			return start
		}
	}
	return start
}

func writeJSON(b *bytes.Buffer, v any) error {
	switch x := v.(type) {
	case *jsonConstant:
		b.WriteString(x.token)
		return nil
	case json.Number:
		if defaultConstants[string(x)] != nil {
			b.WriteString(string(x))
			return nil
		}
	case jsonAtom:
		raw, err := x.MarshalJSON()
		if err != nil {
			return err
		}
		b.Write(raw)
		return nil
	case map[string]any:
		if x == nil {
			b.WriteString("null")
			return nil
		}
		keys := make([]string, 0, len(x))
		for key := range x {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		b.WriteByte('{')
		for i, key := range keys {
			if i > 0 {
				b.WriteByte(',')
			}
			if err := writeJSON(b, key); err != nil {
				return err
			}
			b.WriteByte(':')
			if err := writeJSON(b, x[key]); err != nil {
				return err
			}
		}
		b.WriteByte('}')
		return nil
	case []any:
		if x == nil {
			b.WriteString("null")
			return nil
		}
		b.WriteByte('[')
		for i, child := range x {
			if i > 0 {
				b.WriteByte(',')
			}
			if err := writeJSON(b, child); err != nil {
				return err
			}
		}
		b.WriteByte(']')
		return nil
	}
	var leaf bytes.Buffer
	e := json.NewEncoder(&leaf)
	e.SetEscapeHTML(false)
	if err := e.Encode(v); err != nil {
		return err
	}
	b.Write(bytes.TrimSuffix(leaf.Bytes(), []byte{'\n'}))
	return nil
}

func nonFinite(v any) string {
	switch x := value(v).(type) {
	case *jsonConstant:
		return x.token
	case json.Number:
		if _, ok := integer(x); ok {
			return ""
		}
		f, _ := strconv.ParseFloat(string(x), 64)
		if math.IsNaN(f) {
			return "NaN"
		}
		if math.IsInf(f, 1) {
			return "Infinity"
		}
		if math.IsInf(f, -1) {
			return "-Infinity"
		}
	}
	return ""
}

func containerEqual(a, b any) bool {
	if c, ok := value(a).(*jsonConstant); ok && c == value(b) {
		return true
	}
	return equal(a, b)
}
