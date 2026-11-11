// Package memoryclient supplies the serial, per-invocation memory operations
// used by lifecycle consumers. The caller owns the authenticated API client.
package memoryclient

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"maps"
	"net"
	"net/url"
	"slices"
	"strconv"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/indexview"
	"github.com/halbritt/cairn/localapi"
)

const (
	searchRoom   = 8000
	callTimeout  = 5 * time.Second
	receiptLimit = 8
)

var (
	ErrNotConfirmed     = errors.New("memory operation not confirmed")
	ErrSearchStatus     = errors.New("memory search status refused")
	ErrNotHosted        = errors.New("memory search destination is not hosted")
	ErrCheckpointRecord = errors.New("malformed checkpoint record")
	errTransport        = errors.New("memory API transport failed")
)

// responseError keeps diagnostics fixed while retaining the cause for inspection.
type responseError struct{ reason, cause error }

func (e *responseError) Error() string        { return e.reason.Error() }
func (e *responseError) Unwrap() error        { return e.cause }
func (e *responseError) Is(target error) bool { return target == e.reason }

type Options struct {
	Scope         core.Scope
	Context       *core.ContextPins
	CommandPrefix []string
	Diagnostics   localapi.ClientDiagnostics
	// ObserveOperation reports actual initiated search/pull durations to the
	// reached hook meter. Known-zero pull refusals never start an operation.
	ObserveOperation func(string, time.Duration)
	ObserveRPC       func(string, time.Time, json.RawMessage, json.RawMessage, error)
}

type SearchOptions struct {
	Room      *int
	Entities  []string
	Kinds     []string
	Semantic  bool
	Offset    *int
	RequestID *string
	Timeout   *time.Duration
}

// Accounting is component accounting, not the complete lifecycle hook meter.
type Accounting struct {
	OperationCalls map[string]int
	PullCalls      map[string]int
	ReceiptCredits map[string]int
	SearchReceipts []string
}

// Client is serial, like the Source Memory object. It never closes the SDK.
type Client struct {
	api              *localapi.Client
	scope            core.Scope
	context          core.ContextPins
	commandPrefix    []string
	accounting       Accounting
	observeOperation func(string, time.Duration)
	observeRPC       func(string, time.Time, json.RawMessage, json.RawMessage, error)
	rpcComplete      bool
}

func New(api *localapi.Client, options Options) (*Client, error) {
	if api == nil {
		return nil, &core.Error{Code: "INVALID_REQUEST", Message: "memory client requires an API client"}
	}
	c := &Client{api: api.WithDiagnostics(options.Diagnostics), scope: options.Scope, observeOperation: options.ObserveOperation,
		observeRPC: options.ObserveRPC, rpcComplete: true,
		commandPrefix: slices.Clone(options.CommandPrefix),
		accounting: Accounting{OperationCalls: map[string]int{"search": 0, "pull": 0},
			PullCalls: map[string]int{}, ReceiptCredits: map[string]int{}, SearchReceipts: []string{}}}
	if options.Context != nil {
		c.context = *options.Context
	}
	return c, nil
}

func (c *Client) Search(ctx context.Context, query string, options SearchOptions) (indexview.View, error) {
	started := time.Now()
	defer func() {
		if c.observeOperation != nil {
			c.observeOperation("search", time.Since(started))
		}
	}()
	c.accounting.OperationCalls["search"]++
	room, timeout, requestID := searchRoom, callTimeout, ""
	if options.Room != nil {
		room = *options.Room
	}
	if options.Timeout != nil {
		timeout = *options.Timeout
	}
	if options.RequestID != nil {
		requestID = *options.RequestID
	}
	callCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	entities, err := fileEntities(options.Entities)
	if err != nil {
		c.observedRPC("search", started, nil, nil, err)
		return indexview.View{}, err
	}
	prepared, err := indexview.PrepareSearch(indexview.SearchInput{
		Scope: c.scope, RequireExplicitScope: true, Query: query, AvailableTokens: room,
		Context: &c.context, Entities: entities, Kinds: options.Kinds,
		Semantic: options.Semantic, Offset: options.Offset, RequestID: requestID,
	})
	if err != nil {
		c.observedRPC("search", started, nil, nil, err)
		return indexview.View{}, err
	}
	view, err := indexview.RetrieveCLI(callCtx, indexview.NewClientTransport(c.api, nil), prepared, c.commandPrefix)
	if err != nil {
		c.observedRPC("search", started, prepared.Request, nil, err)
		return indexview.View{}, classifyCallError(err)
	}
	c.observedRPC("search", started, prepared.Request, view, nil)
	c.recordReceipt(view.ReceiptID)
	switch view.Status {
	case "READY", "SCOPE_EMPTY", "DEGRADED_NO_EMBEDDINGS":
	default:
		return indexview.View{}, ErrSearchStatus
	}
	if view.Destination.Name != "hosted" {
		return indexview.View{}, ErrNotHosted
	}
	return view, nil
}

func fileEntities(names []string) ([]core.EntityRef, error) {
	var entities []core.EntityRef
	for _, name := range names {
		entities = append(entities, core.EntityRef{Kind: "file", Name: name})
		if _, err := core.NormalizeEntities(entities); err != nil {
			return nil, err
		}
	}
	return entities, nil
}

func (c *Client) Pull(ctx context.Context, request core.ExpandRequest, timeout time.Duration) (json.RawMessage, error) {
	receipt := request.ReceiptID
	if receipt != "" {
		if credits, known := c.accounting.ReceiptCredits[receipt]; known && credits == 0 {
			return nil, &core.Error{Code: "BUDGET_REFUSED", Message: "receipt has no remaining pull credits"}
		}
		c.accounting.PullCalls[receipt]++
	}
	c.accounting.OperationCalls["pull"]++
	started := time.Now()
	defer func() {
		if c.observeOperation != nil {
			c.observeOperation("pull", time.Since(started))
		}
	}()
	callCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	var raw json.RawMessage
	if err := c.api.Call(callCtx, "expand", request, &raw); err != nil {
		c.observedRPC("pull", started, request, nil, err)
		return nil, classifyCallError(err)
	}
	c.observedRPC("pull", started, request, raw, nil)
	var members map[string]json.RawMessage
	if err := json.Unmarshal(raw, &members); err != nil || members == nil {
		return nil, &responseError{reason: ErrNotConfirmed, cause: err}
	}
	if receipt != "" {
		c.updateCredits(receipt, members["credits_remaining"])
	}
	return raw, nil
}

func (c *Client) updateCredits(receipt string, raw json.RawMessage) {
	previous, known := c.accounting.ReceiptCredits[receipt]
	if !known {
		previous = 4
	}
	remaining, err := strconv.Atoi(string(bytes.TrimSpace(raw)))
	if err == nil && remaining >= 0 && remaining <= 4 {
		c.accounting.ReceiptCredits[receipt] = min(previous, remaining)
	} else {
		c.accounting.ReceiptCredits[receipt] = max(0, previous-1)
	}
}

func (c *Client) recordReceipt(receipt string) {
	id, err := uuid.Parse(receipt)
	if err != nil || id == uuid.Nil || id.String() != receipt ||
		len(c.accounting.SearchReceipts) == receiptLimit || slices.Contains(c.accounting.SearchReceipts, receipt) {
		return
	}
	c.accounting.SearchReceipts = append(c.accounting.SearchReceipts, receipt)
}

func (c *Client) Accounting() Accounting {
	return Accounting{OperationCalls: maps.Clone(c.accounting.OperationCalls),
		PullCalls: maps.Clone(c.accounting.PullCalls), ReceiptCredits: maps.Clone(c.accounting.ReceiptCredits),
		SearchReceipts: slices.Clone(c.accounting.SearchReceipts)}
}

func classifyCallError(err error) error {
	var service *core.Error
	if errors.As(err, &service) || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return err
	}
	var transport *url.Error
	var network net.Error
	if errors.As(err, &transport) || errors.As(err, &network) || errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
		return &responseError{reason: errTransport, cause: err}
	}
	return &responseError{reason: ErrNotConfirmed, cause: err}
}
