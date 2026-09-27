package apicontract

import (
	"bytes"
	"encoding/json"
	"fmt"
	"sync"

	"github.com/google/jsonschema-go/jsonschema"
)

// schemaValidator checks JSON against the generated contract with an
// independent draft 2020-12 implementation. Component schemas become $defs.
type schemaValidator struct {
	defs   map[string]any
	strict bool
	mu     sync.Mutex
	cache  map[string]*jsonschema.Resolved
}

func newSchemaValidator(document []byte, strict bool) (*schemaValidator, error) {
	var parsed struct {
		Components struct {
			Schemas map[string]any `json:"schemas"`
		} `json:"components"`
	}
	if err := json.Unmarshal(document, &parsed); err != nil {
		return nil, err
	}
	defs := parsed.Components.Schemas
	if strict {
		// Close generated Go struct schemas so a reply field the contract does
		// not describe fails. The published contract keeps responses open.
		closed := map[string]any{}
		for name, raw := range defs {
			schema := raw.(map[string]any)
			if _, open := schema["additionalProperties"]; !open && schema["x-go-type"] != nil {
				copy := map[string]any{"additionalProperties": false}
				for key, value := range schema {
					copy[key] = value
				}
				schema = copy
			}
			closed[name] = schema
		}
		defs = closed
	}
	return &schemaValidator{defs: defs, strict: strict, cache: map[string]*jsonschema.Resolved{}}, nil
}

func (v *schemaValidator) Validate(schema any, body []byte) error {
	target, err := json.Marshal(schema)
	if err != nil {
		return err
	}
	key := string(target)
	v.mu.Lock()
	resolved, ok := v.cache[key]
	v.mu.Unlock()
	if !ok {
		root := map[string]any{"$schema": "https://json-schema.org/draft/2020-12/schema", "$defs": v.defs, "allOf": []any{schema}}
		encoded, err := json.Marshal(root)
		if err != nil {
			return err
		}
		encoded = bytes.ReplaceAll(encoded, []byte("#/components/schemas/"), []byte("#/$defs/"))
		var parsed jsonschema.Schema
		if err := json.Unmarshal(encoded, &parsed); err != nil {
			return fmt.Errorf("contract schema is not valid JSON Schema: %w", err)
		}
		if resolved, err = parsed.Resolve(nil); err != nil {
			return fmt.Errorf("contract schema does not resolve: %w", err)
		}
		v.mu.Lock()
		v.cache[key] = resolved
		v.mu.Unlock()
	}
	var instance any
	if err := json.Unmarshal(body, &instance); err != nil {
		return err
	}
	return resolved.Validate(instance)
}
