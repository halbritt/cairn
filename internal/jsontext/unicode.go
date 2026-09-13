// Package jsontext checks request text for Unicode transformations that
// encoding/json otherwise permits silently. Syntax validation stays with JSON.
package jsontext

import (
	"errors"
	"strconv"
	"unicode/utf8"
)

var errUnicode = errors.New("JSON requires valid UTF-8 and paired Unicode surrogate escapes")

// CheckUnicode rejects text that encoding/json would replace with U+FFFD.
// Callers must still validate JSON syntax and their own request schema.
func CheckUnicode(body []byte) error {
	if !utf8.Valid(body) {
		return errUnicode
	}
	quoted := false
	for i := 0; i < len(body); i++ {
		if body[i] == '"' {
			quoted = !quoted
			continue
		}
		if !quoted || body[i] != '\\' {
			continue
		}
		i++
		if i >= len(body) || body[i] != 'u' {
			continue
		}
		if i+4 >= len(body) {
			return errUnicode
		}
		unit, err := strconv.ParseUint(string(body[i+1:i+5]), 16, 16)
		if err != nil {
			return errUnicode
		}
		i += 4
		switch {
		case unit >= 0xdc00 && unit <= 0xdfff:
			return errUnicode
		case unit >= 0xd800 && unit <= 0xdbff:
			if i+6 >= len(body) || body[i+1] != '\\' || body[i+2] != 'u' {
				return errUnicode
			}
			low, err := strconv.ParseUint(string(body[i+3:i+7]), 16, 16)
			if err != nil || low < 0xdc00 || low > 0xdfff {
				return errUnicode
			}
			i += 6
		}
	}
	return nil
}
