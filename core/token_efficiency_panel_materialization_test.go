package core

import (
	"context"
	"encoding/json"
	"os"
	"testing"

	"github.com/google/uuid"
)

// This test materializes three fixture cases through native store APIs in the
// disposable integration database. It does not execute the other cases or run
// a provider, actor, grader, or trial arm.
func TestTokenEfficiencyPanelNativeMaterialization(t *testing.T) {
	ctx := context.Background()
	var panel struct {
		Sources map[string]struct {
			Body           string   `json:"body"`
			Kind           string   `json:"kind"`
			RecordClass    string   `json:"record_class"`
			Sensitivity    string   `json:"sensitivity"`
			ClaimType      string   `json:"claim_type"`
			Operation      string   `json:"operation"`
			RecordFamily   string   `json:"record_family"`
			LogicalVersion int      `json:"logical_version"`
			Scope          Scope    `json:"scope"`
			Supersedes     []string `json:"supersedes"`
			Authority      struct {
				Grant           string `json:"grant"`
				Mandatory       bool   `json:"mandatory"`
				RequiresRuntime bool   `json:"requires_runtime"`
				Category        string `json:"category"`
				PolicyKey       string `json:"policy_key"`
			} `json:"authority"`
		} `json:"sources"`
		Cases []struct {
			ID    string `json:"id"`
			Setup struct {
				Sources []string `json:"sources"`
				Events  []struct {
					ID             string `json:"id"`
					Kind           string `json:"kind"`
					Body           string `json:"body"`
					IdempotencyKey string `json:"idempotency_key"`
				} `json:"events"`
				Transitions []struct {
					Action string `json:"action"`
					Target string `json:"target"`
				} `json:"transitions"`
			} `json:"setup"`
		} `json:"cases"`
		EventSourceDefaults struct {
			Kind        string `json:"kind"`
			RecordClass string `json:"record_class"`
			Operation   string `json:"operation"`
			Sensitivity string `json:"sensitivity"`
			ClaimType   string `json:"claim_type"`
			Scope       Scope  `json:"scope"`
		} `json:"event_source_defaults"`
	}
	raw, err := os.ReadFile("../trials/token-efficiency/FIXTURE.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &panel); err != nil {
		t.Fatal(err)
	}
	caseByID := map[string]int{}
	for index, row := range panel.Cases {
		caseByID[row.ID] = index
	}
	for _, name := range []string{"corrected_note", "required_conflict_group", "duplicate_send"} {
		if _, ok := caseByID[name]; !ok {
			t.Fatalf("missing fixture case %s", name)
		}
	}
	repo := panel.EventSourceDefaults.Scope.Repo
	if repo != "fixture:cairn-token-efficiency" || panel.EventSourceDefaults.Kind != "note" ||
		panel.EventSourceDefaults.RecordClass != "A" || panel.EventSourceDefaults.Operation != "create" ||
		panel.EventSourceDefaults.Sensitivity != "shareable" || panel.EventSourceDefaults.ClaimType != "self" {
		t.Fatal("event source defaults no longer match native ordinary source contract")
	}
	operator, root := testOperator(t)
	writer := testStore(t, Channel{Principal: "agent:panel-writer", Repo: repo})
	receiver := testStore(t, Channel{Principal: "agent:panel-receiver", Repo: repo})
	dest := Destination{Name: "hosted", AllowLocal: false}

	corrected := panel.Cases[caseByID["corrected_note"]].Setup.Sources
	if len(corrected) != 2 || corrected[0] != "old_export" || corrected[1] != "export_correction" {
		t.Fatalf("corrected source order changed: %v", corrected)
	}
	old, current := panel.Sources[corrected[0]], panel.Sources[corrected[1]]
	if old.Operation != "create" || current.Operation != "edit" ||
		old.RecordClass != "A" || current.RecordClass != "A" ||
		old.RecordFamily != current.RecordFamily || old.LogicalVersion != 1 || current.LogicalVersion != 2 ||
		len(current.Supersedes) != 1 || current.Supersedes[0] != corrected[0] {
		t.Fatal("corrected source lineage changed")
	}
	toDraft := func(sourceID string) Draft {
		source := panel.Sources[sourceID]
		if source.Scope.Repo != repo || source.Scope.TaskID != "*" || source.Scope.RunID != "*" ||
			source.Sensitivity != "shareable" || source.ClaimType != "self" {
			t.Fatalf("%s: scope or source contract changed", sourceID)
		}
		return Draft{Kind: source.Kind, Body: source.Body, Scope: source.Scope,
			ClaimType: source.ClaimType, Sensitivity: source.Sensitivity}
	}
	first, err := writer.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: toDraft(corrected[0])})
	if err != nil {
		t.Fatal(err)
	}
	second, err := writer.Edit(ctx, EditRequest{RequestID: uuid.NewString(), RecordID: first.RecordID,
		ExpectedVersion: first.Version, Draft: toDraft(corrected[1])})
	if err != nil || first.Version != 1 || second.Version != 2 || second.RecordID != first.RecordID ||
		second.Body != current.Body || second.ObservedWriter != writer.channel.Principal {
		t.Fatalf("native correction/version identity: first=%+v second=%+v err=%v", first, second, err)
	}
	history, err := writer.History(ctx, RecordHistoryRequest{RecordID: first.RecordID, Version: 1}, dest)
	if err != nil || len(history.Versions) != 1 || history.Versions[0].Body == nil ||
		*history.Versions[0].Body != old.Body {
		t.Fatalf("old correction body not retained: %+v %v", history, err)
	}

	conflict := panel.Cases[caseByID["required_conflict_group"]].Setup.Sources
	if len(conflict) != 2 || conflict[0] != "conflict_24" || conflict[1] != "conflict_48" {
		t.Fatalf("required conflict source order changed: %v", conflict)
	}
	for _, sourceID := range conflict {
		source := panel.Sources[sourceID]
		if source.Kind != "instruction" || source.RecordClass != "C" || source.Operation != "issue" ||
			source.Authority.Grant != "disposable_operator_root" || !source.Authority.Mandatory ||
			source.Authority.RequiresRuntime || source.Authority.Category != "workflow" ||
			source.Authority.PolicyKey != "synthetic-evacuation-threshold" {
			t.Fatalf("%s: required authority contract changed", sourceID)
		}
		issued, issueErr := operator.Issue(ctx, IssueRequest{RequestID: uuid.NewString(),
			Draft: toDraft(sourceID), GrantID: root.ID, Mandatory: source.Authority.Mandatory,
			RequiresRuntime: source.Authority.RequiresRuntime, Category: source.Authority.Category,
			PolicyKey: source.Authority.PolicyKey, Reason: "Materialize synthetic paired panel conflict"})
		if issueErr != nil || issued.Class != "C" || issued.Version != 1 || issued.Body != source.Body ||
			issued.ObservedWriter != operator.channel.Principal {
			t.Fatalf("%s: issued source: %+v %v", sourceID, issued, issueErr)
		}
	}
	_, err = operator.Compile(ctx, CompileRequest{RequestID: uuid.NewString(),
		Scope: Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context",
		AvailableTokens: 64000}, dest)
	requireCode(t, err, "OPEN_CONFLICT")

	duplicate := panel.Cases[caseByID["duplicate_send"]].Setup
	if len(duplicate.Events) != 1 || len(duplicate.Transitions) != 1 ||
		duplicate.Transitions[0].Action != "duplicate_publish" ||
		duplicate.Transitions[0].Target != duplicate.Events[0].ID ||
		duplicate.Events[0].Kind != "request" || duplicate.Events[0].IdempotencyKey != "ck8" {
		t.Fatal("duplicate publish contract changed")
	}
	eventSource := Draft{Kind: panel.EventSourceDefaults.Kind, Body: duplicate.Events[0].Body,
		Scope: panel.EventSourceDefaults.Scope, Sensitivity: panel.EventSourceDefaults.Sensitivity,
		ClaimType: panel.EventSourceDefaults.ClaimType}
	stored, err := writer.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: eventSource})
	if err != nil {
		t.Fatal(err)
	}
	runNamespace := uuid.New()
	requestID := uuid.NewSHA1(runNamespace,
		[]byte("duplicate_send/"+duplicate.Events[0].IdempotencyKey)).String()
	request := PublishEventRequest{RequestID: requestID, Kind: duplicate.Events[0].Kind,
		Ref:         RecordVersionRef{RecordID: stored.RecordID, Version: stored.Version},
		Destination: EventDestination{Type: "agent", Name: receiver.channel.Principal}}
	published, err := writer.PublishEvent(ctx, request, dest)
	if err != nil {
		t.Fatal(err)
	}
	repeated, err := writer.PublishEvent(ctx, request, dest)
	if err != nil || published.EventID != repeated.EventID || published.From != writer.channel.Principal ||
		published.Ref.RecordID != stored.RecordID || published.Ref.Version != stored.Version {
		t.Fatalf("duplicate publication identity: first=%+v retry=%+v err=%v", published, repeated, err)
	}
	status, err := writer.AgentEventStatus(ctx, EventStatusRequest{EventID: published.EventID}, dest)
	if err != nil || len(status.Deliveries) != 1 || status.Deliveries[0].Consumer != receiver.channel.Principal {
		t.Fatalf("duplicate publication deliveries: %+v %v", status, err)
	}
}
