package main

import (
	"context"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

func TestClosureCLIBoundaries(t *testing.T) {
	for _, name := range []string{"event-closures-collection", "event-closure-close", "event-closure-reconcile"} {
		op, ok := operatorOperations[name]
		if !ok {
			t.Fatalf("missing operator command %s", name)
		}
		if _, exposed := localapi.LookupOperation(name); exposed {
			t.Fatalf("operator command %s in API", name)
		}
		if _, err := op.run(context.Background(), nil, strings.NewReader(`{"unexpected":true}`)); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%s strict decode: %v", name, err)
		}
	}
	for _, name := range []string{"closures", "close-replies"} {
		if !isEventCommand(name) || eventCommandOperations[name] == "" {
			t.Fatalf("missing event alias %s", name)
		}
	}
	for _, args := range [][]string{
		{"closures", "--to", "other"},
		{"closures", "extra"},
		{"close-replies", "--request-id", "00000000-0000-4000-8000-000000000001", "--reason", "no longer needed"},
		{"close-replies", "--reason", "no longer needed", "00000000-0000-4000-8000-000000000001"},
		{"close-replies", "--reason", "no longer needed", "00000000-0000-4000-8000-000000000001:0"},
		{"close-replies", "--reason", "no longer needed", "00000000-0000-4000-8000-000000000001:x"},
		{"close-replies", "--state", "reserved", "00000000-0000-4000-8000-000000000001:1"},
	} {
		_, err := eventCommand(context.Background(), args[0], args[1:], strings.NewReader(""), nil)
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%v accepted: %v", args, err)
		}
	}
}
