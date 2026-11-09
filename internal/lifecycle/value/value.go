// Package value implements lossless reader mechanics. Its callers must establish
// the operation's admitted byte/depth domain and trusted runtime parameters.
// The reader does not select a production profile, depth policy, or writer.
package value

import (
	"encoding/binary"
	"errors"
	"math"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

type Kind uint8

const (
	Null Kind = iota
	Bool
	Int
	Float
	String
	Array
	Object
)

// Value keeps scalar types and object insertion order. Strings and keys are
// code points, including isolated surrogates; converting those slices to Go
// strings can lose information. encoding/json is not a legacy Value writer.
type Value struct {
	Kind    Kind
	Boolean bool
	Integer string
	Number  float64
	String  []rune
	Items   []*Value
	Members []Member
}

type Member struct {
	Key   []rune
	Value *Value
}

// Get uses exact code-point keys; admission and binding field names are ASCII.
func (v *Value) Get(key string) *Value {
	if v == nil || v.Kind != Object {
		return nil
	}
	want := []rune(key)
	for _, member := range v.Members {
		if len(want) != len(member.Key) {
			continue
		}
		equal := true
		for i := range want {
			if want[i] != member.Key[i] {
				equal = false
				break
			}
		}
		if equal {
			return member.Value
		}
	}
	return nil
}

// Error preserves the reader's source error family. Rendering and whether a
// family escapes belong to the actual caller, not this reader kernel.
type Error struct {
	Class   string
	Message string
}

func (e *Error) Error() string { return e.Class + ": " + e.Message }

func ErrorClass(err error) string {
	if err == nil {
		return ""
	}
	var source *Error
	if errors.As(err, &source) {
		return source.Class
	}
	return "GoError"
}

func failure(class, message string) error { return &Error{Class: class, Message: message} }

// DecodeBytes follows json.loads(bytes)' encoding detection and surrogatepass.
// integerDigits is required: 0 explicitly means unlimited, as in Python.
// There is no calibrated depth cap: the iterative kernel is independent of the
// caller's still-separate production depth/domain policy.
func DecodeBytes(raw []byte, integerDigits int) (*Value, error) {
	return DecodeBytesWithObjectPolicy(raw, integerDigits, DuplicateLast)
}

// ObjectPolicy is operation semantics, not a reader profile or qualification.
// The zero/unknown policy is unsupported; legacy APIs explicitly choose last.
type ObjectPolicy string

const (
	DuplicateLast   ObjectPolicy = "duplicate-last"
	DuplicateReject ObjectPolicy = "duplicate-reject"
)

func validObjectPolicy(policy ObjectPolicy) bool {
	return policy == DuplicateLast || policy == DuplicateReject
}

// DecodeBytesWithObjectPolicy uses the existing bytes-json mechanics with an
// explicit object_pairs_hook policy. Rejection occurs at each object close,
// after its complete pair sequence is parsed, before any collapsed object is
// returned. Domain/stack/codec qualification still belongs to SourceRoutes.
func DecodeBytesWithObjectPolicy(raw []byte, integerDigits int, policy ObjectPolicy) (*Value, error) {
	if !validObjectPolicy(policy) {
		return nil, &ProfileUnavailable{}
	}
	text, err := decodeBytes(raw)
	if err != nil {
		return nil, err
	}
	return parse(text, integerDigits, policy)
}

// DecodeText models explicit UTF-8 or ASCII strict text decoding followed by
// json.loads(str). It has no implicit locale or encoding default. Support here
// is reader capability, not selection of the supported production locale set.
func DecodeText(raw []byte, encoding string, integerDigits int) (*Value, error) {
	return DecodeTextWithObjectPolicy(raw, encoding, integerDigits, DuplicateLast)
}

// DecodeTextWithObjectPolicy retains the explicit text codec route and the
// same object completion/error priority as DecodeBytesWithObjectPolicy.
func DecodeTextWithObjectPolicy(raw []byte, encoding string, integerDigits int, policy ObjectPolicy) (*Value, error) {
	if !validObjectPolicy(policy) {
		return nil, &ProfileUnavailable{}
	}
	var text []rune
	var err error
	switch strings.ToLower(strings.ReplaceAll(encoding, "_", "-")) {
	case "utf-8", "utf8":
		text, err = decodeUTF8(raw, false)
	case "ascii", "us-ascii", "ansi-x3.4-1968":
		for _, b := range raw {
			if b > 127 {
				return nil, failure("UnicodeDecodeError", "invalid ASCII text")
			}
			text = append(text, rune(b))
		}
	default:
		return nil, failure("ValueError", "explicit supported text decoder required")
	}
	if err != nil {
		return nil, err
	}
	return parse(text, integerDigits, policy)
}

func decodeBytes(raw []byte) ([]rune, error) {
	width, order, skip := 1, binary.ByteOrder(binary.BigEndian), 0
	switch {
	case strings.HasPrefix(string(raw), "\x00\x00\xfe\xff"):
		width, skip = 4, 4
	case strings.HasPrefix(string(raw), "\xff\xfe\x00\x00"):
		width, order, skip = 4, binary.LittleEndian, 4
	case strings.HasPrefix(string(raw), "\xfe\xff"):
		width, skip = 2, 2
	case strings.HasPrefix(string(raw), "\xff\xfe"):
		width, order, skip = 2, binary.LittleEndian, 2
	case strings.HasPrefix(string(raw), "\xef\xbb\xbf"):
		skip = 3
	case len(raw) >= 4:
		if raw[0] == 0 {
			width = 2
			if raw[1] == 0 {
				width = 4
			}
		} else if raw[1] == 0 {
			width, order = 2, binary.LittleEndian
			if raw[2] == 0 && raw[3] == 0 {
				width = 4
			}
		}
	case len(raw) == 2:
		if raw[0] == 0 {
			width = 2
		} else if raw[1] == 0 {
			width, order = 2, binary.LittleEndian
		}
	}
	raw = raw[skip:]
	if width == 1 {
		return decodeUTF8(raw, true)
	}
	if len(raw)%width != 0 {
		return nil, failure("UnicodeDecodeError", "truncated Unicode code unit")
	}
	text := make([]rune, 0, len(raw)/width)
	for i := 0; i < len(raw); i += width {
		if width == 4 {
			unit := order.Uint32(raw[i:])
			if unit > utf8.MaxRune {
				return nil, failure("UnicodeDecodeError", "invalid Unicode code point")
			}
			text = append(text, rune(unit))
			continue
		}
		unit := rune(order.Uint16(raw[i:]))
		if unit >= 0xd800 && unit <= 0xdbff && i+width < len(raw) {
			next := rune(order.Uint16(raw[i+width:]))
			if next >= 0xdc00 && next <= 0xdfff {
				text = append(text, utf16.DecodeRune(unit, next))
				i += width
				continue
			}
		}
		text = append(text, unit)
	}
	return text, nil
}

func decodeUTF8(raw []byte, surrogatepass bool) ([]rune, error) {
	text := make([]rune, 0, len(raw))
	for len(raw) != 0 {
		if surrogatepass && len(raw) >= 3 && raw[0] == 0xed && raw[1] >= 0xa0 && raw[1] <= 0xbf && raw[2]&0xc0 == 0x80 {
			text = append(text, rune(raw[0]&15)<<12|rune(raw[1]&63)<<6|rune(raw[2]&63))
			raw = raw[3:]
			continue
		}
		r, size := utf8.DecodeRune(raw)
		if r == utf8.RuneError && size == 1 {
			return nil, failure("UnicodeDecodeError", "invalid UTF-8 input")
		}
		text = append(text, r)
		raw = raw[size:]
	}
	return text, nil
}

type parser struct {
	text   []rune
	i      int
	digits int
}

type frame struct {
	value     *Value
	state     byte
	key       []rune
	positions map[string]int
	duplicate bool
}

func parse(text []rune, integerDigits int, policy ObjectPolicy) (*Value, error) {
	if integerDigits != 0 && integerDigits < 640 {
		return nil, failure("ValueError", "integer digit setting must be zero or at least 640")
	}
	p := parser{text: text, digits: integerDigits}
	var root *Value
	var stack []frame
	for {
		p.whitespace()
		if root != nil && len(stack) == 0 {
			if p.i != len(p.text) {
				return nil, failure("JSONDecodeError", "extra data")
			}
			return root, nil
		}
		if len(stack) != 0 {
			top := &stack[len(stack)-1]
			var next rune
			if p.i < len(p.text) {
				next = p.text[p.i]
			}
			if (top.value.Kind == Array && (top.state == 0 || top.state == 1) && next == ']') ||
				(top.value.Kind == Object && (top.state == 0 || top.state == 3) && next == '}') {
				p.i++
				if top.value.Kind == Object && top.duplicate && policy == DuplicateReject {
					return nil, failure("ValueError", "duplicate key")
				}
				stack[len(stack)-1] = frame{}
				stack = stack[:len(stack)-1]
				continue
			}
			if top.value.Kind == Array && top.state == 1 || top.value.Kind == Object && top.state == 3 {
				if next != ',' {
					return nil, failure("JSONDecodeError", "comma or closing delimiter required")
				}
				p.i++
				if top.value.Kind == Array {
					top.state = 2
				} else {
					top.state = 4
				}
				continue
			}
			if top.value.Kind == Object {
				switch top.state {
				case 0, 4:
					if next != '"' {
						return nil, failure("JSONDecodeError", "object key required")
					}
					key, err := p.string()
					if err != nil {
						return nil, err
					}
					top.key, top.state = key, 1
					continue
				case 1:
					if next != ':' {
						return nil, failure("JSONDecodeError", "colon required")
					}
					p.i++
					top.state = 2
					continue
				}
			}
		}
		v, err := p.atom()
		if err != nil {
			return nil, err
		}
		if len(stack) == 0 {
			root = v
		} else {
			top := &stack[len(stack)-1]
			if top.value.Kind == Array {
				top.value.Items = append(top.value.Items, v)
				top.state = 1
			} else {
				// Four-byte code-point keys are an internal comparison key, not
				// a legacy serialization or a Unicode normalization.
				raw := make([]byte, 4*len(top.key))
				for i, r := range top.key {
					binary.LittleEndian.PutUint32(raw[4*i:], uint32(r))
				}
				if top.positions == nil {
					top.positions = make(map[string]int)
				}
				if index, ok := top.positions[string(raw)]; ok {
					if policy == DuplicateReject {
						top.duplicate = true
						top.value.Members = append(top.value.Members, Member{Key: top.key, Value: v})
					} else {
						top.value.Members[index].Value = v
					}
				} else {
					top.positions[string(raw)] = len(top.value.Members)
					top.value.Members = append(top.value.Members, Member{Key: top.key, Value: v})
				}
				top.key, top.state = nil, 3
			}
		}
		if v.Kind == Object || v.Kind == Array {
			stack = append(stack, frame{value: v})
		}
	}
}

func (p *parser) whitespace() {
	for p.i < len(p.text) && (p.text[p.i] == ' ' || p.text[p.i] == '\t' || p.text[p.i] == '\r' || p.text[p.i] == '\n') {
		p.i++
	}
}

func (p *parser) atom() (*Value, error) {
	if p.i == len(p.text) {
		return nil, failure("JSONDecodeError", "value required")
	}
	switch p.text[p.i] {
	case '{':
		p.i++
		return &Value{Kind: Object}, nil
	case '[':
		p.i++
		return &Value{Kind: Array}, nil
	case '"':
		text, err := p.string()
		return &Value{Kind: String, String: text}, err
	}
	for _, constant := range []struct {
		text  string
		value Value
	}{
		{"null", Value{Kind: Null}}, {"true", Value{Kind: Bool, Boolean: true}},
		{"false", Value{Kind: Bool}}, {"NaN", Value{Kind: Float, Number: math.NaN()}},
		{"Infinity", Value{Kind: Float, Number: math.Inf(1)}}, {"-Infinity", Value{Kind: Float, Number: math.Inf(-1)}},
	} {
		end := p.i + len(constant.text)
		if end <= len(p.text) && string(p.text[p.i:end]) == constant.text {
			p.i = end
			return &constant.value, nil
		}
	}
	return p.number()
}

func digit(r rune) bool { return r >= '0' && r <= '9' }

func (p *parser) number() (*Value, error) {
	start := p.i
	if p.i < len(p.text) && p.text[p.i] == '-' {
		p.i++
	}
	if p.i == len(p.text) || !digit(p.text[p.i]) {
		return nil, failure("JSONDecodeError", "value required")
	}
	if p.text[p.i] == '0' {
		p.i++
	} else {
		for p.i < len(p.text) && digit(p.text[p.i]) {
			p.i++
		}
	}
	isFloat := false
	// Python converts the longest complete JSON-number prefix. Leave an
	// incomplete suffix for the enclosing syntax check, after conversion:
	// an oversized integer can raise ValueError before that trailing error.
	if p.i+1 < len(p.text) && p.text[p.i] == '.' && digit(p.text[p.i+1]) {
		isFloat = true
		p.i++
		for p.i < len(p.text) && digit(p.text[p.i]) {
			p.i++
		}
	}
	if p.i < len(p.text) && (p.text[p.i] == 'e' || p.text[p.i] == 'E') {
		next := p.i + 1
		if next < len(p.text) && (p.text[next] == '+' || p.text[next] == '-') {
			next++
		}
		if next < len(p.text) && digit(p.text[next]) {
			isFloat = true
			p.i = next + 1
			for p.i < len(p.text) && digit(p.text[p.i]) {
				p.i++
			}
		}
	}
	token := string(p.text[start:p.i])
	if isFloat {
		number, err := strconv.ParseFloat(token, 64)
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return nil, failure("JSONDecodeError", "invalid number")
		}
		return &Value{Kind: Float, Number: number}, nil
	}
	count := len(token)
	if token[0] == '-' {
		count--
	}
	if p.digits != 0 && count > p.digits {
		return nil, failure("ValueError", "integer exceeds source digit setting")
	}
	if token == "-0" {
		token = "0"
	}
	return &Value{Kind: Int, Integer: token}, nil
}

func (p *parser) string() ([]rune, error) {
	p.i++ // opening quote
	var text []rune
	for p.i < len(p.text) {
		r := p.text[p.i]
		p.i++
		if r == '"' {
			return text, nil
		}
		if r < 32 {
			return nil, failure("JSONDecodeError", "invalid string control character")
		}
		if r != '\\' {
			text = append(text, r)
			continue
		}
		if p.i == len(p.text) {
			break
		}
		r = p.text[p.i]
		p.i++
		switch r {
		case '"', '\\', '/':
			text = append(text, r)
		case 'b':
			text = append(text, '\b')
		case 'f':
			text = append(text, '\f')
		case 'n':
			text = append(text, '\n')
		case 'r':
			text = append(text, '\r')
		case 't':
			text = append(text, '\t')
		case 'u':
			unit, ok := p.hex(p.i)
			if !ok {
				return nil, failure("JSONDecodeError", "invalid Unicode escape")
			}
			p.i += 4
			if unit >= 0xd800 && unit <= 0xdbff && p.i+6 <= len(p.text) && p.text[p.i] == '\\' && p.text[p.i+1] == 'u' {
				low, ok := p.hex(p.i + 2)
				if ok && low >= 0xdc00 && low <= 0xdfff {
					unit = utf16.DecodeRune(unit, low)
					p.i += 6
				}
			}
			text = append(text, unit)
		default:
			return nil, failure("JSONDecodeError", "invalid string escape")
		}
	}
	return nil, failure("JSONDecodeError", "unterminated string")
}

func (p *parser) hex(offset int) (rune, bool) {
	if offset+4 > len(p.text) {
		return 0, false
	}
	var result rune
	for _, r := range p.text[offset : offset+4] {
		result <<= 4
		switch {
		case r >= '0' && r <= '9':
			result += r - '0'
		case r >= 'a' && r <= 'f':
			result += r - 'a' + 10
		case r >= 'A' && r <= 'F':
			result += r - 'A' + 10
		default:
			return 0, false
		}
	}
	return result, true
}
