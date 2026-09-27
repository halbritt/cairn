package apicontract

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"
)

// Validator checks JSON values against the schema subset this package emits:
// $ref, type, const, properties, required, additionalProperties, items,
// minItems/maxItems, minimum, anyOf, allOf and not/required.
type Validator struct {
	schemas map[string]any
	// Strict treats generated Go struct schemas (those with x-go-type) as
	// closed, so a response field the contract does not describe is reported.
	// Composed helper schemas (allOf parts) stay open.
	Strict bool
}

// NewValidator reads component schemas from a rendered document.
func NewValidator(document []byte) (*Validator, error) {
	var parsed struct {
		Components struct {
			Schemas map[string]any `json:"schemas"`
		} `json:"components"`
	}
	if err := json.Unmarshal(document, &parsed); err != nil {
		return nil, err
	}
	return &Validator{schemas: parsed.Components.Schemas}, nil
}

// Validate decodes body with numbers preserved and checks it against schema.
func (v *Validator) Validate(schema any, body []byte) error {
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return err
	}
	return v.check(schema, value, "$")
}

func jsonType(value any) string {
	switch value.(type) {
	case nil:
		return "null"
	case bool:
		return "boolean"
	case json.Number:
		return "number"
	case string:
		return "string"
	case []any:
		return "array"
	case map[string]any:
		return "object"
	}
	return "unknown"
}

func typeMatches(want string, value any) bool {
	got := jsonType(value)
	if want == "integer" {
		number, ok := value.(json.Number)
		if !ok {
			return false
		}
		f, err := number.Float64()
		return err == nil && f == math.Trunc(f) && !strings.ContainsAny(number.String(), ".eE")
	}
	return want == got
}

func (v *Validator) check(raw any, value any, path string) error {
	schema, ok := raw.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: invalid schema", path)
	}
	if ref, ok := schema["$ref"].(string); ok {
		target, found := v.schemas[strings.TrimPrefix(ref, "#/components/schemas/")]
		if !found {
			return fmt.Errorf("%s: unknown reference %s", path, ref)
		}
		return v.check(target, value, path)
	}
	if want, ok := schema["const"]; ok {
		wantJSON, _ := json.Marshal(want)
		gotJSON, _ := json.Marshal(value)
		if !bytes.Equal(wantJSON, gotJSON) {
			return fmt.Errorf("%s: %s is not %s", path, gotJSON, wantJSON)
		}
	}
	if kinds, ok := schema["type"]; ok {
		var names []string
		switch kinds := kinds.(type) {
		case string:
			names = []string{kinds}
		case []any:
			for _, kind := range kinds {
				names = append(names, kind.(string))
			}
		}
		matched := false
		for _, name := range names {
			matched = matched || typeMatches(name, value)
		}
		if !matched {
			return fmt.Errorf("%s: %s is not %v", path, jsonType(value), names)
		}
	}
	if minimum, ok := schema["minimum"].(float64); ok {
		if number, isNumber := value.(json.Number); isNumber {
			if f, _ := number.Float64(); f < minimum {
				return fmt.Errorf("%s: %s below minimum %v", path, number, minimum)
			}
		}
	}
	if options, ok := schema["anyOf"].([]any); ok {
		var reasons []string
		matched := false
		for _, option := range options {
			if err := v.check(option, value, path); err == nil {
				matched = true
				break
			} else {
				reasons = append(reasons, err.Error())
			}
		}
		if !matched {
			return fmt.Errorf("%s: no anyOf branch matched (%s)", path, strings.Join(reasons, "; "))
		}
	}
	if parts, ok := schema["allOf"].([]any); ok {
		for _, part := range parts {
			if err := v.check(part, value, path); err != nil {
				return err
			}
		}
	}
	if negated, ok := schema["not"].(map[string]any); ok {
		if v.check(negated, value, path) == nil {
			return fmt.Errorf("%s: matches a forbidden schema", path)
		}
	}
	if object, isObject := value.(map[string]any); isObject {
		if required, ok := schema["required"].([]any); ok {
			for _, name := range required {
				if _, present := object[name.(string)]; !present {
					return fmt.Errorf("%s: missing required %s", path, name)
				}
			}
		}
		properties, hasProperties := schema["properties"].(map[string]any)
		additional, hasAdditional := schema["additionalProperties"]
		names := make([]string, 0, len(object))
		for name := range object {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			child := path + "." + name
			if property, ok := properties[name]; ok {
				if err := v.check(property, object[name], child); err != nil {
					return err
				}
				continue
			}
			switch {
			case hasAdditional && additional == false:
				return fmt.Errorf("%s: unknown field", child)
			case hasAdditional && additional != true:
				if err := v.check(additional, object[name], child); err != nil {
					return err
				}
			case !hasAdditional && hasProperties && v.Strict && schema["x-go-type"] != nil:
				return fmt.Errorf("%s: field not described by the contract", child)
			}
		}
	}
	if array, isArray := value.([]any); isArray {
		if minimum, ok := schema["minItems"].(float64); ok && float64(len(array)) < minimum {
			return fmt.Errorf("%s: fewer than %v items", path, minimum)
		}
		if maximum, ok := schema["maxItems"].(float64); ok && float64(len(array)) > maximum {
			return fmt.Errorf("%s: more than %v items", path, maximum)
		}
		if items, ok := schema["items"]; ok {
			for i, item := range array {
				if err := v.check(items, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	}
	return nil
}
