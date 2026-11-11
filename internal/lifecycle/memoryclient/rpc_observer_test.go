package memoryclient_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"reflect"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
)

func TestRPCObserverSearchBeforePolicy(t *testing.T) {
	for _, destination := range []string{"hosted", "local"} {
		for _, status := range []string{"READY", "REFUSED"} {
			t.Run(destination+"-"+status, func(t *testing.T) {
				reply := indexReply(entry(recordID, "private fixture preview"))
				reply.Package.Semantic.Status, reply.Package.Semantic.Destination.Name = status, destination
				api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
					if !probe(t, w, call) {
						success(t, w, reply)
					}
				})
				var c *memoryclient.Client
				var request, response json.RawMessage
				calls := 0
				c = client(t, api, memoryclient.Options{Scope: scope, ObserveRPC: func(op string, at time.Time, p, r json.RawMessage, err error) {
					calls++
					if op != "search" || at.IsZero() || err != nil || len(c.Accounting().SearchReceipts) != 0 {
						t.Fatal("returned search must be observed before local policy/accounting")
					}
					request, response = bytes.Clone(p), bytes.Clone(r)
					// Poison owned snapshots; neither the returned view nor accounting may change.
					for i := range p {
						p[i] = 'x'
					}
					for i := range r {
						r[i] = 'x'
					}
				}})
				view, err := c.Search(context.Background(), "actual query", memoryclient.SearchOptions{RequestID: &requestIDValue})
				if status == "REFUSED" && !errors.Is(err, memoryclient.ErrSearchStatus) || status != "REFUSED" && destination == "local" && !errors.Is(err, memoryclient.ErrNotHosted) {
					t.Fatalf("observer changed policy: %v", err)
				}
				if status == "READY" && destination == "hosted" && (err != nil || view.Index[0].Summary != "private fixture preview") {
					t.Fatalf("mutated live search: %v", err)
				}
				if calls != 1 || !c.RPCObservationComplete() || c.Accounting().SearchReceipts[0] != receiptID {
					t.Fatal("missing observation or accounting")
				}
				wire := decode(t, seen.snapshot()[1].body).(map[string]any)
				delete(wire, "_cairn_protocol")
				got := decode(t, request).(map[string]any)
				for k, v := range got {
					if !reflect.DeepEqual(wire[k], v) {
						t.Fatalf("observer did not receive actual prepared field %s", k)
					}
				}
				if !bytes.Contains(response, []byte("private fixture preview")) {
					t.Fatal("final presentation was not observed")
				}
			})
		}
	}
}

func TestRPCObserverPullSnapshotsAndKnownZero(t *testing.T) {
	for _, span := range []*core.ByteSpanRequest{nil, {Offset: 7, Length: 11}} {
		t.Run(map[bool]string{true: "span", false: "whole"}[span != nil], func(t *testing.T) {
			data := json.RawMessage(`{"credits_remaining":0,"selection":{"record":{"body":"actual returned body"}}}`)
			api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) { success(t, w, data) })
			request := core.ExpandRequest{RequestID: requestID, ReceiptID: receiptID, Handle: handleID, Span: span}
			var c *memoryclient.Client
			calls := 0
			c = client(t, api, memoryclient.Options{ObserveRPC: func(op string, at time.Time, p, r json.RawMessage, err error) {
				calls++
				if op != "pull" || at.IsZero() || err != nil || len(c.Accounting().ReceiptCredits) != 0 {
					t.Fatal("pull callback did not precede validation/accounting")
				}
				equalJSON(t, p, request)
				if !bytes.Equal(r, data) {
					t.Fatalf("raw SDK data changed: %s", r)
				}
				for i := range p {
					p[i] = 'x'
				}
				for i := range r {
					r[i] = 'x'
				}
			}})
			got, err := c.Pull(context.Background(), request, time.Second)
			if err != nil || !bytes.Equal(got, data) {
				t.Fatalf("snapshot mutated SDK data: %v", err)
			}
			before := c.Accounting()
			if _, err := c.Pull(context.Background(), request, time.Second); core.Code(err) != "BUDGET_REFUSED" {
				t.Fatal(err)
			}
			if calls != 1 || !c.RPCObservationComplete() || !reflect.DeepEqual(c.Accounting(), before) || len(seen.snapshot()) != 1 {
				t.Fatal("known-zero pull initiated observation or RPC")
			}
		})
	}
}

func TestRPCObserverRejectedDataAndTransport(t *testing.T) {
	for _, mode := range []string{"array", "transport", "service"} {
		t.Run(mode, func(t *testing.T) {
			api, seen := fixture(t, func(w http.ResponseWriter, _ *http.Request, _ requestCapture) {
				switch mode {
				case "array":
					success(t, w, []any{})
				case "service":
					w.Write([]byte(`{"schema":"cairn.response/1","ok":false,"status":"AUTHORITY_DENIED","message":"fixture refusal"}`))
				case "transport":
					conn, _, err := w.(http.Hijacker).Hijack()
					if err != nil {
						t.Error(err)
						return
					}
					conn.Close()
				}
			})
			calls := 0
			var observed error
			c := client(t, api, memoryclient.Options{ObserveRPC: func(op string, at time.Time, p, r json.RawMessage, err error) {
				calls++
				observed = err
				if mode == "array" && (err != nil || string(r) != "[]") {
					t.Fatal("returned invalid data was hidden by later validation")
				}
				if mode != "array" && (err == nil || r != nil) {
					t.Fatal("failed RPC did not retain the actual error")
				}
			}})
			_, err := c.Pull(context.Background(), core.ExpandRequest{ReceiptID: receiptID}, time.Second)
			if mode == "array" && !errors.Is(err, memoryclient.ErrNotConfirmed) {
				t.Fatal(err)
			}
			if mode == "service" && (core.Code(err) != "AUTHORITY_DENIED" || core.Code(observed) != "AUTHORITY_DENIED") {
				t.Fatal(err)
			}
			var transport *url.Error
			if mode == "transport" && (!errors.As(err, &transport) || !errors.As(observed, &transport)) {
				t.Fatal("transport cause lost")
			}
			if calls != 1 || len(seen.snapshot()) != 1 || len(c.Accounting().ReceiptCredits) != 0 {
				t.Fatal("failed/unconfirmed pull retried or changed credits")
			}
		})
	}
}

func TestRPCObserverNilAndLossPreserveAccounting(t *testing.T) {
	var previous memoryclient.Accounting
	for _, mode := range []string{"nil", "observed", "panic"} {
		t.Run(mode, func(t *testing.T) {
			api, _ := fixture(t, func(w http.ResponseWriter, _ *http.Request, call requestCapture) {
				if probe(t, w, call) {
					return
				}
				if call.operation == "index" {
					success(t, w, indexReply())
					return
				}
				success(t, w, map[string]int{"credits_remaining": 2})
			})
			options := memoryclient.Options{Scope: scope}
			if mode != "nil" {
				options.ObserveRPC = func(string, time.Time, json.RawMessage, json.RawMessage, error) {
					if mode == "panic" {
						panic("observer lost")
					}
				}
			}
			c := client(t, api, options)
			if _, err := c.Search(context.Background(), "q", memoryclient.SearchOptions{RequestID: &requestIDValue}); err != nil {
				t.Fatal(err)
			}
			if _, err := c.Pull(context.Background(), core.ExpandRequest{ReceiptID: receiptID}, time.Second); err != nil {
				t.Fatal(err)
			}
			if mode == "nil" {
				previous = c.Accounting()
			} else if !reflect.DeepEqual(previous, c.Accounting()) {
				t.Fatal("observer changed accounting")
			}
			if c.RPCObservationComplete() != (mode == "observed") {
				t.Fatal("lost/absent observation was presented as complete")
			}
		})
	}
}
