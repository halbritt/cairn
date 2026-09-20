// Package jsontext checks request text for Unicode transformations that
// encoding/json otherwise permits silently. Syntax validation stays with JSON.
package jsontext

import (
	"errors"
	"reflect"
	"strconv"
	"strings"
	"unicode/utf8"
)

var errUnicode = errors.New("JSON requires valid UTF-8 and paired Unicode surrogate escapes")

func CheckValue(value any) error {
	return checkValue(reflect.ValueOf(value), 0, make(map[any]bool))
}

func checkValue(value reflect.Value, depth int, seen map[any]bool) error {
	if depth > 1000 {
		return errors.New("JSON request nesting exceeds 1000 levels")
	}
	switch value.Kind() {
	case reflect.String:
		if !utf8.ValidString(value.String()) {
			return errUnicode
		}
	case reflect.Interface:
		if !value.IsNil() {
			return checkValue(value.Elem(), depth+1, seen)
		}
	case reflect.Pointer, reflect.Map, reflect.Slice:
		if value.IsNil() {
			return nil
		}
		key := struct {
			typeOf  reflect.Type
			pointer uintptr
			length  int
		}{typeOf: value.Type(), pointer: value.Pointer()}
		if value.Kind() == reflect.Slice {
			key.length = value.Len()
		}
		if seen[key] {
			return nil
		}
		seen[key] = true
		switch value.Kind() {
		case reflect.Pointer:
			return checkValue(value.Elem(), depth+1, seen)
		case reflect.Map:
			iter := value.MapRange()
			for iter.Next() {
				if err := checkValue(iter.Key(), depth+1, seen); err != nil {
					return err
				}
				if err := checkValue(iter.Value(), depth+1, seen); err != nil {
					return err
				}
			}
		case reflect.Slice:
			for i := 0; i < value.Len(); i++ {
				if err := checkValue(value.Index(i), depth+1, seen); err != nil {
					return err
				}
			}
		}
	case reflect.Array:
		for i := 0; i < value.Len(); i++ {
			if err := checkValue(value.Index(i), depth+1, seen); err != nil {
				return err
			}
		}
	case reflect.Struct:
		for i := 0; i < value.NumField(); i++ {
			field := value.Type().Field(i)
			if field.PkgPath != "" || strings.Split(field.Tag.Get("json"), ",")[0] == "-" {
				continue
			}
			if err := checkValue(value.Field(i), depth+1, seen); err != nil {
				return err
			}
		}
	}
	return nil
}

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
