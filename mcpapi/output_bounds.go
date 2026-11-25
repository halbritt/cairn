package mcpapi

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/internal/outputbound"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type boundedToolPayload struct {
	Truncated      bool   `json:"truncated"`
	TotalBytes     int    `json:"total_bytes"`
	OmittedBytes   int    `json:"omitted_bytes"`
	FullOutputPath string `json:"full_output_path"`
	Excerpt        string `json:"excerpt,omitempty"`
}

func (c Config) effectiveOutputMax() int {
	if c.UnboundedOutput {
		return 0
	}
	if c.OutputMaxBytes > 0 {
		if outputbound.ResolveMaxBytes(c.OutputMaxBytes, "CAIRN_MCP_OUTPUT_MAX_BYTES", "CAIRN_TOOL_OUTPUT_MAX_BYTES") == 0 {
			return 0
		}
		return c.OutputMaxBytes
	}
	return outputbound.ResolveMaxBytes(-1, "CAIRN_MCP_OUTPUT_MAX_BYTES", "CAIRN_TOOL_OUTPUT_MAX_BYTES")
}

func (t memoryTools) boundedToolResult(value any, err error, room int) (*mcp.CallToolResult, any, error) {
	return boundedResultWithConfig(value, err, room, t.config, false, t.facadeBuild)
}

func (t memoryTools) boundedRetrievalOutput(value any, err error, room int) (*mcp.CallToolResult, any, error) {
	withMeta := t.config.SearchView == "compact/1"
	return boundedResultWithConfig(value, err, room, t.config, withMeta, t.facadeBuild)
}

func (t messagingTools) boundedToolResult(value any, err error, room int) (*mcp.CallToolResult, any, error) {
	return boundedResultWithConfig(value, err, room, t.config, false, buildinfo.Info{})
}

func boundedResultWithConfig(value any, err error, room int, cfg Config, withRetrievalMeta bool, facadeBuild buildinfo.Info) (*mcp.CallToolResult, any, error) {
	outMax := cfg.effectiveOutputMax()
	if err != nil {
		var failure *core.Error
		if errors.As(err, &failure) {
			message := failure.Code + ": " + strings.TrimPrefix(failure.Message, failure.Code+": ")
			if outMax > 0 && len(message) > outMax {
				if fullPath, spoolErr := outputbound.SpoolRaw([]byte(message), cfg.ToolOutputDir, "log"); spoolErr == nil {
					excerpt, _ := outputbound.BuildExcerpt([]byte(message), max(256, outMax-128), fullPath)
					message = failure.Code + ": " + excerpt
				}
			}
			if failure.RefusalID != "" {
				message += "; refusal_id=" + failure.RefusalID
			}
			if withRetrievalMeta {
				envelope, envErr := retrievalResultEnvelope(facadeBuild)
				if envErr != nil {
					return nil, nil, envErr
				}
				return boundedRetrievalError(envelope, message, max(room, len(message)+256))
			}
			return nil, nil, errors.New(message)
		}
		const generic = "Cairn operation failed; inspect the local API connection and service"
		if withRetrievalMeta {
			envelope, envErr := retrievalResultEnvelope(facadeBuild)
			if envErr != nil {
				return nil, nil, envErr
			}
			return boundedRetrievalError(envelope, generic, max(room, len(generic)+256))
		}
		return nil, nil, errors.New(generic)
	}

	makeEnvelope := func() (*mcp.CallToolResult, error) {
		if withRetrievalMeta {
			return retrievalResultEnvelope(facadeBuild)
		}
		return new(mcp.CallToolResult), nil
	}

	compact, err := json.Marshal(value)
	if err != nil {
		return nil, nil, err
	}

	if outMax == 0 {
		envelope, envErr := makeEnvelope()
		if envErr != nil {
			return nil, nil, envErr
		}
		envelope.Content = []mcp.Content{&mcp.TextContent{Text: string(compact)}}
		return envelope, nil, nil
	}

	limit := outMax
	if room > 0 && room < limit {
		limit = room
	}

	envelope, envErr := makeEnvelope()
	if envErr != nil {
		return nil, nil, envErr
	}
	envelope.Content = []mcp.Content{&mcp.TextContent{Text: string(compact)}}
	encoded, err := json.Marshal(envelope)
	if err != nil {
		return nil, nil, err
	}
	if len(encoded) <= limit {
		return envelope, nil, nil
	}

	var prettyBuf bytes.Buffer
	pretty := compact
	if indentErr := json.Indent(&prettyBuf, compact, "", "  "); indentErr == nil {
		pretty = prettyBuf.Bytes()
	}
	fullPath, err := outputbound.SpoolRaw(pretty, cfg.ToolOutputDir, "json")
	if err != nil {
		return nil, nil, fmt.Errorf("spool oversized tool output: %w", err)
	}
	totalBytes := len(pretty)
	for capBytes := max(128, limit-256); capBytes >= 64; capBytes = capBytes * 3 / 4 {
		excerpt, omitted := outputbound.BuildExcerpt(pretty, capBytes, fullPath)
		payload := boundedToolPayload{
			Truncated:      true,
			TotalBytes:     totalBytes,
			OmittedBytes:   omitted,
			FullOutputPath: fullPath,
			Excerpt:        excerpt,
		}
		candEnv, envErr := makeEnvelope()
		if envErr != nil {
			return nil, nil, envErr
		}
		if res, _, encErr := encodeToolResultInto(candEnv, payload, limit); encErr == nil {
			return res, nil, nil
		}
		if capBytes == 64 {
			break
		}
	}

	fallback := boundedToolPayload{
		Truncated:      true,
		TotalBytes:     totalBytes,
		OmittedBytes:   totalBytes,
		FullOutputPath: fullPath,
	}
	candEnv, envErr := makeEnvelope()
	if envErr != nil {
		return nil, nil, envErr
	}
	body, err := json.Marshal(fallback)
	if err != nil {
		return nil, nil, err
	}
	candEnv.Content = []mcp.Content{&mcp.TextContent{Text: string(body)}}
	return candEnv, nil, nil
}
