package value

import (
	"fmt"
	"io"
	"math"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

// WriteKernel supplies ordered scalar/encoding mechanics, not a selected
// Python dumps/dump buffering, recursion or partial-error policy. SourceRoutes
// must enforce that per-site route before invoking or adapting these chunks.
func WriteKernel(out io.Writer, v *Value, route WriterRoute, integerDigits int) error {
	if route.EnsureASCII == nil || route.Compact == nil || route.TrailingLF == nil || route.Codec == nil {
		return &ProfileUnavailable{}
	}
	if (route.Codec.Name != "utf-8" && route.Codec.Name != "ascii") ||
		(route.Codec.Errors != "strict" && route.Codec.Errors != "surrogatepass" && route.Codec.Errors != "surrogateescape") {
		return &ProfileUnavailable{}
	}
	write := func(raw string) error {
		n, err := io.WriteString(out, raw)
		if err == nil && n != len(raw) {
			err = io.ErrShortWrite
		}
		return err
	}
	quote := func(points []rune) (string, error) {
		var b strings.Builder
		b.WriteByte('"')
		for _, c := range points {
			switch c {
			case '"', '\\':
				b.WriteByte('\\')
				b.WriteRune(c)
			case '\b':
				b.WriteString(`\b`)
			case '\f':
				b.WriteString(`\f`)
			case '\n':
				b.WriteString(`\n`)
			case '\r':
				b.WriteString(`\r`)
			case '\t':
				b.WriteString(`\t`)
			default:
				if c < 32 || (*route.EnsureASCII && c > 127) {
					if c <= 0xffff {
						fmt.Fprintf(&b, `\u%04x`, c)
					} else {
						hi, lo := utf16.EncodeRune(c)
						fmt.Fprintf(&b, `\u%04x\u%04x`, hi, lo)
					}
					continue
				}
				if route.Codec.Name == "ascii" && c > 127 {
					return "", failure("UnicodeEncodeError", "writer codec")
				}
				if c >= 0xd800 && c <= 0xdfff {
					if route.Codec.Errors == "surrogatepass" && route.Codec.Name == "utf-8" {
						b.WriteByte(0xe0 | byte(c>>12))
						b.WriteByte(0x80 | byte(c>>6&63))
						b.WriteByte(0x80 | byte(c&63))
					} else if route.Codec.Errors == "surrogateescape" && c >= 0xdc80 && c <= 0xdcff {
						b.WriteByte(byte(c - 0xdc00))
					} else {
						return "", failure("UnicodeEncodeError", "writer surrogate")
					}
					continue
				}
				if !utf8.ValidRune(c) {
					return "", failure("UnicodeEncodeError", "writer code point")
				}
				b.WriteRune(c)
			}
		}
		b.WriteByte('"')
		return b.String(), nil
	}
	type token struct {
		value *Value
		raw   string
		key   []rune
	}
	stack := []token{{value: v}}
	comma, colon := ",", ":"
	if !*route.Compact {
		comma = ", "
		colon = ": "
	}
	for len(stack) > 0 {
		t := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if t.raw != "" {
			if err := write(t.raw); err != nil {
				return err
			}
			continue
		}
		if t.key != nil {
			s, err := quote(t.key)
			if err != nil {
				return err
			}
			if err := write(s); err != nil {
				return err
			}
			continue
		}
		x := t.value
		if x == nil || x.Kind == Null {
			if err := write("null"); err != nil {
				return err
			}
			continue
		}
		var text string
		switch x.Kind {
		case Bool:
			text = strconv.FormatBool(x.Boolean)
		case Int:
			if integerDigits != 0 && len(strings.TrimPrefix(x.Integer, "-")) > integerDigits {
				return failure("ValueError", "writer integer digit limit")
			}
			text = x.Integer
		case Float:
			switch {
			case math.IsNaN(x.Number):
				text = "NaN"
			case math.IsInf(x.Number, 1):
				text = "Infinity"
			case math.IsInf(x.Number, -1):
				text = "-Infinity"
			default:
				style := byte('f')
				n := math.Abs(x.Number)
				if n != 0 && (n < 1e-4 || n >= 1e16) {
					style = 'e'
				}
				text = strconv.FormatFloat(x.Number, style, -1, 64)
				if !strings.ContainsAny(text, ".eE") {
					text += ".0"
				}
			}
		case String:
			s, err := quote(x.String)
			if err != nil {
				return err
			}
			text = s
		case Array:
			if err := write("["); err != nil {
				return err
			}
			stack = append(stack, token{raw: "]"})
			for i := len(x.Items) - 1; i >= 0; i-- {
				stack = append(stack, token{value: x.Items[i]})
				if i > 0 {
					stack = append(stack, token{raw: comma})
				}
			}
			continue
		case Object:
			if err := write("{"); err != nil {
				return err
			}
			stack = append(stack, token{raw: "}"})
			for i := len(x.Members) - 1; i >= 0; i-- {
				m := x.Members[i]
				key := m.Key
				if key == nil {
					key = []rune{}
				}
				stack = append(stack, token{value: m.Value}, token{raw: colon}, token{key: key})
				if i > 0 {
					stack = append(stack, token{raw: comma})
				}
			}
			continue
		default:
			return failure("TypeError", "writer value kind")
		}
		if err := write(text); err != nil {
			return err
		}
	}
	if *route.TrailingLF {
		return write("\n")
	}
	return nil
}
