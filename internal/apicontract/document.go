package apicontract

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/types"
	"sort"
	"strings"

	"github.com/halbritt/cairn/localapi"
)

// EnvelopeSchema is the only response envelope schema the API emits.
const EnvelopeSchema = "cairn.response/1"

// Document is the generated OpenAPI 3.1 contract.
type Document map[string]any

// Build assembles the contract for the extracted source.
func Build(source *Source) (Document, error) {
	r := newReflector()
	paths := map[string]any{}
	var unclassified []string
	served := map[string]bool{}
	for _, route := range source.Routes {
		// encoding/json also accepts a top-level null as the zero request. The
		// reserved protocol guard is verified and stripped before decoding, so
		// only the top-level accepted view carries it.
		requestSchema := nullable(withProtocolGuard(r, r.schema(route.Request, accepted)))
		canonicalRequest := r.schema(route.Request, canonical)
		responseSchema := r.schema(route.Response, canonical)
		for _, operation := range route.Operations {
			served[operation] = true
			properties := canonicalProperties(r, route.Request)
			if _, listed := retryByOperation[operation]; listed && properties["request_id"] {
				return nil, fmt.Errorf("%s has a request_id; remove it from retryByOperation", operation)
			}
			retry, ok := retryClass(operation, properties)
			if !ok {
				unclassified = append(unclassified, operation)
			}
			allowed, classified := localapi.RemoteOperation(operation)
			remote := "denied"
			if !classified {
				remote = "unclassified"
			} else if allowed {
				remote = "allowed"
			}
			roles := []string{"agent", "observer"}
			remoteRoles := []string{}
			if remote == "allowed" {
				remoteRoles = []string{"agent"}
				for _, other := range localapi.RemoteRoleOperations() {
					if other == operation {
						remoteRoles = []string{"agent", "observer"}
					}
				}
			}
			sessionHeaders := "optional"
			if strings.HasPrefix(operation, "agent-") {
				sessionHeaders = "forbidden"
			}
			paths["/v1/"+operation] = map[string]any{"post": map[string]any{
				"operationId": operation,
				"requestBody": map[string]any{
					"required":    true,
					"description": "Exactly one JSON value. Accepted input: unknown fields are refused; absent and null fields decode as zero values; keys match case-insensitively (encoding/json). Send the canonical shape.",
					"content":     map[string]any{"application/json": map[string]any{"schema": requestSchema}},
				},
				"parameters": []any{
					map[string]any{"$ref": "#/components/parameters/AgentID"},
					map[string]any{"$ref": "#/components/parameters/ExecutionID"},
					map[string]any{"$ref": "#/components/parameters/Protocol"},
					map[string]any{"$ref": "#/components/parameters/RelayProtocol"},
				},
				"responses": map[string]any{
					"200": map[string]any{
						"description": "Success envelope",
						"content": map[string]any{"application/json": map[string]any{"schema": map[string]any{
							"type": "object",
							"properties": map[string]any{
								"schema": map[string]any{"const": EnvelopeSchema},
								"ok":     map[string]any{"const": true},
								"status": map[string]any{"const": "OK"},
								"data":   responseSchema,
							},
							"required": []string{"data", "ok", "schema", "status"},
						}}},
					},
					"default": map[string]any{"$ref": "#/components/responses/Error"},
				},
				"x-cairn-canonical-request":          canonicalRequest,
				"x-cairn-go-types":                   map[string]string{"request": route.Request.String(), "response": route.Response.String()},
				"x-cairn-request-limit-bytes":        localapi.RequestBodyLimit(operation),
				"x-cairn-remote":                     remote,
				"x-cairn-remote-roles":               remoteRoles,
				"x-cairn-local-roles":                roles,
				"x-cairn-requires-local-destination": route.RequiresLocalDestination,
				"x-cairn-session-headers":            sessionHeaders,
				"x-cairn-retry":                      retry,
				"x-cairn-source":                     route.File,
			}}
		}
	}
	for operation := range retryByOperation {
		if !served[operation] {
			unclassified = append(unclassified, operation+" (stale entry)")
		}
	}
	if len(unclassified) > 0 {
		sort.Strings(unclassified)
		return nil, fmt.Errorf("operations without a retry classification: %s", strings.Join(unclassified, ", "))
	}
	for path, item := range paths {
		if item.(map[string]any)["post"].(map[string]any)["x-cairn-remote"] == "unclassified" {
			return nil, fmt.Errorf("%s has no remote allowlist decision", path)
		}
	}
	documented := map[string]bool{"Authorization": true, "Cairn-Agent-ID": true, "Cairn-Execution-ID": true, localapi.ProtocolHeader: true, localapi.RelayProtocolHeader: true}
	for _, header := range source.Headers {
		if !documented[header] {
			return nil, fmt.Errorf("request header %s is read by the server but not documented", header)
		}
	}
	envelope := r.schema(source.Envelope, canonical)
	if r.err != nil {
		return nil, r.err
	}
	errorEnvelope := map[string]any{
		"allOf": []any{envelope, map[string]any{
			"properties": map[string]any{
				"schema": map[string]any{"const": EnvelopeSchema},
				"ok":     map[string]any{"const": false},
				"status": map[string]any{"type": "string", "description": "Error code; see x-cairn-errors. Clients treat an unknown code as a refusal with that code."},
			},
			"not": map[string]any{"required": []string{"data"}},
		}},
	}
	var codes []any
	seenCodes := map[ErrorCode]bool{}
	for _, code := range source.Errors {
		if !seenCodes[code] {
			seenCodes[code] = true
			codes = append(codes, code)
		}
	}
	document := Document{
		"openapi":           "3.1.0",
		"jsonSchemaDialect": "https://json-schema.org/draft/2020-12/schema",
		"info": map[string]any{
			"title":       "Cairn agent API",
			"version":     EnvelopeSchema,
			"description": "Generated from localapi by internal/apicontract (make contract). Do not edit by hand. See docs/api-contract.md for semantics the schema cannot express.",
		},
		"servers": []any{
			map[string]any{"url": "http://cairn", "description": "Local Unix socket (~/.local/share/cairn/api.sock); the host name is ignored"},
			map[string]any{"url": "https://{host}", "description": "Central network listener (cairn serve --listen) reached through a relay; remote machine profiles only", "variables": map[string]any{"host": map[string]any{"default": "central.example.ts.net:8789"}}},
		},
		"security": []any{map[string]any{"bearer": []string{}}},
		"paths":    paths,
		"components": map[string]any{
			"securitySchemes": map[string]any{"bearer": map[string]any{
				"type": "http", "scheme": "bearer",
				"description": "Authorization: Bearer <profile token>, at most 512 bytes. The token's configured profile fixes principal, collection, role (agent|observer) and destination (local|hosted); no request field can change them.",
			}},
			"parameters": map[string]any{
				"AgentID":       map[string]any{"name": "Cairn-Agent-ID", "in": "header", "required": false, "schema": map[string]any{"type": "string", "format": "uuid"}, "description": "Registered session UUID. Send together with Cairn-Execution-ID to act as that session's inbox; forbidden on agent-* directory operations."},
				"Protocol":      map[string]any{"name": localapi.ProtocolHeader, "in": "header", "required": false, "schema": map[string]any{"type": "string", "pattern": "^[1-9][0-9]{0,3}$"}, "description": "Wire protocol the client speaks: one canonical decimal value. Absent means protocol 1, or defers to the body guard. Malformed or duplicate: 400 INVALID_REQUEST. Outside the server's range: 426 PROTOCOL_UNSUPPORTED. Checked after authentication and before any effect; never affects authority."},
				"RelayProtocol": map[string]any{"name": localapi.RelayProtocolHeader, "in": "header", "required": false, "schema": map[string]any{"type": "string", "pattern": "^[1-9][0-9]{0,3}$"}, "description": "Set by the relay to its own protocol, overwriting any client value; diagnostic only. Malformed: 400 INVALID_REQUEST."},
				"ExecutionID":   map[string]any{"name": "Cairn-Execution-ID", "in": "header", "required": false, "schema": map[string]any{"type": "string", "format": "uuid"}, "description": "Current execution UUID of the session named by Cairn-Agent-ID. A replaced or restored execution is refused with STALE_SESSION."},
			},
			"responses": map[string]any{"Error": map[string]any{
				"description": "Error envelope. HTTP status and code pairs are listed in x-cairn-errors.",
				"content":     map[string]any{"application/json": map[string]any{"schema": map[string]any{"$ref": "#/components/schemas/Error"}}},
			}},
			"schemas": withError(r.schemas, errorEnvelope),
		},
		"x-cairn-errors":               codes,
		"x-cairn-store-codes":          source.StoreCodes,
		"x-cairn-default-error-status": source.DefaultCode,
		"x-cairn-request-headers":      source.Headers,
		"x-cairn-limits": map[string]any{
			"authorization-token-bytes": 512,
			"response-body-bytes":       localapi.ResponseBodyLimit,
			"max-request-body-bytes":    localapi.MaxRequestBodyLimit,
			"request-timeout-seconds":   30,
		},
		"x-cairn-retry-classes": retryClasses,
		"x-cairn-protocol": map[string]any{
			"min": localapi.Protocol.Min, "current": localapi.Protocol.Current,
			"header": localapi.ProtocolHeader, "relay-header": localapi.RelayProtocolHeader,
			"body-field": localapi.BodyProtocolField,
			"policy":     "docs/api-compatibility.md",
		},
	}
	return document, nil
}

// withProtocolGuard returns the top-level accepted request with the reserved
// protocol guard field, which serveJSON verifies and strips before decoding.
func withProtocolGuard(r *reflector, schema map[string]any) map[string]any {
	component := schema
	if ref, ok := schema["$ref"].(string); ok {
		component, _ = r.schemas[strings.TrimPrefix(ref, "#/components/schemas/")].(map[string]any)
	}
	if component == nil || component["type"] != "object" {
		return schema
	}
	copy := map[string]any{}
	for key, value := range component {
		copy[key] = value
	}
	properties := map[string]any{localapi.BodyProtocolField: map[string]any{"type": "integer", "minimum": 1, "description": "Reserved client minimum protocol guard; verified and removed before decoding. Older servers refuse it as an unknown field."}}
	for name, property := range component["properties"].(map[string]any) {
		properties[name] = property
	}
	copy["properties"] = properties
	return copy
}

func withError(schemas map[string]any, errorEnvelope map[string]any) map[string]any {
	out := map[string]any{"Error": errorEnvelope}
	for name, schema := range schemas {
		out[name] = schema
	}
	return out
}

// canonicalProperties lists the top-level JSON names of a request type.
func canonicalProperties(r *reflector, t types.Type) map[string]bool {
	properties := map[string]bool{}
	schema := r.schema(t, canonical)
	if ref, ok := schema["$ref"].(string); ok {
		schema, _ = r.schemas[strings.TrimPrefix(ref, "#/components/schemas/")].(map[string]any)
	}
	if fields, ok := schema["properties"].(map[string]any); ok {
		for name := range fields {
			properties[name] = true
		}
	}
	return properties
}

// Marshal renders the document deterministically.
func (d Document) Marshal() ([]byte, error) {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(d); err != nil {
		return nil, err
	}
	return buffer.Bytes(), nil
}

// Generate loads the localapi source at dir and renders the contract.
func Generate(dir string) ([]byte, error) {
	source, err := Load(dir)
	if err != nil {
		return nil, err
	}
	document, err := Build(source)
	if err != nil {
		return nil, err
	}
	return document.Marshal()
}
