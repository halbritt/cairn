package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// This opt-in trial uses committed decision clauses and native commit subjects.
// It measures retrieval coverage in explicitly later recurrence scenarios. The
// newly captured memories are never claimed to have existed in the original run.
func TestStriatumHistoryRecurrenceCoverage(t *testing.T) {
	source := os.Getenv("CAIRN_HISTORY_SOURCE")
	output := os.Getenv("CAIRN_HISTORY_REPORT")
	if source == "" || output == "" {
		t.Skip("set CAIRN_HISTORY_SOURCE and CAIRN_HISTORY_REPORT for the native-history trial")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	git := func(args ...string) string {
		t.Helper()
		args = append([]string{"-C", source}, args...)
		body, err := exec.CommandContext(ctx, "git", args...).Output()
		if err != nil {
			t.Fatal(err)
		}
		return strings.TrimSpace(string(body))
	}
	head := git("rev-parse", "HEAD")
	op, root := testOperator(t)
	collector := testStore(t, Channel{Principal: "service:committed-history-collector", Instrumented: true})
	repo := "history-trial:" + uuid.NewString()
	sources := map[string]string{"D0005.C10": "decisions/D0005-artifact-lifecycle-and-state-machine.md", "D0008.C1": "decisions/D0008-scheduler-and-capacity-policy.md", "D0013.C2": "decisions/D0013-adapter-supervision.md"}
	records := map[string]string{}
	sourceHashes := map[string]string{}
	for _, clause := range []string{"D0005.C10", "D0008.C1", "D0013.C2"} {
		path := sources[clause]
		raw, err := exec.CommandContext(ctx, "git", "-C", source, "show", head+":"+path).Output()
		if err != nil {
			t.Fatal(err)
		}
		body := string(raw)
		digest := sha256.Sum256(raw)
		sourceHashes[path] = hex.EncodeToString(digest[:])
		selected := ""
		for _, line := range strings.Split(body, "\n") {
			if strings.HasPrefix(line, "- **"+clause+" ") {
				selected = line
				break
			}
		}
		if selected == "" {
			t.Fatalf("accepted source clause missing: %s", clause)
		}
		draft := Draft{Kind: "decision", Body: selected, Scope: Scope{repo, "*", "*"}, ClaimType: "self", Pins: &Applicability{Revision: head}}
		record, err := collector.Create(ctx, CreateRequest{uuid.NewString(), draft})
		if err != nil {
			t.Fatal(err)
		}
		evidenceBody, err := json.Marshal(map[string]string{"commit": head, "path": path, "file_sha256": sourceHashes[path], "clause": clause, "selected_text": selected})
		if err != nil {
			t.Fatal(err)
		}
		evidence, err := collector.CaptureEvidence(ctx, EvidenceRequest{uuid.NewString(), repo, string(evidenceBody), "Explicit committed decision-clause capture", "local"})
		if err != nil {
			t.Fatal(err)
		}
		promoted, err := op.Promote(ctx, PromoteRequest{uuid.NewString(), record.RecordID, record.Version, root.ID, []string{evidence.ID}, "Admit this exact accepted clause with existing committed source evidence for the bounded recurrence trial"})
		if err != nil {
			t.Fatal(err)
		}
		records[promoted.RecordID] = clause
	}
	log := git("log", "-12", "--format=%H%x09%s", "--", "decisions", "internal/backend/llm", "internal/scheduler")
	type scenario struct {
		Commit           string   `json:"commit"`
		QuerySHA256      string   `json:"query_sha256"`
		Selected         []string `json:"selected_clauses"`
		Recompiled       bool     `json:"recompiled"`
		NoMemorySelected int      `json:"no_memory_selected"`
	}
	cases := []scenario{}
	hits := 0
	for _, line := range strings.Split(log, "\n") {
		commit, query, ok := strings.Cut(line, "\t")
		if !ok {
			t.Fatal("malformed native history record")
		}
		qhash := sha256.Sum256([]byte(query))
		c := scenario{Commit: commit, QuerySHA256: hex.EncodeToString(qhash[:]), Selected: []string{}}
		req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, commit, "recurrence"}, Query: query, Purpose: "planning", AvailableTokens: 64000, Context: &ContextPins{Revision: head}}
		p, err := op.Compile(ctx, req, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range p.Semantic.Selected {
			c.Selected = append(c.Selected, records[entry.Record.RecordID])
		}
		if len(c.Selected) > 0 {
			hits++
		}
		replay, err := op.Recompile(ctx, RecompileRequest{p.ReceiptID, query})
		if err != nil || replay.Seal != p.Seal {
			t.Fatalf("native query recompile: %v", err)
		}
		c.Recompiled = true
		req.RequestID = uuid.NewString()
		req.Scope.Repo = "empty:" + repo
		baseline, err := op.Compile(ctx, req, Destination{"local", true})
		if err != nil {
			t.Fatal(err)
		}
		c.NoMemorySelected = len(baseline.Semantic.Selected)
		cases = append(cases, c)
	}
	report := map[string]any{"schema": "cairn.history-coverage/1", "source_head": head, "source_hashes": sourceHashes, "source_clause_count": len(sources), "scenario_count": len(cases), "scenarios_with_selected_memory": hits, "scenarios": cases, "availability": "later_recurrence_only", "interpretation": "Native commit subjects drive this provider-free retrieval coverage probe. Clauses were captured now, not before the historical incident. Recompiled seals establish retained-input reproducibility, not avoided failures, task acceptance, search/native-model baseline superiority or causal benefit."}
	body, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(output, append(body, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
	t.Logf("native recurrence coverage: %d/%d subjects selected a source clause; all historical seals reproduced", hits, len(cases))
}
