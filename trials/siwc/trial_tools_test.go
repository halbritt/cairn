package siwc

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

const trialTestRepo = "trial:task-eval"
const trialTestTask = "source-fit/buried_live_qualification"
const trialTestRun = "buried_live_qualification/current"
const trialTestRecord = "11111111-1111-4111-8111-111111111111"
const trialTestRequest = "22222222-2222-4222-8222-222222222222"
const trialTestReceipt = "33333333-3333-4333-8333-333333333333"
const trialTestHandle = "44444444-4444-4444-8444-444444444444"
const trialTestBody = "synthetic source body"

func TestTrialStoreToolsSearchPullReceiptAndLineage(t *testing.T) {
	tools, observedPull := newTrialToolsFixture(t, "shareable", "active")
	search, err := tools.Search(context.Background(), json.RawMessage(`{"query":"live relay admission allowance"}`))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(search), "pull_command") || strings.Contains(string(search), "secret/path") {
		t.Fatalf("search leaked executable command metadata: %s", search)
	}
	args := json.RawMessage(`{"handle":"` + trialTestHandle + `","request_id":"` + trialTestRequest + `","receipt_id":"` + trialTestReceipt + `"}`)
	if _, err := tools.Pull(context.Background(), args); err != nil {
		t.Fatal(err)
	}
	gotArgs, err := os.ReadFile(observedPull)
	if err != nil {
		t.Fatal(err)
	}
	var got, want any
	if json.Unmarshal(gotArgs, &got) != nil || json.Unmarshal(args, &want) != nil || !jsonEqual(got, want) {
		t.Fatalf("pull request changed: %s", gotArgs)
	}
	receipts := tools.Receipts()
	wantDigest := sha256.Sum256([]byte(trialTestBody))
	if len(receipts) != 2 || receipts[0].Extent != "preview" || receipts[1].RecordID != trialTestRecord || receipts[1].Version != 1 || receipts[1].Extent != "full_body" || receipts[1].SourceSHA256 != hex.EncodeToString(wantDigest[:]) || receipts[1].DeliveredSHA256 != hex.EncodeToString(wantDigest[:]) || receipts[1].DeliveredBytes != len(trialTestBody) {
		t.Fatalf("unexpected body-free receipts: %+v", receipts)
	}
	encoded, _ := json.Marshal(receipts)
	if strings.Contains(string(encoded), trialTestBody) {
		t.Fatal("receipt retained source body")
	}
}

func TestTrialStoreToolsRejectsCallerInventedPullArguments(t *testing.T) {
	tools, _ := newTrialToolsFixture(t, "shareable", "active")
	_, err := tools.Search(context.Background(), json.RawMessage(`{"query":"live relay admission allowance"}`))
	if err != nil {
		t.Fatal(err)
	}
	args := json.RawMessage(`{"request_id":"55555555-5555-4555-8555-555555555555","receipt_id":"` + trialTestReceipt + `","handle":"` + trialTestHandle + `"}`)
	if _, err := tools.Pull(context.Background(), args); err == nil || !strings.Contains(err.Error(), "not returned") {
		t.Fatalf("caller-invented pull identity was accepted: %v", err)
	}
}

func TestTrialStoreToolsRejectsIneligibleSourceAndKeepsNoReceipt(t *testing.T) {
	for _, tc := range []struct{ sensitivity, lifecycle string }{{"local", "active"}, {"shareable", "forgotten"}} {
		t.Run(tc.sensitivity+"/"+tc.lifecycle, func(t *testing.T) {
			tools, _ := newTrialToolsFixture(t, tc.sensitivity, tc.lifecycle)
			if _, err := tools.Search(context.Background(), json.RawMessage(`{"query":"live relay admission allowance"}`)); err != nil {
				t.Fatal(err)
			}
			args := json.RawMessage(`{"request_id":"` + trialTestRequest + `","receipt_id":"` + trialTestReceipt + `","handle":"` + trialTestHandle + `"}`)
			if _, err := tools.Pull(context.Background(), args); err == nil || !strings.Contains(err.Error(), "not eligible") {
				t.Fatalf("ineligible source accepted: %v", err)
			}
			for _, receipt := range tools.Receipts() {
				if receipt.Extent != "preview" {
					t.Fatalf("failed source validation left a source-body receipt: %+v", receipt)
				}
			}
		})
	}
}

func TestTrialStoreToolsRejectsWrongHostedIdentityAndManagedStore(t *testing.T) {
	tools, provenance, _ := newTrialToolsFixtureWithProvenance(t, "shareable", "active")
	_ = tools
	identityPath := filepath.Join(provenance.Root, "home", "identities.json")
	body := []byte(`[{"token_sha256":"` + strings.Repeat("0", 64) + `","principal":"agent:task-eval-fixture","repo":"` + trialTestRepo + `","role":"agent","destination":"hosted"}]`)
	if err := os.WriteFile(identityPath, body, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := NewTrialStoreTools(provenance); err == nil || !strings.Contains(err.Error(), "not bound") {
		t.Fatalf("mismatched token identity accepted: %v", err)
	}
	provenance.Root = filepath.Join(os.Getenv("HOME"), ".local", "share", "cairn")
	if _, err := NewTrialStoreTools(provenance); err == nil {
		t.Fatal("managed local store path accepted")
	}
}

func TestReceiptForRecordValidatesPartialSpanDigest(t *testing.T) {
	data := []byte("portion")
	digest := sha256.Sum256(data)
	record := trialRecord{RecordID: trialTestRecord, Version: 2}
	receipt, err := receiptForRecord(record, &trialSpan{Offset: 12, End: 19, TotalBytes: 40,
		SHA256: hex.EncodeToString(digest[:]), SourceSHA256: strings.Repeat("a", 64), Body: string(data)}, true)
	if err != nil || receipt.Extent != "partial_span" || receipt.DeliveredBytes != 7 || !receipt.Mandatory {
		t.Fatalf("partial span receipt: %+v, %v", receipt, err)
	}
	bad := &trialSpan{Offset: 12, End: 19, TotalBytes: 40, SHA256: strings.Repeat("b", 64),
		SourceSHA256: strings.Repeat("a", 64), Body: string(data)}
	if _, err := receiptForRecord(record, bad, false); err == nil {
		t.Fatal("span digest mismatch accepted")
	}
}

func newTrialToolsFixture(t *testing.T, sensitivity, lifecycle string) (*TrialStoreTools, string) {
	t.Helper()
	tools, _, observed := newTrialToolsFixtureWithProvenance(t, sensitivity, lifecycle)
	return tools, observed
}

func newTrialToolsFixtureWithProvenance(t *testing.T, sensitivity, lifecycle string) (*TrialStoreTools, TrialStoreProvenance, string) {
	t.Helper()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(root, "home"), 0700); err != nil {
		t.Fatal(err)
	}
	tokenPath := filepath.Join(root, "agent.token")
	token := []byte("synthetic-disposable-token")
	if err := os.WriteFile(tokenPath, token, 0600); err != nil {
		t.Fatal(err)
	}
	tokenDigest := sha256.Sum256(token)
	identity := `[{"token_sha256":"` + hex.EncodeToString(tokenDigest[:]) + `","principal":"agent:task-eval-fixture","repo":"` + trialTestRepo + `","role":"agent","destination":"hosted"}]`
	if err := os.WriteFile(filepath.Join(root, "home", "identities.json"), []byte(identity), 0600); err != nil {
		t.Fatal(err)
	}
	socketDir, err := os.MkdirTemp("/tmp", "cte-sock-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(socketDir) })
	_ = os.Remove(filepath.Join(root, "sock"))
	if err := os.Symlink(socketDir, filepath.Join(root, "sock")); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", filepath.Join(socketDir, "api.sock"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	observedPull := filepath.Join(root, "observed-pull.json")
	bodyDigest := sha256.Sum256([]byte(trialTestBody))
	searchJSON := `{"ok":true,"status":"OK","meta":{},"data":{"schema":"cairn.agent-search/1","scope":{"repo":"` + trialTestRepo + `","task_id":"` + trialTestTask + `","run_id":"` + trialTestRun + `"},"destination":{"name":"hosted","allow_local":false},"index":[{"record_id":"` + trialTestRecord + `","version":1,"class":"A","body_sha256":"` + hex.EncodeToString(bodyDigest[:]) + `","summary":"fixture","pull_command":"secret/path cairn agent pull","pull_arguments":{"request_id":"` + trialTestRequest + `","receipt_id":"` + trialTestReceipt + `","handle":"` + trialTestHandle + `"}}]}}`
	pullJSON := `{"ok":true,"status":"OK","data":{"selection":{"mandatory":false,"reason":"fixture","record":{"record_id":"` + trialTestRecord + `","version":1,"class":"A","lifecycle":"` + lifecycle + `","sensitivity":"` + sensitivity + `","scope":{"repo":"` + trialTestRepo + `","task_id":"*","run_id":"*"},"body":"` + trialTestBody + `","unexpected_future_field":"allowed"},"authority":[],"evidence":[]},"credits_remaining":2}}`
	script := "#!/bin/sh\ncase \" $* \" in\n  *' search '*) printf '%s\\n' '" + searchJSON + "' ;;\n  *' pull '*) cat > '" + observedPull + "'; printf '%s\\n' '" + pullJSON + "' ;;\n  *) exit 9 ;;\nesac\n"
	binary := filepath.Join(root, "cairn-fixture")
	if err := os.WriteFile(binary, []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	binaryDigest := sha256.Sum256([]byte(script))
	provenance := TrialStoreProvenance{Root: root, SocketDir: socketDir, Socket: filepath.Join(socketDir, "api.sock"),
		TokenFile: tokenPath, Binary: binary, BinarySHA256: hex.EncodeToString(binaryDigest[:]),
		Repo: trialTestRepo, Task: trialTestTask, Run: trialTestRun}
	tools, err := NewTrialStoreTools(provenance)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("shell needed for executable test fixture")
	}
	return tools, provenance, observedPull
}

func jsonEqual(a, b any) bool {
	left, _ := json.Marshal(a)
	right, _ := json.Marshal(b)
	return string(left) == string(right)
}

func TestTrialStoreToolsRejectsSearchedDigestMismatch(t *testing.T) {
	tools, _ := newTrialToolsFixture(t, "shareable", "active")
	if _, err := tools.Search(context.Background(), json.RawMessage(`{"query":"live relay admission allowance"}`)); err != nil {
		t.Fatal(err)
	}
	args := json.RawMessage(`{"request_id":"` + trialTestRequest + `","receipt_id":"` + trialTestReceipt + `","handle":"` + trialTestHandle + `"}`)
	key, err := canonicalJSON(args)
	if err != nil {
		t.Fatal(err)
	}
	expected := tools.returned[string(key)]
	expected.SHA256 = strings.Repeat("f", 64)
	tools.returned[string(key)] = expected
	if _, err := tools.Pull(context.Background(), args); err == nil || !strings.Contains(err.Error(), "did not match") {
		t.Fatalf("pull digest differing from its search was accepted: %v", err)
	}
	for _, receipt := range tools.Receipts() {
		if receipt.Extent != "preview" {
			t.Fatalf("digest mismatch left a pull receipt: %+v", receipt)
		}
	}
}

func TestTrialStoreToolsSearchRefusesLocalDestinationOrPrivateSelectedSource(t *testing.T) {
	base := `{"schema":"cairn.agent-search/1","scope":{"repo":"` + trialTestRepo + `","task_id":"` + trialTestTask + `","run_id":"` + trialTestRun + `"},"destination":{"name":"hosted","allow_local":false},"index":[]}`
	localDestination := strings.Replace(base, `"allow_local":false`, `"allow_local":true`, 1)
	if err := validateTrialSearchView(json.RawMessage(localDestination), trialTestRepo, trialTestTask, trialTestRun); err == nil {
		t.Fatal("local destination search view accepted")
	}
	unknownDestination := strings.Replace(base, `"allow_local":false`, `"allow_local":null`, 1)
	if err := validateTrialSearchView(json.RawMessage(unknownDestination), trialTestRepo, trialTestTask, trialTestRun); err == nil {
		t.Fatal("unknown local destination status accepted")
	}
	privateSelected := strings.TrimSuffix(base, "}") + `,"selected":[{"mandatory":true,"record":{"record_id":"` + trialTestRecord + `","version":1,"class":"C","lifecycle":"active","sensitivity":"local","scope":{"repo":"` + trialTestRepo + `","task_id":"*","run_id":"*"},"body":"amber harbor"}}]}`
	if _, err := validateTrialSearchDeliveries(json.RawMessage(privateSelected), trialTestRepo, trialTestTask, trialTestRun); err == nil || !strings.Contains(err.Error(), "not eligible") {
		t.Fatalf("private mandatory search source accepted: %v", err)
	}
}

func TestTrialStoreToolsAgainstDisposableTrialStore(t *testing.T) {
	root := os.Getenv("CAIRN_SIWC_TRIAL_ROOT")
	if root == "" {
		t.Skip("real disposable TrialStore fixture is not configured")
	}
	p := TrialStoreProvenance{Root: root, SocketDir: os.Getenv("CAIRN_SIWC_SOCKET_DIR"),
		Socket: os.Getenv("CAIRN_SIWC_SOCKET"), TokenFile: os.Getenv("CAIRN_SIWC_TOKEN_FILE"),
		Binary: os.Getenv("CAIRN_SIWC_BINARY"), BinarySHA256: os.Getenv("CAIRN_SIWC_BINARY_SHA256"),
		Repo: os.Getenv("CAIRN_SIWC_REPO"), Task: os.Getenv("CAIRN_SIWC_TASK"), Run: os.Getenv("CAIRN_SIWC_RUN")}
	tools, err := NewTrialStoreTools(p)
	if err != nil {
		t.Fatal(err)
	}
	query := os.Getenv("CAIRN_SIWC_QUERY")
	search, err := tools.Search(context.Background(), mustJSON(t, map[string]any{"query": query}))
	if err != nil {
		t.Fatal(err)
	}
	var result struct {
		Index []struct {
			RecordID      string          `json:"record_id"`
			PullArguments json.RawMessage `json:"pull_arguments"`
		} `json:"index"`
	}
	if err := json.Unmarshal(search, &result); err != nil || len(result.Index) == 0 || len(result.Index[0].PullArguments) == 0 {
		t.Fatalf("real disposable Cairn search returned no pullable fixture: %s (%v)", search, err)
	}
	if _, err := tools.Pull(context.Background(), result.Index[0].PullArguments); err != nil {
		t.Fatal(err)
	}
	receipts := tools.Receipts()
	found := false
	for _, receipt := range receipts {
		if receipt.RecordID == result.Index[0].RecordID && receipt.Extent == "full_body" {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("real disposable source pull has no full-source receipt: %+v", receipts)
	}
}

func mustJSON(t *testing.T, value any) json.RawMessage {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return encoded
}

func TestCollectPullArgumentsRejectsAmbiguousSourceLineage(t *testing.T) {
	pull := `"pull_arguments":{"request_id":"` + trialTestRequest + `","receipt_id":"` + trialTestReceipt + `","handle":"` + trialTestHandle + `"}`
	entry := `{"record_id":"` + trialTestRecord + `","version":1,"class":"A","body_sha256":"` + strings.Repeat("a", 64) + `","summary":"fixture",` + pull + `}`
	different := strings.Replace(entry, trialTestRecord, "55555555-5555-4555-8555-555555555555", 1)
	view := json.RawMessage(`{"index":[` + entry + `,` + different + `]}`)
	if _, err := collectPullArguments(view); err == nil || !strings.Contains(err.Error(), "different source identities") {
		t.Fatalf("ambiguous returned pull arguments accepted: %v", err)
	}
	view = json.RawMessage(`{"index":[` + entry + `,` + entry + `]}`)
	if _, err := collectPullArguments(view); err != nil {
		t.Fatalf("same-source repeated pull arguments should remain idempotent: %v", err)
	}
}
