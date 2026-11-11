// Record public Go supervisor results for labelled disposable process fixtures.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/semantic"
)

type call struct {
	Error  string `json:"error"`
	Result any    `json:"result"`
}

func recorded(result any, err error) call {
	if err != nil {
		return call{Error: err.Error(), Result: result}
	}
	return call{Result: result}
}

func run() error {
	if len(os.Args) != 4 {
		return fmt.Errorf("expected case, launcher, events path")
	}
	name, launcher, events := os.Args[1], os.Args[2], os.Args[3]
	owner, cancel := context.WithCancel(context.Background())
	defer cancel()
	request := core.SemanticRankRequest{Query: "query"}
	var calls []call
	waitFor := func(text string) error {
		deadline := time.Now().Add(5 * time.Second)
		for time.Now().Before(deadline) {
			raw, err := os.ReadFile(events)
			if err == nil && strings.Contains(string(raw), text) {
				return nil
			}
			time.Sleep(time.Millisecond)
		}
		return fmt.Errorf("fixture event missing: %s", text)
	}
	switch name {
	case "rank-start-busy-retry", "rank-start-busy-persistent":
		file, err := os.OpenFile(launcher, os.O_WRONLY, 0)
		if err != nil {
			return err
		}
		defer file.Close()
		if name == "rank-start-busy-retry" {
			go func() { time.Sleep(40 * time.Millisecond); file.Close() }()
		}
		rank, err := semantic.Command(launcher)
		if err != nil {
			return err
		}
		result, err := rank(owner, request)
		calls = append(calls, recorded(result, err))
	case "stream-close-reopen":
		for i := 0; i < 2; i++ {
			stream, closeWorker, err := semantic.StreamCommand(owner, launcher)
			if err != nil {
				return err
			}
			result, err := stream(owner, request)
			calls = append(calls, recorded(result, err))
			closeWorker()
		}
	case "rank-output-boundary", "rank-output-overflow", "rank-early-exit", "rank-descendant", "rank-blocked-input", "rank-blocked-output":
		if name == "rank-blocked-input" {
			request.Query = strings.Repeat("x", 1024*1024)
		}
		rank, err := semantic.Command(launcher)
		if err != nil {
			return err
		}
		ctx, stop := context.WithTimeout(owner, 2*time.Second)
		defer stop()
		result, err := rank(ctx, request)
		calls = append(calls, recorded(result, err))
	case "embed-concurrency", "embed-busy", "embed-query-busy", "embed-owner-close", "embed-earlier-deadline", "embed-output-overflow", "embed-restart-identity", "embed-legacy-projection", "embed-projection-method", "embed-projection-hash", "embed-projection-byte-length", "embed-projection-mid-codepoint", "embed-projection-token-counts", "embed-projection-on-document":
		embed, err := semantic.EmbeddingCommand(owner, launcher)
		if err != nil {
			return err
		}
		defer embed.Close()
		if name == "embed-output-overflow" || name == "embed-legacy-projection" || strings.HasPrefix(name, "embed-projection-") {
			if name == "embed-projection-on-document" {
				result, err := embed.Document(owner, "body")
				calls = append(calls, recorded(result, err))
			} else {
				text := "query"
				if name == "embed-projection-mid-codepoint" {
					text = "é"
				}
				result, err := embed.Query(owner, text)
				calls = append(calls, recorded(result, err))
			}
		} else if name == "embed-restart-identity" {
			result, err := embed.Query(owner, "query")
			calls = append(calls, recorded(result, err))
			ctx, stop := context.WithTimeout(owner, 100*time.Millisecond)
			result, err = embed.Query(ctx, "block")
			stop()
			calls = append(calls, recorded(result, err))
			result, err = embed.Query(owner, "query")
			calls = append(calls, recorded(result, err))
		} else if name == "embed-earlier-deadline" {
			ctx, stop := context.WithTimeout(owner, time.Second)
			result, err := embed.Document(ctx, "body")
			stop()
			calls = append(calls, recorded(result, err))
		} else {
			done := make(chan call, 1)
			blocked := "document-blocked"
			if name == "embed-query-busy" {
				blocked = "query-blocked"
				go func() { result, err := embed.Query(owner, "block"); done <- recorded(result, err) }()
			} else {
				go func() { result, err := embed.Document(owner, "body"); done <- recorded(result, err) }()
			}
			if err := waitFor(blocked); err != nil {
				return err
			}
			if name == "embed-concurrency" {
				result, err := embed.Query(owner, "query")
				calls = append(calls, recorded(result, err))
			} else if name == "embed-busy" {
				result, err := embed.Document(owner, "body")
				calls = append(calls, recorded(result, err))
			} else if name == "embed-query-busy" {
				result, err := embed.Query(owner, "query")
				calls = append(calls, recorded(result, err))
			}
			embed.Close()
			calls = append(calls, <-done)
		}
	default:
		idle := 30 * time.Second
		if name == "stream-cache-omission" || name == "stream-cache-null" || name == "stream-new-child-identity" {
			idle = 500 * time.Millisecond
		}
		stream, closeWorker, err := semantic.StreamCommandWithIdleTimeout(owner, launcher, idle)
		if err != nil {
			return err
		}
		defer closeWorker()
		count := 1
		if name == "stream-identity-model" || name == "stream-identity-algorithm" || name == "stream-trailing-line" || name == "stream-new-child-identity" {
			count = 2
		}
		if name == "stream-cache-omission" || name == "stream-cache-null" || name == "stream-failure-restart" {
			count = 3
		}
		for i := 0; i < count; i++ {
			ctx := owner
			stop := func() {}
			if name == "stream-blocked-input" || name == "stream-blocked-output" || name == "stream-descendant" || name == "stream-owner-close" || name == "stream-busy" {
				ctx, stop = context.WithTimeout(owner, 2*time.Second)
			}
			if name == "stream-blocked-input" {
				request.Query = strings.Repeat("x", 1024*1024)
			}
			if name == "stream-busy" || name == "stream-owner-close" {
				done := make(chan call, 1)
				go func() { result, err := stream(ctx, request); done <- recorded(result, err) }()
				if err := waitFor("stream-blocked"); err != nil {
					return err
				}
				if name == "stream-busy" {
					result, err := stream(ctx, request)
					calls = append(calls, recorded(result, err))
				} else {
					cancel()
				}
				calls = append(calls, <-done)
			} else {
				result, err := stream(ctx, request)
				calls = append(calls, recorded(result, err))
			}
			stop()
			if i == 1 && count == 3 {
				time.Sleep(600 * time.Millisecond)
			}
			if i == 0 && name == "stream-new-child-identity" {
				time.Sleep(600 * time.Millisecond)
			}
		}
	}
	return json.NewEncoder(os.Stdout).Encode(struct {
		Case  string `json:"case"`
		Calls []call `json:"calls"`
	}{name, calls})
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
