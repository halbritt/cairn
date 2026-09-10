package jsontext

import (
	"encoding/json"
	"testing"
	"unicode/utf8"
)

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
