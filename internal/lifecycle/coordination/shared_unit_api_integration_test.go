package coordination

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// TestR06RealAPIConsumer is a private process entry point for the disposable
// PostgreSQL/API fixture. It uses the public consumer methods and the concrete
// file host. The runner records real command responses; it supplies no views.
func TestR06RealAPIConsumer(t *testing.T) {
	path := os.Getenv("CAIRN_R06_API_PLAN")
	if path == "" {
		return
	}
	var plan struct {
		Current        WatchBindings
		Retained       string
		Next           []install.File
		Result         string
		BeforeExchange []string
		BeforeInput    string
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &plan); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	type call struct {
		Argv  []string
		Input string
		journal.Result
		Error string
	}
	var calls []call
	allowed := map[string]bool{"wake-attempts": true, "event-watch": true, "session-inbox-control": true, "session-inbox-attempt-status": true, "session-inbox-poll-status": true}
	runner := journal.RunnerFunc(func(ctx context.Context, argv []string, input string) (journal.Result, error) {
		if len(argv) == 0 || !allowed[argv[len(argv)-1]] {
			return journal.Result{}, fmt.Errorf("inspector attempted non-read command: %v", argv)
		}
		result, err := (journal.CommandRunner{}).Run(ctx, argv, input)
		entry := call{Argv: argv, Input: input, Result: result}
		if err != nil {
			entry.Error = err.Error()
		}
		calls = append(calls, entry)
		return result, err
	})
	profile := stage2ServicesProfile()
	cli := &CLI{Profile: profile, Host: NewFileHost(FileHostOptions{Profile: profile, Runner: runner}), ProcRoot: "/proc"}
	receipt, runErr := cli.CaptureWatchDrain(ctx, plan.Current, []*value.Value{})
	var exchange WatchBindingsExchange
	var facts map[string]string
	var actorOutput string
	if runErr == nil {
		facts = receipt.Facts()
		if len(plan.BeforeExchange) != 0 {
			actor := exec.CommandContext(ctx, plan.BeforeExchange[0], plan.BeforeExchange[1:]...)
			actor.Stdin = strings.NewReader(plan.BeforeInput)
			output, err := actor.CombinedOutput()
			actorOutput = string(output)
			if err != nil {
				t.Fatalf("fixture actor: %s: %v", output, err)
			}
		}
		exchange, runErr = ExchangeWatchBindings(ctx, receipt, plan.Retained, plan.Next)
	}
	out := struct {
		Receipt     bool
		Facts       map[string]string
		Exchange    WatchBindingsExchange
		Error       string
		Calls       []call
		ActorOutput string
	}{Receipt: receipt != nil, Facts: facts, Exchange: exchange, Calls: calls, ActorOutput: actorOutput}
	if runErr != nil {
		out.Error = runErr.Error()
	}
	raw, err = json.MarshalIndent(out, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(plan.Result, raw, 0600); err != nil {
		t.Fatal(err)
	}
	// Refusals are asserted by the parent fixture with exact API evidence.
}
