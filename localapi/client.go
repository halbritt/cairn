package localapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"syscall"
	"time"

	"github.com/halbritt/cairn/core"
)

// Client uses a provisioned Unix API identity. Calls are never automatically
// retried: an ambiguous launch claim must not authorize another process.
type Client struct {
	http      *http.Client
	transport *http.Transport
	token     string
}

func NewClient(socket, tokenFile string) (*Client, error) {
	file, err := os.OpenFile(tokenFile, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || stat.Uid != uint32(os.Geteuid()) || info.Size() > 512 {
		return nil, &core.Error{Code: "INVALID_REQUEST", Message: "token file must be bounded and owner-only"}
	}
	token, err := io.ReadAll(io.LimitReader(file, 513))
	if err != nil {
		return nil, err
	}
	secret := strings.TrimSpace(string(token))
	if secret == "" || len(token) > 512 {
		return nil, &core.Error{Code: "INVALID_REQUEST", Message: "empty or oversized API token"}
	}
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", socket)
	}}
	return &Client{http: &http.Client{Transport: transport, Timeout: 35 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, transport: transport, token: secret}, nil
}

func (c *Client) Close() { c.transport.CloseIdleConnections() }

func (c *Client) Call(ctx context.Context, operation string, request, response any) error {
	if operation == "" || strings.Trim(operation, "abcdefghijklmnopqrstuvwxyz-") != "" {
		return &core.Error{Code: "INVALID_REQUEST", Message: "invalid API operation"}
	}
	body, err := json.Marshal(request)
	if err != nil {
		return err
	}
	if limit := RequestBodyLimit(operation); int64(len(body)) > limit {
		return &core.Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("request exceeds %d KiB", limit/1024)}
	}
	req, err := http.NewRequestWithContext(ctx, "POST", "http://cairn/v1/"+operation, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Content-Type", "application/json")
	result, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer result.Body.Close()
	encoded, err := io.ReadAll(io.LimitReader(result.Body, 8*1024*1024+1))
	if err != nil {
		return err
	}
	if len(encoded) > 8*1024*1024 {
		return fmt.Errorf("API response exceeds 8 MiB")
	}
	var envelope struct {
		Schema    string          `json:"schema"`
		RefusalID string          `json:"refusal_id"`
		OK        bool            `json:"ok"`
		Status    string          `json:"status"`
		Message   string          `json:"message"`
		Data      json.RawMessage `json:"data"`
	}
	if err = json.Unmarshal(encoded, &envelope); err != nil {
		return err
	}
	if envelope.Schema != "cairn.response/1" {
		return fmt.Errorf("unsupported API response schema")
	}
	if result.StatusCode != http.StatusOK || !envelope.OK {
		return &core.Error{Code: envelope.Status, Message: envelope.Message, RefusalID: envelope.RefusalID}
	}
	return json.Unmarshal(envelope.Data, response)
}

func (c *Client) Compile(ctx context.Context, req core.CompileRequest, dest core.Destination) (core.Package, error) {
	var result core.Package
	if err := c.Call(ctx, "compile", req, &result); err != nil {
		return result, err
	}
	// Configuration chooses destination. The caller's declaration is a check,
	// never a request to broaden delivery permission before a child launches.
	if result.Semantic.Destination != dest {
		return core.Package{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "run destination differs from the authenticated API profile"}
	}
	return result, nil
}
func (c *Client) BindRun(ctx context.Context, req core.RunBindingRequest) (result core.Observation, err error) {
	err = c.Call(ctx, "bind-run", req, &result)
	return
}

func (c *Client) RunPackage(ctx context.Context, req core.RunPackageRequest, dest core.Destination) (core.Package, error) {
	var result core.Package
	if err := c.Call(ctx, "run-package", req, &result); err != nil {
		return core.Package{}, err
	}
	if result.Semantic.Destination != dest {
		return core.Package{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "run destination differs from the authenticated API profile"}
	}
	return result, nil
}
func (c *Client) LinkRunRetrieval(ctx context.Context, req core.RunRetrievalRequest) (result core.RunRetrieval, err error) {
	err = c.Call(ctx, "link-run-retrieval", req, &result)
	return
}
func (c *Client) ClaimRun(ctx context.Context, id string) error {
	var result struct{}
	return c.Call(ctx, "claim-run", struct {
		ReceiptID string `json:"receipt_id"`
	}{id}, &result)
}
func (c *Client) RegisterManagedContext(ctx context.Context, req core.ManagedContextRequest) (result core.ManagedContext, err error) {
	err = c.Call(ctx, "register-context", req, &result)
	return
}
func (c *Client) RecordDelivery(ctx context.Context, req core.DeliveryRequest) (result core.Observation, err error) {
	err = c.Call(ctx, "delivery", req, &result)
	return
}
func (c *Client) RecordOutcome(ctx context.Context, req core.OutcomeRequest) (result core.Observation, err error) {
	err = c.Call(ctx, "outcome", req, &result)
	return
}

func (c *Client) RunStatus(ctx context.Context, id string) (result core.RunStatus, err error) {
	err = c.Call(ctx, "run-status", struct {
		ReceiptID string `json:"receipt_id"`
	}{id}, &result)
	return
}
