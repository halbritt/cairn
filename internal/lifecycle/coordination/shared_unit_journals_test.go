package coordination

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
)

func TestSharedWatchJournalFactsKeepReplyObligationsAndMissingReferences(t *testing.T) {
	stateDir := t.TempDir()
	if err := os.Chmod(stateDir, 0700); err != nil {
		t.Fatal(err)
	}
	files := map[string]map[string]any{
		"pending": {"schema": journal.Schema, "attempt_id": "pending", "kind": "notice", "completion": map[string]any{"status": "pending"}},
		"reply": {"schema": journal.Schema, "attempt_id": "reply", "kind": "request", "completion": map[string]any{
			"kind": "complete", "status": "committed", "result": map[string]any{"record_id": "88888888-8888-4888-8888-888888888888", "version": 1}}},
		"missing-result": {"schema": journal.Schema, "attempt_id": "missing-result", "kind": "request", "completion": map[string]any{
			"kind": "complete", "status": "committed"}, "finish_reply": map[string]any{"request_id": "retained-request"}},
		"final-unindexed": {"schema": journal.Schema, "attempt_id": "final-unindexed", "kind": "notice", "completion": map[string]any{"kind": "ack", "status": "committed"}},
	}
	before := map[string][]byte{}
	for id, body := range files {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		path := filepath.Join(stateDir, "intents", id+".json")
		writePrivateFile(t, path, raw, 0600)
		before[path] = raw
	}
	// The source's settled index is true even though a request can still reply.
	if !journal.SettledJournalIndex(stateDir, map[string]any{"inbox_journals": []string{"reply"}}) {
		t.Fatal("fixture did not reach the retained settled/reply_possible discrepancy")
	}
	inspection, err := InspectWatchJournals(stateDir, []string{"reply", "missing"})
	if err != nil || !inspection.DirectoryExists || len(inspection.Journals) != 5 {
		t.Fatalf("inspection=%+v err=%v", inspection, err)
	}
	rows := map[string]WatchJournalFacts{}
	for _, facts := range inspection.Journals {
		rows[facts.AttemptID] = facts
	}
	if !rows["pending"].Status.PendingReplay || !rows["reply"].Status.ReplyPossible ||
		!rows["reply"].Status.HasUsableFinishReplyRef || !rows["missing-result"].Status.UnmaterializedFinishReply ||
		rows["missing-result"].Status.HasUsableFinishReplyRef || rows["missing"].Status.Exists {
		t.Fatalf("lost journal drain facts: %+v", rows)
	}
	entries, err := os.ReadDir(filepath.Join(stateDir, "intents"))
	if err != nil || len(entries) != len(before) {
		t.Fatalf("inspection changed membership: %v %v", entries, err)
	}
	for path, raw := range before {
		after, err := os.ReadFile(path)
		if err != nil || !bytes.Equal(after, raw) {
			t.Fatalf("inspection mutated %s: %q %v", path, after, err)
		}
	}
}

func TestSharedWatchJournalLockAndUnknownFacts(t *testing.T) {
	stateDir := t.TempDir()
	lease, err := TryAcquireIntentLock(stateDir, "busy")
	if err != nil {
		t.Fatal(err)
	}
	defer mustRelease(t, lease)
	writePrivateFile(t, filepath.Join(stateDir, "intents", "bad.json"), []byte("not-json"), 0600)
	inspection, err := InspectWatchJournals(stateDir, []string{"busy"})
	if err != nil || len(inspection.Journals) != 2 || inspection.Journals[0].Status.ParseErr == nil ||
		inspection.Journals[1].Status.Exists {
		t.Fatalf("lost unknown/busy facts: %+v %v", inspection, err)
	}
	if other, err := TryAcquireIntentLock(stateDir, "busy"); !IsSessionBusy(err) {
		if other != nil {
			mustRelease(t, other)
		}
		t.Fatalf("inspection disturbed an existing lock: %v", err)
	}
	if _, err := InspectWatchJournals(stateDir, []string{"../other-session"}); err == nil {
		t.Fatal("inspection accepted an escaping reference")
	}
	missing, err := InspectWatchJournals(filepath.Join(stateDir, "absent"), nil)
	if err != nil || missing.DirectoryExists {
		t.Fatalf("missing directory was asserted present: %+v %v", missing, err)
	}
}
