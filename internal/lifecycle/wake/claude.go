package wake

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/procpeer"
)

// Claude channel client constants matching integrations/lifecycle/claude_channel.py.
const (
	ClaudeConnectTimeout = 4 * time.Second
	ClaudeReplyTimeout   = 4 * time.Second
	ClaudeMaxLineBytes   = procpeer.MaxLine
	ClaudeChannelSchema  = "cairn.claude-channel/1"
)

// ClaudeChannelError reports a post-send or uncertain Claude channel failure
// that must never be automatically resent.
type ClaudeChannelError struct {
	Message string
	Cause   error
}

func (e *ClaudeChannelError) Error() string { return e.Message }
func (e *ClaudeChannelError) Unwrap() error { return e.Cause }

// ClaudeChannelUnavailable reports a checked pre-send refusal before any
// channel notification reached the bridge transport.
type ClaudeChannelUnavailable struct {
	Message string
	Cause   error
}

func (e *ClaudeChannelUnavailable) Error() string { return e.Message }
func (e *ClaudeChannelUnavailable) Unwrap() error { return e.Cause }

// IsClaudeChannelUnavailable reports whether err is a retryable pre-send refusal.
func IsClaudeChannelUnavailable(err error) bool {
	var target *ClaudeChannelUnavailable
	return errors.As(err, &target)
}

// IsClaudeChannelError reports whether err is a Claude channel error (including unavailable).
func IsClaudeChannelError(err error) bool {
	var target *ClaudeChannelError
	return errors.As(err, &target) || IsClaudeChannelUnavailable(err)
}

// ClaudeChannelMeta preserves the exact wire field order required by claudechannel.go
// and inbox_recall_wake: native_session_id, agent_id, execution_id, delivery_id.
type ClaudeChannelMeta struct {
	NativeSessionID string `json:"native_session_id"`
	AgentID         string `json:"agent_id"`
	ExecutionID     string `json:"execution_id"`
	DeliveryID      string `json:"delivery_id"`
}

// ClaudeChannelEndpoint describes a selected cairn.claude-channel/1 registry entry.
type ClaudeChannelEndpoint struct {
	Socket  string     `json:"socket"`
	Process ProcessRef `json:"process"`
	Parent  ProcessRef `json:"parent"`
}

type claudeChannelRequest struct {
	Parent  ProcessRef        `json:"parent"`
	Content string            `json:"content"`
	Meta    ClaudeChannelMeta `json:"meta"`
}

// SelectClaudeChannelEndpoint selects the channel bridge registry for exactly
// sessionProcess, verifying schema, parent identity, absolute socket path, and
// non-zombie bridge liveness.
func SelectClaudeChannelEndpoint(harness, channelDir string, sessionProcess ProcessRef) (*ClaudeChannelEndpoint, bool) {
	return selectClaudeChannelEndpointAt("/proc", harness, channelDir, sessionProcess)
}

func selectClaudeChannelEndpointAt(procRoot, harness, channelDir string, sessionProcess ProcessRef) (*ClaudeChannelEndpoint, bool) {
	if harness != HarnessClaude || channelDir == "" || sessionProcess.PID <= 0 {
		return nil, false
	}
	raw, err := os.ReadFile(filepath.Join(channelDir, fmt.Sprintf("%d.json", sessionProcess.PID)))
	if err != nil || !utf8.Valid(raw) {
		return nil, false
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var record map[string]any
	if err := dec.Decode(&record); err != nil || record == nil {
		return nil, false
	}
	schema, ok := record["schema"].(string)
	if !ok || schema != ClaudeChannelSchema {
		return nil, false
	}
	parentRef, ok := decodeProcessProjection(record["parent"])
	if !ok || parentRef != sessionProcess {
		return nil, false
	}
	bridgeRef, ok := decodeProcessProjection(record["process"])
	if !ok {
		return nil, false
	}
	sock, ok := record["socket"].(string)
	if !ok || sock == "" || !filepath.IsAbs(sock) {
		return nil, false
	}
	if !processAliveAt(procRoot, bridgeRef) {
		return nil, false
	}
	return &ClaudeChannelEndpoint{
		Socket:  sock,
		Process: bridgeRef,
		Parent:  parentRef,
	}, true
}

func decodeProcessProjection(raw any) (ProcessRef, bool) {
	obj, ok := raw.(map[string]any)
	if !ok || obj == nil {
		return ProcessRef{}, false
	}
	pidVal, ok := parseExactInt64(obj["pid"])
	if !ok || pidVal <= 0 {
		return ProcessRef{}, false
	}
	startVal, ok := parseExactUint64(obj["start"])
	if !ok {
		return ProcessRef{}, false
	}
	bootVal, ok := obj["boot"].(string)
	if !ok {
		return ProcessRef{}, false
	}
	return ProcessRef{PID: int(pidVal), Start: startVal, Boot: bootVal}, true
}

func parseExactInt64(v any) (int64, bool) {
	num, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	s := num.String()
	if strings.ContainsAny(s, ".eE") {
		return 0, false
	}
	val, err := num.Int64()
	return val, err == nil
}

func parseExactUint64(v any) (uint64, bool) {
	num, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	s := num.String()
	if strings.ContainsAny(s, ".eE-") {
		return 0, false
	}
	var val uint64
	_, err := fmt.Sscan(s, &val)
	return val, err == nil
}

// ClaudeChannelClient configures timeouts for one verified Claude channel write.
type ClaudeChannelClient struct {
	ConnectTimeout time.Duration
	ReplyTimeout   time.Duration
}

// WriteClaudeChannel performs one bounded channel write to an already-selected
// Claude channel bridge using the default 4s connect and 4s reply deadlines.
func WriteClaudeChannel(ctx context.Context, endpoint string, bridge, parent ProcessRef, content string, meta ClaudeChannelMeta) error {
	var client ClaudeChannelClient
	return client.Write(ctx, endpoint, bridge, parent, content, meta)
}

// Write connects to endpoint in process-owner mode, verifies bridge peer identity
// before sending any bytes, writes the compact JSON request line, and classifies
// the single bounded JSON reply line.
func (c ClaudeChannelClient) Write(ctx context.Context, endpoint string, bridge, parent ProcessRef, content string, meta ClaudeChannelMeta) error {
	connectTimeout := c.ConnectTimeout
	if connectTimeout <= 0 {
		connectTimeout = ClaudeConnectTimeout
	}
	replyTimeout := c.ReplyTimeout
	if replyTimeout <= 0 {
		replyTimeout = ClaudeReplyTimeout
	}

	framed, err := CompactJSONLine(
		claudeChannelRequest{Parent: parent, Content: content, Meta: meta},
		ClaudeMaxLineBytes,
		&ClaudeChannelUnavailable{Message: "claude channel request exceeds the line limit"},
	)
	if err != nil {
		if IsClaudeChannelUnavailable(err) {
			return err
		}
		return &ClaudeChannelUnavailable{Message: "claude channel endpoint is unavailable", Cause: err}
	}

	policy := procpeer.ClientPolicy{
		Owner:       procpeer.ProcessOwner,
		ExpectedUID: nil,
		Messages: procpeer.ClientMessages{
			WrongProcess:        "claude channel socket belongs to a different process",
			WrongUser:           "native socket belongs to another user",
			IdentityChanged:     "claude channel bridge identity has changed",
			EndpointUnavailable: "claude channel endpoint is unavailable",
		},
	}

	conn, _, err := procpeer.ConnectClient(ctx, endpoint, bridge, policy, connectTimeout)
	if err != nil {
		var refusal *procpeer.ClientRefusal
		if errors.As(err, &refusal) {
			return &ClaudeChannelUnavailable{Message: refusal.Message, Cause: refusal.Cause}
		}
		return &ClaudeChannelUnavailable{Message: "claude channel endpoint is unavailable", Cause: err}
	}
	defer func() { _ = conn.Close() }()

	if err := procpeer.WriteClientFrame(conn, framed, connectTimeout); err != nil {
		return &ClaudeChannelError{Message: "claude channel outcome is uncertain; do not resend", Cause: err}
	}

	line, err := procpeer.ReadClientLineUntil(conn, ClaudeMaxLineBytes, time.Now().Add(replyTimeout), true)
	if err != nil {
		if errors.Is(err, io.ErrShortBuffer) {
			return &ClaudeChannelError{Message: "claude channel reply exceeds the line limit", Cause: err}
		}
		if errors.Is(err, io.EOF) {
			return &ClaudeChannelError{Message: "claude channel closed before replying", Cause: err}
		}
		var netErr net.Error
		if errors.Is(err, os.ErrDeadlineExceeded) || (errors.As(err, &netErr) && netErr.Timeout()) {
			return &ClaudeChannelError{Message: "claude channel reply timed out; submission uncertain", Cause: err}
		}
		return &ClaudeChannelError{Message: "claude channel outcome is uncertain; do not resend", Cause: err}
	}

	if !utf8.Valid(line) {
		return &ClaudeChannelError{Message: "claude channel outcome is uncertain; do not resend"}
	}
	var parsed any
	dec := json.NewDecoder(bytes.NewReader(line))
	dec.UseNumber()
	if err := dec.Decode(&parsed); err != nil || dec.More() {
		return &ClaudeChannelError{Message: "claude channel outcome is uncertain; do not resend", Cause: err}
	}
	obj, ok := parsed.(map[string]any)
	if !ok || obj == nil {
		return &ClaudeChannelError{Message: "claude channel reply is not an object"}
	}
	status, _ := obj["status"].(string)
	switch status {
	case "written":
		return nil
	case "unavailable":
		return &ClaudeChannelUnavailable{Message: "claude channel refused this submission before sending"}
	default:
		return &ClaudeChannelError{Message: "claude channel write is uncertain; do not resend"}
	}
}
