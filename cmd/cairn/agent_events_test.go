package main

import (
	"context"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestEventHelpAndInvalidCommandsNeedNoCredentials(t *testing.T) {
	ctx := context.Background()
	for _, command := range []string{"publish", "inbox", "ack", "complete", "retry", "renew", "subscribe", "unsubscribe", "subscriptions", "events", "event-status", "event-stats"} {
		for _, args := range [][]string{{command, "--help"}, {"agent", command, "--help"}} {
			out, err := run(ctx, args, strings.NewReader(""))
			if err != nil {
				t.Fatalf("%v: %v", args, err)
			}
			if help, ok := out.(commandHelp); !ok || !strings.Contains(string(help), "authenticated") {
				t.Fatalf("%v: %v", args, out)
			}
		}
	}
	for _, args := range [][]string{{"publish", "--kind", "request", "--version", "1", "id"}, {"inbox", "next", "--to", "someone"}, {"ack", "--request-id", "id"}, {"complete", "--request-id", "id", "--lease", "id", "delivery"}, {"events", "--after", "-1", "unexpected"}} {
		_, err := run(ctx, args, strings.NewReader(""))
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%v: %v", args, err)
		}
	}
}
