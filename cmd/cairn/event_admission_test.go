package main

import (
	"context"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

func TestEventAdmissionCLIBoundaries(t *testing.T) {
	for _, name := range []string{"event-admission-configure", "event-admission-policy", "event-pressure-collection", "event-fanout-collection"} {
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
	for _, args := range [][]string{{"event-admission-preview"}, {"event-admission-preview", "--to", "one", "--topic", "all"}, {"event-pressure", "--to", "other"}, {"event-fanout", "--agent", "other"}} {
		_, err := eventCommand(context.Background(), args[0], args[1:], strings.NewReader(""), nil)
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("%v accepted: %v", args, err)
		}
	}
	for _, name := range []string{"event-pressure", "event-fanout", "event-admission-preview"} {
		if !isEventCommand(name) || eventCommandOperations[name] == "" {
			t.Fatalf("missing event alias %s", name)
		}
	}
}
