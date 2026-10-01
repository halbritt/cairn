package apicontract

import (
	"fmt"
	"go/types"
	"reflect"
	"runtime"
	"sort"
	"strings"
)

// mode selects which wire view a schema describes.
type mode int

const (
	// canonical is what Cairn produces and what clients should send: fields
	// without omitempty/omitzero are always present, nil values are null.
	canonical mode = iota
	// accepted is what serveJSON's encoding/json decoder admits: unknown fields
	// are refused, every field may be absent, and null is accepted for every
	// field (a no-op for non-pointer fields).
	accepted
)

type reflector struct {
	schemas map[string]any
	names   map[string]types.Type
	err     error
}

func newReflector() *reflector {
	return &reflector{schemas: map[string]any{}, names: map[string]types.Type{}}
}

func (r *reflector) fail(format string, args ...any) map[string]any {
	if r.err == nil {
		r.err = fmt.Errorf(format, args...)
	}
	return map[string]any{}
}

func nullable(schema map[string]any) map[string]any {
	if kind, ok := schema["type"].(string); ok && len(schema) <= 3 {
		copy := map[string]any{}
		for key, value := range schema {
			copy[key] = value
		}
		copy["type"] = []string{kind, "null"}
		return copy
	}
	if kinds, ok := schema["type"].([]string); ok {
		for _, kind := range kinds {
			if kind == "null" {
				return schema
			}
		}
	}
	if len(schema) == 0 {
		return schema // already any JSON value
	}
	return map[string]any{"anyOf": []any{schema, map[string]any{"type": "null"}}}
}

func componentName(named *types.Named, m mode) string {
	object := named.Obj()
	name := object.Name()
	if object.Pkg() != nil {
		name = object.Pkg().Name() + "." + name
	}
	if m == accepted {
		name += ".accepted"
	}
	return name
}

func (r *reflector) schema(t types.Type, m mode) map[string]any {
	t = types.Unalias(t)
	switch t := t.(type) {
	case *types.Named:
		object := t.Obj()
		if object.Pkg() != nil {
			switch object.Pkg().Path() + "." + object.Name() {
			case "time.Time":
				return map[string]any{"type": "string", "format": "date-time"}
			case "github.com/halbritt/cairn/core.SemanticQueryProjection":
				// The closed decoder requires every non-null field in this output declaration.
				schema := r.schema(t.Underlying(), canonical)
				schema["additionalProperties"] = false
				return schema
			case "encoding/json.RawMessage":
				return map[string]any{"description": "any JSON value, passed through unchanged"}
			}
		}
		if hasMethod(t, "MarshalJSON") || hasMethod(t, "UnmarshalJSON") || hasMethod(t, "MarshalText") || hasMethod(t, "UnmarshalText") {
			return r.fail("%s has custom JSON encoding; add an explicit schema", t)
		}
		if _, ok := t.Underlying().(*types.Struct); !ok {
			return r.schema(t.Underlying(), m)
		}
		name := componentName(t, m)
		if previous, ok := r.names[name]; ok && !types.Identical(previous, t) {
			return r.fail("component name %s used by two types", name)
		}
		if _, done := r.schemas[name]; !done {
			r.names[name] = t
			r.schemas[name] = map[string]any{} // placeholder for recursion
			body := r.structSchema(t.Underlying().(*types.Struct), m)
			body["x-go-type"] = t.String()
			r.schemas[name] = body
		}
		return map[string]any{"$ref": "#/components/schemas/" + name}
	case *types.Pointer:
		return nullable(r.schema(t.Elem(), m))
	case *types.Slice:
		if basic, ok := t.Elem().Underlying().(*types.Basic); ok && basic.Kind() == types.Byte {
			if m == accepted {
				return map[string]any{"anyOf": []any{
					nullable(map[string]any{"type": "string", "contentEncoding": "base64"}),
					map[string]any{"type": "array", "items": r.elementSchema(t.Elem(), m)},
				}}
			}
			return nullable(map[string]any{"type": "string", "contentEncoding": "base64"})
		}
		return nullable(map[string]any{"type": "array", "items": r.elementSchema(t.Elem(), m)})
	case *types.Array:
		if m == accepted {
			// encoding/json zero-fills short arrays and skips surplus values.
			schema := map[string]any{"type": "array"}
			if t.Len() > 0 {
				prefix := make([]any, t.Len())
				for i := range prefix {
					prefix[i] = r.elementSchema(t.Elem(), m)
				}
				schema["prefixItems"] = prefix
			}
			return schema
		}
		return map[string]any{"type": "array", "items": r.schema(t.Elem(), m), "minItems": t.Len(), "maxItems": t.Len()}
	case *types.Map:
		key, ok := t.Key().Underlying().(*types.Basic)
		if !ok || key.Info()&(types.IsString|types.IsInteger) == 0 {
			return r.fail("map key %s is not a JSON object key", t.Key())
		}
		schema := map[string]any{"type": "object", "additionalProperties": r.elementSchema(t.Elem(), m)}
		if key.Info()&types.IsInteger != 0 {
			schema["propertyNames"] = map[string]any{"pattern": "^-?[0-9]+$"}
		}
		return nullable(schema)
	case *types.Interface:
		return map[string]any{}
	case *types.Struct:
		return r.structSchema(t, m)
	case *types.Basic:
		switch {
		case t.Info()&types.IsBoolean != 0:
			return map[string]any{"type": "boolean"}
		case t.Info()&types.IsInteger != 0:
			schema := map[string]any{"type": "integer"}
			bits := uint(types.SizesFor("gc", runtime.GOARCH).Sizeof(t) * 8)
			if t.Info()&types.IsUnsigned != 0 {
				schema["minimum"] = 0
				schema["maximum"] = ^uint64(0) >> (64 - bits)
			} else {
				maximum := int64(^uint64(0) >> (65 - bits))
				schema["minimum"], schema["maximum"] = -maximum-1, maximum
			}
			return schema
		case t.Info()&types.IsFloat != 0:
			return map[string]any{"type": "number"}
		case t.Info()&types.IsString != 0:
			return map[string]any{"type": "string"}
		}
	}
	return r.fail("unsupported type %s", t)
}

func (r *reflector) elementSchema(t types.Type, m mode) map[string]any {
	schema := r.schema(t, m)
	if m == accepted {
		return nullable(schema)
	}
	return schema
}

func hasMethod(t *types.Named, name string) bool {
	for _, typ := range []types.Type{t, types.NewPointer(t)} {
		set := types.NewMethodSet(typ)
		for i := 0; i < set.Len(); i++ {
			if set.At(i).Obj().Name() == name {
				return true
			}
		}
	}
	return false
}

type jsonField struct {
	name      string
	depth     int
	tagged    bool
	omit      bool
	schema    map[string]any
	fieldType types.Type
}

// collectFields applies encoding/json's field rules: exported fields,
// json tags, "-", embedded struct promotion and depth/tag conflict resolution.
func (r *reflector) collectFields(s *types.Struct, m mode, depth int, visited map[*types.Struct]bool, out *[]jsonField) {
	if visited[s] {
		return
	}
	visited[s] = true
	defer delete(visited, s)
	for i := 0; i < s.NumFields(); i++ {
		field := s.Field(i)
		tag := reflect.StructTag(s.Tag(i)).Get("json")
		if tag == "-" {
			continue
		}
		name, options, _ := strings.Cut(tag, ",")
		fieldType := types.Unalias(field.Type())
		if field.Embedded() && name == "" {
			embedded := fieldType
			if pointer, ok := embedded.(*types.Pointer); ok {
				embedded = pointer.Elem()
			}
			if inner, ok := embedded.Underlying().(*types.Struct); ok {
				r.collectFields(inner, m, depth+1, visited, out)
				continue
			}
			if !field.Exported() {
				continue
			}
		} else if !field.Exported() {
			continue
		}
		tagged := name != ""
		if name == "" {
			name = field.Name()
		}
		omit := false
		quoted := false
		for _, option := range strings.Split(options, ",") {
			switch option {
			case "omitempty":
				omit = omit || canBeEmpty(fieldType)
			case "omitzero":
				omit = true
			case "string":
				quoted = true
			}
		}
		schema := r.schema(fieldType, m)
		if quoted {
			if basic, ok := fieldType.Underlying().(*types.Basic); ok && basic.Info()&(types.IsBoolean|types.IsNumeric|types.IsString) != 0 {
				schema = map[string]any{"type": "string", "description": "JSON-encoded " + basic.Name() + " inside a string"}
			}
		}
		*out = append(*out, jsonField{name: name, depth: depth, tagged: tagged, omit: omit, schema: schema, fieldType: fieldType})
	}
}

// canBeEmpty reports whether encoding/json's omitempty can omit the type.
func canBeEmpty(t types.Type) bool {
	switch u := t.Underlying().(type) {
	case *types.Struct:
		return false
	case *types.Array:
		return u.Len() == 0
	}
	return true
}

func (r *reflector) structSchema(s *types.Struct, m mode) map[string]any {
	var fields []jsonField
	r.collectFields(s, m, 0, map[*types.Struct]bool{}, &fields)
	byName := map[string][]jsonField{}
	var order []string
	for _, field := range fields {
		key := field.name
		if _, seen := byName[key]; !seen {
			order = append(order, key)
		}
		byName[key] = append(byName[key], field)
	}
	properties := map[string]any{}
	var required []string
	for _, name := range order {
		candidates := byName[name]
		sort.SliceStable(candidates, func(i, j int) bool { return candidates[i].depth < candidates[j].depth })
		shallow := candidates[0].depth
		var winners []jsonField
		for _, candidate := range candidates {
			if candidate.depth == shallow {
				winners = append(winners, candidate)
			}
		}
		if len(winners) > 1 {
			var tagged []jsonField
			for _, winner := range winners {
				if winner.tagged {
					tagged = append(tagged, winner)
				}
			}
			if len(tagged) != 1 {
				continue // encoding/json drops ambiguous fields
			}
			winners = tagged
		}
		field := winners[0]
		schema := field.schema
		if m == accepted {
			schema = nullable(schema)
		} else if !field.omit {
			required = append(required, field.name)
		}
		properties[field.name] = schema
	}
	schema := map[string]any{"type": "object", "properties": properties}
	if m == accepted {
		schema["additionalProperties"] = false
	}
	if len(required) > 0 {
		sort.Strings(required)
		schema["required"] = required
	}
	return schema
}
