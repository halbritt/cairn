package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"reflect"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

type unreadHelpInput struct{}

func (unreadHelpInput) Read([]byte) (int, error) { panic("help read stdin") }

func TestAgentHelpWorksWithoutConnectionOrHome(t *testing.T) {
	t.Setenv("HOME", "")
	t.Setenv("CAIRN_HOME", "")
	for _, args := range [][]string{
		{"agent", "--help"},
		{"agent", "replace", "--help"},
		{"agent", "--socket", "/absent/api.sock", "--token-file", "/absent/token", "history", "-h"},
	} {
		got, err := run(context.Background(), args, unreadHelpInput{})
		if err != nil || !strings.Contains(fmt.Sprint(got), "Usage: cairn agent") {
			t.Fatalf("offline help %v: %v %v", args, got, err)
		}
	}
}

func TestAgentFlagOperationHelpWorksWithoutConnectionOrHome(t *testing.T) {
	t.Setenv("HOME", "")
	t.Setenv("CAIRN_HOME", "")
	for _, operation := range []struct {
		name    string
		needles []string
	}{
		{"search", []string{"--task TASK", "advisory-conflicts", "entity-symbol"}},
		{"remember", []string{"--stdin", "shareable", "pins"}},
		{"pull", []string{"RECEIPT_UUID HANDLE_UUID", "length", "request-id"}},
		{"pull-evidence", []string{"EVIDENCE_UUID EXPECTED_SHA256", "length", "request-id"}},
	} {
		for _, helpFlag := range []string{"--help", "-h"} {
			got, err := run(context.Background(), []string{"agent", operation.name, helpFlag}, unreadHelpInput{})
			text := fmt.Sprint(got)
			if err != nil {
				t.Fatalf("offline %s help: %v", operation.name, err)
			}
			for _, needle := range operation.needles {
				if !strings.Contains(text, needle) {
					t.Errorf("offline %s help omitted %q:\n%s", operation.name, needle, text)
				}
			}
		}
	}
}

func TestAgentHelpDoesNotHideMalformedArguments(t *testing.T) {
	for _, args := range [][]string{
		{"agent", "--unknown", "--help"},
		{"agent", "unknown-operation", "--help"},
		{"agent", "replace", "--help", "extra"},
		{"agent", "search", "--help", "extra"},
		{"agent", "remember", "-h", "extra"},
		{"agent", "pull", "--help", "extra"},
		{"agent", "pull-evidence", "-h", "extra"},
	} {
		if _, err := run(context.Background(), args, strings.NewReader("")); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("invalid invocation %v became help: %v", args, err)
		}
	}
}

func TestAgentSubcommandAndHelpCatalogSynchronizedWithLocalAPI(t *testing.T) {
	t.Setenv("HOME", "")
	t.Setenv("CAIRN_HOME", "")
	for _, op := range localapi.Operations() {
		sub, ok := lookupAgentSubcommand(op.Name)
		if !ok {
			t.Errorf("localapi operation %q is missing from agentSubcommands", op.Name)
			continue
		}
		if sub.Operation != op.Name {
			t.Errorf("localapi operation %q mapped to %q in agentSubcommands", op.Name, sub.Operation)
		}
	}
	for name, sub := range agentSubcommands {
		if _, ok := localapi.LookupOperation(sub.Operation); !ok {
			t.Errorf("agent subcommand %q references unknown localapi operation %q", name, sub.Operation)
		}
		if _, isEvent := eventCommandOperations[name]; isEvent != isEventCommand(name) {
			t.Errorf("eventCommandOperations[%q] = %v, isEventCommand = %v", name, isEvent, isEventCommand(name))
		}
		if sub.Help != nil {
			got, err := run(context.Background(), []string{"agent", name, "--help"}, unreadHelpInput{})
			if err != nil || strings.TrimSpace(fmt.Sprint(got)) == "" {
				t.Errorf("offline help for %q failed: %v", name, err)
			}
		}
	}
	for name, topic := range agentJSONHelp {
		opSpec, ok := localapi.LookupOperation(name)
		if !ok {
			t.Errorf("agentJSONHelp entry %q is not a registered localapi operation", name)
			continue
		}
		if strings.TrimSpace(topic.Detail) == "" || strings.TrimSpace(topic.Example) == "" {
			t.Errorf("agentJSONHelp entry %q has empty detail or example", name)
		}
		target := reflect.New(opSpec.RequestType).Interface()
		dec := json.NewDecoder(strings.NewReader(topic.Example))
		dec.DisallowUnknownFields()
		if err := dec.Decode(target); err != nil {
			t.Errorf("agentJSONHelp[%q] example %s does not decode into %s: %v", name, topic.Example, opSpec.RequestType, err)
		}
		var extra any
		if err := dec.Decode(&extra); err != io.EOF {
			t.Errorf("agentJSONHelp[%q] example has trailing tokens: %v", name, err)
		}
	}
}
