package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

// operatorDestination is the trusted local operator channel's destination.
var operatorDestination = core.Destination{Name: "local", AllowLocal: true}

// operatorOperation is one operator command that reads one JSON request from
// stdin. A shared entry names the localapi catalog operation that implements
// it, so its body limit, decoding and invocation are the catalog's and cannot
// drift. An operator-only entry binds its own store method with the same
// shape and stays in cmd/cairn, so authority mutations remain structurally
// absent from localapi. The operator channel itself is established by
// core.Open, never by anything in a request.
type operatorOperation struct {
	Name    string
	Catalog string
	Limit   int64
	NoInput bool
	Invoke  func(ctx context.Context, store *core.Store, raw json.RawMessage) (any, error)
}

func (op operatorOperation) limit() int64 {
	if op.Catalog != "" {
		return localapi.RequestBodyLimit(op.Catalog)
	}
	if op.Limit > 0 {
		return op.Limit
	}
	return localapi.DefaultRequestBodyLimit
}

func (op operatorOperation) run(ctx context.Context, store *core.Store, input io.Reader) (any, error) {
	if op.NoInput {
		return op.Invoke(ctx, store, nil)
	}
	raw, err := readBoundedRequest(input, op.limit())
	if err != nil {
		return nil, invalid(err.Error())
	}
	return op.Invoke(ctx, store, raw)
}

// readBoundedRequest reads at most limit bytes of one request. Decoding is the
// catalog's (localapi.DecodeRequest), so unknown fields, invalid Unicode and
// trailing tokens are refused the same way on every surface.
func readBoundedRequest(input io.Reader, limit int64) ([]byte, error) {
	body, err := io.ReadAll(io.LimitReader(input, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(body)) > limit {
		if limit == localapi.DefaultRequestBodyLimit {
			return nil, errors.New("request exceeds 128 KiB")
		}
		return nil, errors.New("request exceeds size limit")
	}
	return body, nil
}

// invoke decodes one bounded JSON request from input and calls call with it.
func invoke[Request, Response any](ctx context.Context, input io.Reader, call func(context.Context, Request) (Response, error)) (Response, error) {
	var zero Response
	raw, err := readBoundedRequest(input, localapi.DefaultRequestBodyLimit)
	if err != nil {
		return zero, invalid(err.Error())
	}
	var req Request
	if err := localapi.DecodeRequest(raw, &req); err != nil {
		return zero, err
	}
	return call(ctx, req)
}

func sharedOperation(name, catalog string) operatorOperation {
	spec, ok := localapi.LookupOperation(catalog)
	if !ok || !spec.Invocable() {
		panic("operator command " + name + " references a localapi operation that cannot run outside HTTP: " + catalog)
	}
	return operatorOperation{Name: name, Catalog: catalog, Invoke: func(ctx context.Context, store *core.Store, raw json.RawMessage) (any, error) {
		return localapi.Invoke(ctx, spec, store, operatorDestination, raw)
	}}
}

func decodedOperation[Q, R any](name string, limit int64, call func(context.Context, *core.Store, Q) (R, error)) operatorOperation {
	return operatorOperation{Name: name, Limit: limit, Invoke: func(ctx context.Context, store *core.Store, raw json.RawMessage) (any, error) {
		var req Q
		if err := localapi.DecodeRequest(raw, &req); err != nil {
			return nil, err
		}
		return call(ctx, store, req)
	}}
}

func storeOperation[Q, R any](name string, call func(*core.Store, context.Context, Q) (R, error)) operatorOperation {
	return decodedOperation(name, 0, func(ctx context.Context, store *core.Store, req Q) (R, error) { return call(store, ctx, req) })
}

func destinationOperation[Q, R any](name string, call func(*core.Store, context.Context, Q, core.Destination) (R, error)) operatorOperation {
	return decodedOperation(name, 0, func(ctx context.Context, store *core.Store, req Q) (R, error) {
		return call(store, ctx, req, operatorDestination)
	})
}

func noInputOperation(name string, call func(context.Context, *core.Store) (any, error)) operatorOperation {
	return operatorOperation{Name: name, NoInput: true, Invoke: func(ctx context.Context, store *core.Store, _ json.RawMessage) (any, error) { return call(ctx, store) }}
}

// readerOperation keeps commands that decode their own extended request shape.
func readerOperation(name string, call func(context.Context, *core.Store, io.Reader) (any, error)) operatorOperation {
	return operatorOperation{Name: name, Invoke: func(ctx context.Context, store *core.Store, raw json.RawMessage) (any, error) {
		return call(ctx, store, bytes.NewReader(raw))
	}}
}

var operatorOperations = buildOperatorOperations()

func buildOperatorOperations() map[string]operatorOperation {
	historical := func(p core.Package, err error) (any, error) {
		return struct {
			Historical bool         `json:"historical"`
			Package    core.Package `json:"package"`
		}{true, p}, err
	}
	entries := []operatorOperation{
		// Shared with the agent API: the catalog owns limit, decoding and invocation.
		sharedOperation("create", "create"),
		sharedOperation("edit", "edit"),
		sharedOperation("revise", "revise"),
		sharedOperation("replace", "replace"),
		sharedOperation("set-sections", "set-sections"),
		sharedOperation("append", "append"),
		sharedOperation("cite", "cite"),
		sharedOperation("delete", "delete"),
		sharedOperation("check-evidence", "check-evidence"),
		sharedOperation("capture-evidence", "evidence"),
		sharedOperation("usage", "usage"),
		sharedOperation("history", "history"),
		sharedOperation("expand-evidence", "expand-evidence"),

		// Operator-only authority, policy and proposal mutations. localapi has no
		// route for these; the operator channel from core.Open is the authority.
		storeOperation("bootstrap", (*core.Store).Bootstrap),
		storeOperation("grant", (*core.Store).Grant),
		storeOperation("revoke-grant", (*core.Store).RevokeGrant),
		storeOperation("demote", (*core.Store).Demote),
		storeOperation("promote", (*core.Store).Promote),
		storeOperation("issue", (*core.Store).Issue),
		storeOperation("correct", (*core.Store).Correct),
		// The API refuses a grant-bearing supersession; the operator may supersede with a grant.
		storeOperation("supersede", (*core.Store).Supersede),
		storeOperation("authorize-scope", (*core.Store).AuthorizeScope),
		storeOperation("policy-revise", (*core.Store).RevisePolicy),
		storeOperation("forget", (*core.Store).Forget),
		storeOperation("retract", (*core.Store).Retract),
		storeOperation("dispute", (*core.Store).Dispute),
		storeOperation("resolve", (*core.Store).Resolve),
		storeOperation("generate-proposals", (*core.Store).GenerateProposals),
		storeOperation("proposal-history", (*core.Store).ProposalHistory),
		storeOperation("review-proposal", (*core.Store).ReviewProposal),

		// Operator reads that differ from their API namesakes: no authenticated
		// receipt binding (assess-run, recompile) and no API reader guard
		// (compile, index, expand); the local destination is explicit.
		storeOperation("assess-run", (*core.Store).AssessRun),
		decodedOperation("recompile", 0, func(ctx context.Context, store *core.Store, req core.RecompileRequest) (any, error) {
			return historical(store.Recompile(ctx, req))
		}),
		destinationOperation("compile", (*core.Store).Compile),
		destinationOperation("index", (*core.Store).Index),
		destinationOperation("expand", (*core.Store).Expand),

		// Operator recovery, coordination and scheduling.
		storeOperation("worker-pool-configure", (*core.Store).ConfigureWorkerPool),
		readerOperation("coordination-review", coordinationReview),
		readerOperation("delivery-alerts", deliveryAlerts),
		storeOperation("event-reissue", (*core.Store).ReissueEvent),
		storeOperation("native-hold-release", (*core.Store).ReleaseNativeHold),
		storeOperation("stopped-inbox-expire", (*core.Store).ExpireStoppedInboxes),
		storeOperation("session-successor-authorize", (*core.Store).AuthorizeSessionSuccessor),
		storeOperation("session-successor-revoke", (*core.Store).RevokeSessionSuccessor),
		storeOperation("work-cancel", (*core.Store).CancelWork),
		storeOperation("request-control-sweep", (*core.Store).SweepRequestControls),
		storeOperation("event-schedule", (*core.Store).ScheduleEvent),
		storeOperation("schedule-list", (*core.Store).Schedules),
		storeOperation("schedule-cancel", (*core.Store).CancelSchedule),
		storeOperation("schedule-tick", (*core.Store).TickSchedules),

		// Restore, audit and migration administration.
		storeOperation("begin-restore", (*core.Store).BeginRestore),
		noInputOperation("restore-status", func(ctx context.Context, store *core.Store) (any, error) { return store.RestoreStatus(ctx) }),
		storeOperation("rebuild-restore", (*core.Store).RebuildRestore),
		decodedOperation("verify-restore", 20*1024*1024, func(ctx context.Context, store *core.Store, req core.VerifyRestoreRequest) (core.RestoreVerification, error) {
			result, err := store.VerifyRestore(ctx, req)
			if err == nil && !result.Ready {
				err = &core.Error{Code: "RESTORE_INCOMPLETE", Message: "restore checks failed; service remains paused"}
			}
			return result, err
		}),
		decodedOperation("resume-restore", 20*1024*1024, func(ctx context.Context, store *core.Store, req core.ResumeRestoreRequest) (core.RestoreResume, error) {
			return store.ResumeRestore(ctx, req)
		}),
		storeOperation("fence-restore", (*core.Store).FenceRestore),
		storeOperation("invalidate-handles", (*core.Store).InvalidateHandles),
		decodedOperation("verify-checkpoint", 0, func(ctx context.Context, store *core.Store, req core.VerifyCheckpointRequest) (core.CheckpointVerification, error) {
			result, err := store.VerifyCheckpoint(ctx, req)
			if err == nil && !result.Valid {
				err = &core.Error{Code: "INTEGRITY_FAILURE", Message: "audit members differ from expected restore set"}
			}
			return result, err
		}),
		noInputOperation("migrate", func(ctx context.Context, store *core.Store) (any, error) { return nil, store.Migrate(ctx) }),
		noInputOperation("grants", func(ctx context.Context, store *core.Store) (any, error) { return store.Grants(ctx) }),
	}
	table := make(map[string]operatorOperation, len(entries))
	for _, op := range entries {
		if _, dup := table[op.Name]; dup {
			panic("duplicate operator command: " + op.Name)
		}
		table[op.Name] = op
	}
	return table
}
