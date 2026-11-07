package value

import (
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"strconv"
	"strings"
	"testing"
	"unicode/utf16"
)

func TestReaderPreservesScalarTypesOrderAndDuplicatePosition(t *testing.T) {
	v, err := DecodeBytes([]byte(`{"x":{"old":1},"X":true,"big":900719925474099312345,"x":{"new":2},"n":[1,1.0,true,-0.0,NaN,Infinity,-Infinity],"s":"é<>&\u2028\ud800"}`), 4300)
	if err != nil {
		t.Fatal(err)
	}
	if v.Kind != Object || len(v.Members) != 5 || string(v.Members[0].Key) != "x" || string(v.Members[1].Key) != "X" || v.Get("x").Get("old") != nil || v.Get("x").Get("new").Integer != "2" {
		t.Fatalf("object order/case/duplicate replacement changed: %+v", v)
	}
	if v.Get("big").Integer != "900719925474099312345" {
		t.Fatal("integer rounded")
	}
	n := v.Get("n").Items
	if n[0].Kind != Int || n[1].Kind != Float || n[2].Kind != Bool || !math.Signbit(n[3].Number) || !math.IsNaN(n[4].Number) || !math.IsInf(n[5].Number, 1) || !math.IsInf(n[6].Number, -1) {
		t.Fatal("scalar class or value changed")
	}
	s := v.Get("s").String
	if s[len(s)-1] != 0xd800 || s[len(s)-2] != 0x2028 {
		t.Fatalf("Unicode was replaced: %x", s)
	}
}

// This is a test-only wire shape for reader comparisons, not an F01 command,
// production profile selector, compatibility frame, or legacy writer API.
func TestReaderOracleHelper(t *testing.T) {
	if os.Getenv("CAIRN_F01_VALUE_HELPER") != "1" {
		return
	}
	digits, present := os.LookupEnv("CAIRN_F01_VALUE_DIGITS")
	if !present {
		t.Fatal("explicit reader setting missing")
	}
	limit, err := strconv.Atoi(digits)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		t.Fatal(err)
	}
	var v *Value
	switch os.Getenv("CAIRN_F01_VALUE_MODE") {
	case "bytes":
		v, err = DecodeBytes(raw, limit)
	case "text":
		v, err = DecodeText(raw, os.Getenv("CAIRN_F01_VALUE_ENCODING"), limit)
	default:
		t.Fatal("explicit reader mode missing")
	}
	result := map[string]any{"error_class": ErrorClass(err)}
	if err == nil {
		result["value"] = oracleValue(v)
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		t.Fatal(err)
	}
}

func oracleValue(v *Value) any {
	switch v.Kind {
	case Null:
		return []any{"null"}
	case Bool:
		return []any{"bool", v.Boolean}
	case Int:
		return []any{"int", v.Integer}
	case Float:
		class := ""
		switch {
		case math.IsNaN(v.Number):
			class = "nan"
		case math.IsInf(v.Number, 1):
			class = "+inf"
		case math.IsInf(v.Number, -1):
			class = "-inf"
		default:
			raw := make([]byte, 8)
			binary.BigEndian.PutUint64(raw, math.Float64bits(v.Number))
			class = hex.EncodeToString(raw)
		}
		return []any{"float", class}
	case String:
		// []rune has integer elements; isolated surrogates stay code points.
		return []any{"string", append([]rune{}, v.String...)}
	case Array:
		items := make([]any, 0, len(v.Items))
		for _, item := range v.Items {
			items = append(items, oracleValue(item))
		}
		return []any{"array", items}
	case Object:
		items := make([]any, 0, len(v.Members))
		for _, member := range v.Members {
			items = append(items, []any{append([]rune{}, member.Key...), oracleValue(member.Value)})
		}
		return []any{"object", items}
	default:
		panic(fmt.Sprintf("unknown reader kind %d", v.Kind))
	}
}

func TestExplicitIntegerDigitsAndMalformedValues(t *testing.T) {
	for _, tc := range []struct {
		raw   string
		class string
	}{
		{strings.Repeat("1", 4300), ""}, {strings.Repeat("1", 4301), "ValueError"},
		{`{"x":01}`, "JSONDecodeError"}, {`[1,]`, "JSONDecodeError"},
		{`{"x":}`, "JSONDecodeError"}, {`{,"x":1}`, "JSONDecodeError"},
		{`{"x" 1}`, "JSONDecodeError"}, {`[}`, "JSONDecodeError"},
		{`"\uxxxx"`, "JSONDecodeError"}, {`[1e+]`, "JSONDecodeError"},
		{"", "JSONDecodeError"}, {"true false", "JSONDecodeError"},
	} {
		_, err := DecodeBytes([]byte(tc.raw), 4300)
		if ErrorClass(err) != tc.class {
			t.Fatalf("%q: %v", tc.raw[:min(30, len(tc.raw))], err)
		}
	}
	if _, err := DecodeBytes([]byte(strings.Repeat("1", 4301)), 0); err != nil {
		t.Fatalf("explicit unlimited integer setting refused: %v", err)
	}
	if v, err := DecodeBytes([]byte("-0"), 4300); err != nil || v.Integer != "0" {
		t.Fatalf("integer negative zero: %+v, %v", v, err)
	}
	if v, err := DecodeBytes([]byte("1e400"), 4300); err != nil || !math.IsInf(v.Number, 1) {
		t.Fatalf("source overflow class: %+v, %v", v, err)
	}
}

func TestByteDetectionAndExplicitUTF8TextAreSeparate(t *testing.T) {
	source := `{"s":"é😀"}`
	inputs := [][]byte{[]byte(source), append([]byte{0xef, 0xbb, 0xbf}, []byte(source)...)}
	for _, order := range []binary.AppendByteOrder{binary.LittleEndian, binary.BigEndian} {
		for _, width := range []int{2, 4} {
			var raw []byte
			if width == 2 {
				for _, unit := range utf16.Encode([]rune(source)) {
					raw = order.AppendUint16(raw, unit)
				}
			} else {
				for _, unit := range source {
					raw = order.AppendUint32(raw, uint32(unit))
				}
			}
			inputs = append(inputs, raw)
		}
	}
	for _, raw := range inputs {
		v, err := DecodeBytes(raw, 4300)
		if err != nil || string(v.Get("s").String) != "é😀" {
			t.Fatalf("byte decoder %x: %+v, %v", raw[:min(12, len(raw))], v, err)
		}
	}
	if _, err := DecodeText(inputs[1], "utf-8", 4300); ErrorClass(err) != "JSONDecodeError" {
		t.Fatalf("text reader stripped BOM: %v", err)
	}
	wtf := []byte{'"', 0xed, 0xa0, 0x80, '"'}
	v, err := DecodeBytes(wtf, 4300)
	if err != nil || len(v.String) != 1 || v.String[0] != 0xd800 {
		t.Fatalf("bytes surrogatepass: %+v, %v", v, err)
	}
	if _, err := DecodeText(wtf, "utf-8", 4300); ErrorClass(err) != "UnicodeDecodeError" {
		t.Fatalf("text reader accepted encoded surrogate: %v", err)
	}
	if _, err := DecodeBytes([]byte{'"', 0xff, '"'}, 4300); ErrorClass(err) != "UnicodeDecodeError" {
		t.Fatalf("invalid UTF-8 silently normalized: %v", err)
	}
	if _, err := DecodeText([]byte(`"é"`), "ascii", 4300); ErrorClass(err) != "UnicodeDecodeError" {
		t.Fatalf("explicit ASCII text reader accepted non-ASCII: %v", err)
	}
}

func TestReaderMechanicsUseHeapStack(t *testing.T) {
	// This proves decoder mechanics only. The caller's production depth/domain
	// policy and trusted profile handoff remain separate root decisions.
	const depth = 100000
	v, err := DecodeBytes([]byte(strings.Repeat("[", depth)+"0"+strings.Repeat("]", depth)), 4300)
	if err != nil {
		t.Fatal(err)
	}
	for range depth {
		if v.Kind != Array || len(v.Items) != 1 {
			t.Fatal("deep array structure lost")
		}
		v = v.Items[0]
	}
	if v.Kind != Int || v.Integer != "0" {
		t.Fatal("deep leaf changed")
	}
}
