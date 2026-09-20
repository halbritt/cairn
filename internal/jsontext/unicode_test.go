package jsontext

import (
	"encoding/json"
	"testing"
	"unicode/utf8"
)

func TestValueUnicodeBeforeEncoding(t *testing.T) {
	type label string
	type request struct {
		Text    string `json:"text"`
		Hidden  string `json:"-"`
		private string
	}
	bad := "label-\xff"
	for name, value := range map[string]any{
		"string":       bad,
		"named-string": label(bad),
		"pointer":      &bad,
		"struct":       &request{Text: bad},
		"map-key":      map[string]string{bad: "valid"},
		"nested-value": map[string]any{"items": []any{[1]string{bad}}},
	} {
		t.Run(name, func(t *testing.T) {
			if err := CheckValue(value); err == nil {
				t.Fatal("invalid UTF-8 accepted before encoding")
			}
		})
	}
	cycle := map[string]any{}
	cycle["self"] = cycle
	for _, value := range []any{nil, (*string)(nil), []byte{0xff}, request{Text: "日本語 � \\ud800", Hidden: bad, private: bad}, cycle} {
		if err := CheckValue(value); err != nil {
			t.Fatalf("non-lossy value refused: %T %v", value, err)
		}
	}
	cycle["text"] = bad
	if err := CheckValue(cycle); err == nil {
		t.Fatal("cycle hid invalid text")
	}
}

func TestUnicodeEscapesAndLiteralBackslashes(t *testing.T) {
	for _, input := range []string{`{"text":"\ud83d\ude00"}`, `{"\ud83d\ude00":"x"}`, `{"text":"\\ud800"}`, `{"text":"\"\\ud800"}`, `{"text":"\u0000\u00e9\ufffd"}`, `{"text":"é 😀 �"}`} {
		if !json.Valid([]byte(input)) {
			t.Fatalf("invalid fixture: %s", input)
		}
		if err := CheckUnicode([]byte(input)); err != nil {
			t.Errorf("valid Unicode refused: %s: %v", input, err)
		}
	}
	for _, input := range []string{`{"text":"\ud800"}`, `{"text":"\udc00"}`, `{"text":"\ud800\ud800"}`, `{"text":"\ud800\\udc00"}`, `{"\udfff":"x"}`, string([]byte{'"', 0xed, 0xa0, 0x80, '"'})} {
		if err := CheckUnicode([]byte(input)); err == nil {
			t.Errorf("lossy Unicode accepted: %q", input)
		}
	}
}

func FuzzUnicodePreservesValidJSONString(f *testing.F) {
	for _, seed := range []string{"plain", "é 😀 �", "\\ud800", "\x00\r\n\"\\"} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, text string) {
		if !utf8.ValidString(text) {
			t.Skip()
		}
		encoded, err := json.Marshal(map[string]string{text: text})
		if err != nil {
			t.Fatal(err)
		}
		if err = CheckUnicode(encoded); err != nil {
			t.Fatalf("serialized valid Unicode refused: %v", err)
		}
	})
}
