package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

type adoptionFixture struct {
	store   *Store
	root    Grant
	record  Record
	pkg     Package
	request AdoptContextRequest
}

func adoptionStore(t *testing.T) (*Store, Grant) {
	t.Helper()
	s := testStore(t, Channel{Principal: "operator:tests", Operator: true, Instrumented: true})
	root, err := s.Bootstrap(context.Background(), BootstrapRequest{"82bf5bce-dc6c-4d03-a49f-1677bdcc9a32", "Install synthetic test operator root"})
	if err != nil {
		t.Fatal(err)
	}
	return s, root
}

// completedRun is a claimed run with a recorded outcome whose context file, as
// an adoption observed it, is the canonical package.
func completedRun(t *testing.T, s *Store, repo string, outcome bool) (Package, AdoptContextRequest) {
	t.Helper()
	ctx := context.Background()
	pkg, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if err = s.ClaimRun(ctx, pkg.ReceiptID); err != nil {
		t.Fatal(err)
	}
	if outcome {
		zero := 0
		if _, err = s.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: pkg.ReceiptID, ExitCode: &zero, DurationMS: 12, ProcessState: "exited",
			StdoutSHA256: strings.Repeat("a", 64), StderrSHA256: strings.Repeat("b", 64)}); err != nil {
			t.Fatal(err)
		}
	}
	rendered, err := pkg.Render()
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256([]byte(rendered))
	return pkg, AdoptContextRequest{
		RequestID: uuid.NewString(), ReceiptID: pkg.ReceiptID, Directory: "/synthetic/runs/" + pkg.ReceiptID,
		DirectoryDevice: "64768", DirectoryInode: "1048577", OwnershipID: uuid.NewString(),
		ObservedBytes: int64(len(rendered)), ObservedSHA256: hex.EncodeToString(sum[:]),
		FileDevice: "64768", FileInode: "1048600", FileModifiedAt: time.Date(2026, 9, 1, 12, 30, 0, 0, time.UTC),
	}
}

func newAdoptionFixture(t *testing.T) adoptionFixture {
	t.Helper()
	s, root := adoptionStore(t)
	repo := uuid.NewString()
	record, err := s.Create(context.Background(), CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	pkg, request := completedRun(t, s, repo, true)
	return adoptionFixture{s, root, record, pkg, request}
}

func (f adoptionFixture) rows(t *testing.T, query string, args ...any) int {
	t.Helper()
	var count int
	if err := f.store.pool.QueryRow(context.Background(), query, args...).Scan(&count); err != nil {
		t.Fatal(err)
	}
	return count
}

func (f adoptionFixture) custody(t *testing.T) int {
	return f.rows(t, `SELECT count(*) FROM cairn.managed_context WHERE receipt_id=$1`, f.pkg.ReceiptID)
}

func targetTypes(preview RetractionPreview, receipt string) map[string]string {
	found := map[string]string{}
	for _, target := range preview.DeletionTargets {
		if target.TargetID == receipt {
			found[target.TargetType] = target.Status
		}
	}
	return found
}

func TestAdoptedContextEntersDeletionInventoryWithoutChangingRunHistory(t *testing.T) {
	ctx := context.Background()
	f := newAdoptionFixture(t)
	history := func() [6]int {
		var counts [6]int
		for i, table := range []string{"delivery_receipt", "run_outcome", "usage_observation", "usage_coverage", "run_assessment", "run_binding"} {
			counts[i] = f.rows(t, `SELECT count(*) FROM cairn.`+table+` WHERE receipt_id=$1`, f.pkg.ReceiptID)
		}
		return counts
	}
	before := history()
	preview, err := f.store.PreviewDeletion(ctx, f.record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if got := targetTypes(preview, f.pkg.ReceiptID); got["run_artifacts"] != "not_possible" || got["managed_context"] != "" {
		t.Fatalf("an unadopted historical launch is a residual, not a managed effect: %+v", got)
	}
	var generation int
	if err = f.store.pool.QueryRow(ctx, `SELECT use_generation FROM cairn.memory_record WHERE record_id=$1`, f.record.RecordID).Scan(&generation); err != nil {
		t.Fatal(err)
	}

	adopted, err := f.store.AdoptManagedContext(ctx, f.request)
	if err != nil {
		t.Fatal(err)
	}
	if adopted.CustodyOrigin != "adopted" || adopted.AlreadyAdopted || adopted.ReceiptID != f.pkg.ReceiptID || adopted.Directory != f.request.Directory ||
		adopted.OwnershipID != f.request.OwnershipID || adopted.BodySHA256 != f.request.ObservedSHA256 || adopted.ObservedBytes != f.request.ObservedBytes ||
		adopted.ObservedFileDevice != "64768" || adopted.ObservedFileInode != "1048600" || !adopted.ObservedFileModifiedAt.Equal(f.request.FileModifiedAt) || adopted.AdoptedAt.IsZero() {
		t.Fatalf("adoption did not record what it observed: %+v", adopted)
	}
	if after := history(); after != before {
		t.Fatalf("adoption claimed history: delivery/outcome/usage/assessment/binding rows %v became %v", before, after)
	}
	var caller, origin string
	if err = f.store.pool.QueryRow(ctx, `SELECT registered_by,custody_origin FROM cairn.managed_context WHERE receipt_id=$1`, f.pkg.ReceiptID).Scan(&caller, &origin); err != nil || caller != "operator:tests" || origin != "adopted" {
		t.Fatalf("custody is stamped by the store: %q %q %v", caller, origin, err)
	}
	var next int
	if err = f.store.pool.QueryRow(ctx, `SELECT use_generation FROM cairn.memory_record WHERE record_id=$1`, f.record.RecordID).Scan(&next); err != nil || next != generation+1 {
		t.Fatalf("an earlier deletion preview must go stale: %d -> %d %v", generation, next, err)
	}

	// The same deletion workflow now covers the file: a stale preview refuses, a fresh one lists it.
	_, err = f.store.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: f.record.RecordID, ExpectedVersion: f.record.Version, GrantID: f.root.ID, PreviewID: preview.PreviewID})
	requireCode(t, err, "STALE_PREVIEW")
	fresh, err := f.store.PreviewDeletion(ctx, f.record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	if got := targetTypes(fresh, f.pkg.ReceiptID); got["managed_context"] != "pending" || got["run_artifacts"] != "" {
		t.Fatalf("adopted file missing from the inventory: %+v", got)
	}
	deletion, err := f.store.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: f.record.RecordID, ExpectedVersion: f.record.Version, GrantID: f.root.ID, PreviewID: fresh.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	target, err := f.store.ContextPurgeTarget(ctx, deletion.DeletionID, f.pkg.ReceiptID)
	if err != nil || target.Status != "pending" || target.OwnershipID != adopted.OwnershipID || target.Directory != adopted.Directory {
		t.Fatalf("purge cannot find the adopted file: %+v %v", target, err)
	}
	// Forgetting excludes the payload; nothing more can be adopted.
	again := f.request
	again.RequestID = uuid.NewString()
	_, err = f.store.AdoptManagedContext(ctx, again)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
}

func TestAdoptionRetriesAreExactAndConflictsRefuse(t *testing.T) {
	ctx := context.Background()
	f := newAdoptionFixture(t)
	first, err := f.store.AdoptManagedContext(ctx, f.request)
	if err != nil {
		t.Fatal(err)
	}
	replay, err := f.store.AdoptManagedContext(ctx, f.request)
	if err != nil || replay != first {
		t.Fatalf("the same request must return the original result: %+v %v", replay, err)
	}
	changed := f.request
	changed.FileInode = "7"
	_, err = f.store.AdoptManagedContext(ctx, changed)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")

	// A new request for the identical adoption changes nothing and says so.
	same := f.request
	same.RequestID = uuid.NewString()
	again, err := f.store.AdoptManagedContext(ctx, same)
	if err != nil || !again.AlreadyAdopted || again.OwnershipID != first.OwnershipID || !again.AdoptedAt.Equal(first.AdoptedAt) {
		t.Fatalf("identical adoption: %+v %v", again, err)
	}
	if n := f.custody(t); n != 1 {
		t.Fatalf("custody rows: %d", n)
	}
	for name, mutate := range map[string]func(*AdoptContextRequest){
		"other ownership": func(r *AdoptContextRequest) { r.OwnershipID = uuid.NewString() },
		"other directory": func(r *AdoptContextRequest) { r.Directory = "/synthetic/moved/" + r.ReceiptID },
		"other identity":  func(r *AdoptContextRequest) { r.DirectoryInode = "9" },
	} {
		t.Run(name, func(t *testing.T) {
			conflicting := f.request
			conflicting.RequestID = uuid.NewString()
			mutate(&conflicting)
			_, err := f.store.AdoptManagedContext(ctx, conflicting)
			requireCode(t, err, "CUSTODY_CONFLICT")
		})
	}
	// Existing custody never excuses bytes that are not the retained package.
	wrong := same
	wrong.RequestID = uuid.NewString()
	wrong.ObservedSHA256 = strings.Repeat("c", 64)
	_, err = f.store.AdoptManagedContext(ctx, wrong)
	requireCode(t, err, "ARTIFACT_MISMATCH")
	if n := f.custody(t); n != 1 {
		t.Fatalf("custody rows after refusals: %d", n)
	}
}

func TestContextAdoptionCheckChangesNothing(t *testing.T) {
	ctx := context.Background()
	f := newAdoptionFixture(t)
	generation := func() int {
		return f.rows(t, `SELECT use_generation FROM cairn.memory_record WHERE record_id=$1`, f.record.RecordID)
	}
	before := generation()
	check := f.request
	check.OwnershipID = ""
	existing, err := f.store.CheckContextAdoption(ctx, check)
	if err != nil || existing != nil || f.custody(t) != 0 || generation() != before {
		t.Fatalf("a check must be read-only: %+v %v custody=%d", existing, err, f.custody(t))
	}
	if _, err = f.store.AdoptManagedContext(ctx, f.request); err != nil {
		t.Fatal(err)
	}
	after := generation()
	existing, err = f.store.CheckContextAdoption(ctx, f.request)
	if err != nil || existing == nil || existing.OwnershipID != f.request.OwnershipID || generation() != after {
		t.Fatalf("check of an adopted file: %+v %v", existing, err)
	}
	// A bad file is refused by the check, before an owner would create its marker.
	bad := check
	bad.ObservedSHA256 = strings.Repeat("d", 64)
	_, err = f.store.CheckContextAdoption(ctx, bad)
	requireCode(t, err, "ARTIFACT_MISMATCH")
}

func TestAdoptionRefusesUnsupportedAndUnverifiableRuns(t *testing.T) {
	ctx := context.Background()
	f := newAdoptionFixture(t)
	repo := f.record.Scope.Repo
	notLaunched, err := f.store.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "unlaunched"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	running, runningRequest := completedRun(t, f.store, repo, false)
	other := testStore(t, Channel{Principal: "operator:someone-else", Operator: true, Instrumented: true})
	plain := testStore(t, Channel{Principal: "operator:tests", Operator: true})
	unprivileged := testStore(t, Channel{Principal: "operator:tests", Instrumented: true})
	missing := f.request
	missing.ReceiptID = uuid.NewString()
	missing.Directory = "/synthetic/runs/" + missing.ReceiptID

	cases := []struct {
		name  string
		store *Store
		req   AdoptContextRequest
		code  string
	}{
		{"never launched", f.store, AdoptContextRequest{ReceiptID: notLaunched.ReceiptID, Directory: "/synthetic/runs/" + notLaunched.ReceiptID}, "INVALID_REQUEST"},
		{"no recorded outcome", f.store, runningRequest, "INVALID_REQUEST"},
		{"another principal's receipt", other, f.request, "AUTHORITY_DENIED"},
		{"not instrumented", plain, f.request, "AUTHORITY_DENIED"},
		{"not an operator", unprivileged, f.request, "AUTHORITY_DENIED"},
		{"unknown receipt", f.store, missing, "NOT_FOUND"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			req := c.req
			req.RequestID = uuid.NewString()
			if req.OwnershipID == "" {
				req.OwnershipID = uuid.NewString()
			}
			if req.ObservedSHA256 == "" {
				req.ObservedSHA256, req.ObservedBytes = f.request.ObservedSHA256, f.request.ObservedBytes
				req.DirectoryDevice, req.DirectoryInode, req.FileDevice, req.FileInode = "1", "2", "3", "4"
				req.FileModifiedAt = f.request.FileModifiedAt
			}
			_, err := c.store.AdoptManagedContext(ctx, req)
			requireCode(t, err, c.code)
			_, err = c.store.CheckContextAdoption(ctx, req)
			requireCode(t, err, c.code)
		})
	}
	for _, receipt := range []string{notLaunched.ReceiptID, running.ReceiptID, missing.ReceiptID} {
		if n := f.rows(t, `SELECT count(*) FROM cairn.managed_context WHERE receipt_id=$1`, receipt); n != 0 {
			t.Fatalf("a refused adoption left custody for %s", receipt)
		}
	}
	if f.custody(t) != 0 {
		t.Fatal("a refused adoption left custody")
	}
}

func TestAdoptionRequestValidationAndByteChecks(t *testing.T) {
	ctx := context.Background()
	f := newAdoptionFixture(t)
	cases := map[string]func(*AdoptContextRequest){
		"relative directory": func(r *AdoptContextRequest) { r.Directory = "runs/" + r.ReceiptID },
		"non canonical":      func(r *AdoptContextRequest) { r.Directory = "/synthetic/../runs/" + r.ReceiptID },
		"wrong name":         func(r *AdoptContextRequest) { r.Directory = "/synthetic/runs/other" },
		"directory device":   func(r *AdoptContextRequest) { r.DirectoryDevice = "-1" },
		"file inode":         func(r *AdoptContextRequest) { r.FileInode = "01" },
		"uppercase digest":   func(r *AdoptContextRequest) { r.ObservedSHA256 = strings.ToUpper(r.ObservedSHA256) },
		"short digest":       func(r *AdoptContextRequest) { r.ObservedSHA256 = "abc" },
		"negative size":      func(r *AdoptContextRequest) { r.ObservedBytes = -1 },
		"oversized":          func(r *AdoptContextRequest) { r.ObservedBytes = MaxAdoptedContextBytes + 1 },
		"no modification":    func(r *AdoptContextRequest) { r.FileModifiedAt = time.Time{} },
		"ownership":          func(r *AdoptContextRequest) { r.OwnershipID = "marker" },
		"receipt":            func(r *AdoptContextRequest) { r.ReceiptID = "receipt" },
		"request":            func(r *AdoptContextRequest) { r.RequestID = "request" },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			req := f.request
			req.RequestID = uuid.NewString()
			mutate(&req)
			_, err := f.store.AdoptManagedContext(ctx, req)
			requireCode(t, err, "INVALID_REQUEST")
		})
	}
	// Valid-looking observations that are not this receipt's package.
	for name, mutate := range map[string]func(*AdoptContextRequest){
		"other bytes": func(r *AdoptContextRequest) { r.ObservedSHA256 = strings.Repeat("e", 64) },
		"truncated":   func(r *AdoptContextRequest) { r.ObservedBytes-- },
		"empty file": func(r *AdoptContextRequest) {
			r.ObservedBytes, r.ObservedSHA256 = 0, hex.EncodeToString(make([]byte, 32))
		},
		"extended file": func(r *AdoptContextRequest) { r.ObservedBytes++ },
	} {
		t.Run(name, func(t *testing.T) {
			req := f.request
			req.RequestID = uuid.NewString()
			mutate(&req)
			_, err := f.store.AdoptManagedContext(ctx, req)
			requireCode(t, err, "ARTIFACT_MISMATCH")
		})
	}
	if f.custody(t) != 0 {
		t.Fatal("a refused adoption left custody")
	}
}

func TestAdoptionRefusesOtherCustodyForTheReceiptOrMarker(t *testing.T) {
	ctx := context.Background()
	f := newAdoptionFixture(t)
	// Another run already holds registered custody and its marker.
	registered, registeredRequest := completedRun(t, f.store, f.record.Scope.Repo, false)
	_, err := f.store.RegisterManagedContext(ctx, ManagedContextRequest{OwnershipID: registeredRequest.OwnershipID, RequestID: registered.ReceiptID, ReceiptID: registered.ReceiptID,
		Directory: registeredRequest.Directory, DirectoryDevice: "64768", DirectoryInode: "77"})
	if err != nil {
		t.Fatal(err)
	}
	zero := 0
	if _, err = f.store.RecordOutcome(ctx, OutcomeRequest{RequestID: uuid.NewString(), ReceiptID: registered.ReceiptID, ExitCode: &zero, DurationMS: 1, ProcessState: "exited", StdoutSHA256: strings.Repeat("a", 64), StderrSHA256: strings.Repeat("b", 64)}); err != nil {
		t.Fatal(err)
	}
	registeredRequest.RequestID = uuid.NewString()
	_, err = f.store.AdoptManagedContext(ctx, registeredRequest)
	requireCode(t, err, "CUSTODY_CONFLICT") // registered custody is never replaced by adoption
	reused := f.request
	reused.OwnershipID = registeredRequest.OwnershipID
	_, err = f.store.AdoptManagedContext(ctx, reused)
	requireCode(t, err, "CUSTODY_CONFLICT") // a marker already names another receipt
	_, err = f.store.CheckContextAdoption(ctx, reused)
	requireCode(t, err, "CUSTODY_CONFLICT")
	if f.custody(t) != 0 {
		t.Fatal("a refused adoption left custody")
	}
}

func TestAdoptionNeverTakesOverRegisteredCustodyOfTheSameDescriptor(t *testing.T) {
	ctx := context.Background()
	f := newAdoptionFixture(t)
	// A run registered its own context before writing it. Adopting the identical
	// descriptor must not relabel that custody as an adoption or report success.
	registered := ManagedContextRequest{OwnershipID: f.request.OwnershipID, RequestID: f.pkg.ReceiptID, ReceiptID: f.pkg.ReceiptID,
		Directory: f.request.Directory, DirectoryDevice: f.request.DirectoryDevice, DirectoryInode: f.request.DirectoryInode}
	if _, err := f.store.pool.Exec(ctx, `UPDATE cairn.retrieval_receipt SET launch_claimed=true WHERE receipt_id=$1`, f.pkg.ReceiptID); err != nil {
		t.Fatal(err)
	}
	if _, err := f.store.RegisterManagedContext(ctx, registered); err != nil {
		t.Fatal(err)
	}
	_, err := f.store.AdoptManagedContext(ctx, f.request)
	requireCode(t, err, "CUSTODY_CONFLICT")
	_, err = f.store.CheckContextAdoption(ctx, f.request)
	requireCode(t, err, "CUSTODY_CONFLICT")
	if origin := f.rows(t, `SELECT count(*) FROM cairn.managed_context WHERE receipt_id=$1 AND custody_origin='registered'`, f.pkg.ReceiptID); origin != 1 {
		t.Fatalf("registered custody was changed: %d", origin)
	}
}

func TestAdoptionRefusesReceiptWithRecoveredCustody(t *testing.T) {
	ctx := context.Background()
	f := newAdoptionFixture(t)
	// Imported recovery custody has no receipt foreign key (its receipt may be absent
	// from a backup), but its deletion and application keys do; relax them for one fixture row.
	conn, err := f.store.pool.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Release()
	if _, err = conn.Exec(ctx, `SET session_replication_role = replica`); err != nil {
		t.Fatal(err)
	}
	_, err = conn.Exec(ctx, `INSERT INTO cairn.recovery_context(deletion_id,receipt_id,application_id,directory,directory_device,directory_inode,ownership_id,body_sha256)
 VALUES(gen_random_uuid(),$1,gen_random_uuid(),'/synthetic/recovered','1','2',gen_random_uuid(),$2)`, f.pkg.ReceiptID, f.request.ObservedSHA256)
	if _, resetErr := conn.Exec(ctx, `SET session_replication_role = DEFAULT`); resetErr != nil {
		t.Fatal(resetErr)
	}
	if err != nil {
		t.Fatal(err)
	}
	_, err = f.store.AdoptManagedContext(ctx, f.request)
	requireCode(t, err, "CUSTODY_CONFLICT")
	_, err = f.store.CheckContextAdoption(ctx, f.request)
	requireCode(t, err, "CUSTODY_CONFLICT")
	if f.custody(t) != 0 {
		t.Fatal("a refused adoption left custody")
	}
}

func TestAdoptionRaceWithForgettingNeverAdoptsForgottenContent(t *testing.T) {
	ctx := context.Background()
	f := newAdoptionFixture(t)
	check := f.request
	check.OwnershipID = ""
	if _, err := f.store.CheckContextAdoption(ctx, check); err != nil {
		t.Fatal(err)
	}
	preview, err := f.store.PreviewDeletion(ctx, f.record.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	// Forgetting commits between the owner's check and the registration.
	if _, err = f.store.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: f.record.RecordID, ExpectedVersion: f.record.Version, GrantID: f.root.ID, PreviewID: preview.PreviewID}); err != nil {
		t.Fatal(err)
	}
	_, err = f.store.AdoptManagedContext(ctx, f.request)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	_, err = f.store.CheckContextAdoption(ctx, check)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	if f.custody(t) != 0 {
		t.Fatal("forgotten content was adopted")
	}
}

func TestAdoptionKeepsRestoreAdmission(t *testing.T) {
	ctx := context.Background()
	s := restoreTestStore(t)
	recoveryRoot(t, s)
	repo := uuid.NewString()
	if _, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)}); err != nil {
		t.Fatal(err)
	}
	_, old := completedRun(t, s, repo, true)
	if _, err := s.FenceRestore(ctx, RestoreFenceRequest{RequestID: uuid.NewString(), Reason: "Fence restored launch receipts before service resumes"}); err != nil {
		t.Fatal(err)
	}
	// A receipt from before the fence is not admitted to new custody.
	_, err := s.AdoptManagedContext(ctx, old)
	requireCode(t, err, "STALE_PACKAGE")
	_, err = s.CheckContextAdoption(ctx, old)
	requireCode(t, err, "STALE_PACKAGE")
	_, current := completedRun(t, s, repo, true)
	if _, err = s.BeginRestore(ctx, BeginRestoreRequest{uuid.NewString(), "synthetic isolated older backup", "Pause restored store before reconciliation"}); err != nil {
		t.Fatal(err)
	}
	// Normal service, including new custody, stays paused until reconciliation.
	_, err = s.AdoptManagedContext(ctx, current)
	requireCode(t, err, "RESTORE_PAUSED")
	_, err = s.CheckContextAdoption(ctx, current)
	requireCode(t, err, "RESTORE_PAUSED")
	var rows int
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.managed_context`).Scan(&rows); err != nil || rows != 0 {
		t.Fatalf("custody appeared while paused: %d %v", rows, err)
	}
}

func TestPolicyChangeSinceTheRunDoesNotBlockAdoption(t *testing.T) {
	// Effective-policy freshness guards a launch; adoption authorizes none.
	ctx := context.Background()
	f := newAdoptionFixture(t)
	if _, err := f.store.RevisePolicy(ctx, RevisePolicyRequest{RequestID: uuid.NewString(), Repo: f.record.Scope.Repo, GrantID: f.root.ID,
		Rules: &PolicyRules{OptionalPercent: 10, OptionalMaxTokens: 6000}, Reason: "Adopt the existing optional memory budget as governed policy"}); err != nil {
		t.Fatal(err)
	}
	// Registering a new run's context is still refused for a receipt made under the earlier policy...
	_, err := f.store.RegisterManagedContext(ctx, ManagedContextRequest{OwnershipID: uuid.NewString(), RequestID: uuid.NewString(), ReceiptID: f.pkg.ReceiptID,
		Directory: f.request.Directory, DirectoryDevice: "1", DirectoryInode: "2"})
	requireCode(t, err, "STALE_PACKAGE")
	// ...while adopting the copy that already exists is not.
	if _, err = f.store.AdoptManagedContext(ctx, f.request); err != nil {
		t.Fatalf("a changed repository policy blocked adopting an existing copy: %v", err)
	}
}
