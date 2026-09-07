package core

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

// One root per test cluster; every test's privileged work has its own repository.
func testOperator(t *testing.T) (*Store, Grant) {
	t.Helper()
	s := testStore(t, Channel{Principal: "operator:tests", Operator: true})
	root, err := s.Bootstrap(context.Background(), BootstrapRequest{"82bf5bce-dc6c-4d03-a49f-1677bdcc9a32", "Install synthetic test operator root"})
	if err != nil {
		t.Fatal(err)
	}
	return s, root
}
func projectNote(repo string) Draft {
	d := note()
	d.Scope = Scope{repo, "*", "*"}
	d.Body = "Build failed with fixture_error; inspect the compiler first."
	return d
}
func testEvidence(t *testing.T, s *Store, repo string) Evidence {
	t.Helper()
	e, err := s.CaptureEvidence(context.Background(), EvidenceRequest{uuid.NewString(), repo, "synthetic exit code 1", "test fixture", "local"})
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func TestPromotionAuthorityAndCorrection(t *testing.T) {
	ctx := context.Background()
	operator, root := testOperator(t)
	repo := uuid.NewString()
	writer := testStore(t, Channel{Principal: "agent:" + repo, Repo: repo})
	r, err := writer.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	e := testEvidence(t, operator, repo)
	grant, err := operator.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: writer.channel.Principal, Repo: repo, Capabilities: []string{"promote"}, Reason: "Test an explicitly granted self-promotion refusal"})
	if err != nil {
		t.Fatal(err)
	}
	req := PromoteRequest{uuid.NewString(), r.RecordID, 1, grant.ID, []string{e.ID}, "Promote this independently supported claim"}
	_, err = writer.Promote(ctx, req)
	requireCode(t, err, "SELF_PROMOTION_DENIED")
	req.RequestID = uuid.NewString()
	req.GrantID = root.ID
	_, err = writer.Promote(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
	promoted, err := operator.Promote(ctx, req)
	if err != nil || promoted.Class != "B" || promoted.Version != 2 {
		t.Fatalf("promotion %+v %v", promoted, err)
	}
	_, err = operator.Edit(ctx, EditRequest{uuid.NewString(), r.RecordID, 2, r.Draft})
	requireCode(t, err, "AUTHORITY_DENIED")
	correction := r.Draft
	correction.Body = "Use compiler diagnostics before changing dependencies; fixture_error corrected."
	next, err := operator.Correct(ctx, CorrectRequest{uuid.NewString(), r.RecordID, 2, root.ID, correction, []string{e.ID}, "Correct the earlier overbroad advice"})
	if err != nil || next.Version != 3 {
		t.Fatalf("correction %+v %v", next, err)
	}
	var auditCount int
	if err = operator.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.record_authority WHERE record_id=$1`, r.RecordID).Scan(&auditCount); err != nil || auditCount != 2 {
		t.Fatalf("audit %d %v", auditCount, err)
	}
}

func TestGrantContainmentAndRevocation(t *testing.T) {
	ctx := context.Background()
	operator, root := testOperator(t)
	repo := uuid.NewString()
	service := testStore(t, Channel{Principal: "service:" + repo, Repo: repo})
	expiry := time.Now().Add(time.Hour)
	parent, err := operator.Grant(ctx, GrantRequest{uuid.NewString(), root.ID, service.channel.Principal, repo, []string{"grant", "issue", "revoke"}, &expiry, "Delegate narrowly scoped instruction authoring"})
	if err != nil {
		t.Fatal(err)
	}
	childReq := GrantRequest{uuid.NewString(), parent.ID, "child:" + repo, repo, []string{"issue"}, nil, "Delegate a bounded child instruction grant"}
	_, err = service.Grant(ctx, childReq)
	requireCode(t, err, "AUTHORITY_DENIED")
	childReq.ExpiresAt = &expiry
	child, err := service.Grant(ctx, childReq)
	if err != nil {
		t.Fatal(err)
	}
	childActor := testStore(t, Channel{Principal: child.Principal, Repo: repo})
	draft := projectNote(repo)
	draft.Kind = "instruction"
	issued, err := childActor.Issue(ctx, IssueRequest{uuid.NewString(), draft, child.ID, true, false, "workflow", "Issue a scoped workflow instruction"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = operator.RevokeGrant(ctx, RevokeGrantRequest{uuid.NewString(), parent.ID, root.ID, 1, "Revoke the parent and its dependent authority"}); err != nil {
		t.Fatal(err)
	}
	_, err = childActor.Issue(ctx, IssueRequest{uuid.NewString(), draft, child.ID, true, false, "another", "Must refuse after parent revocation"})
	requireCode(t, err, "AUTHORITY_DENIED")
	pkg, err := operator.Compile(ctx, CompileRequest{nil, uuid.NewString(), Scope{repo, "task", "run"}, "", "context", 32000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range pkg.Semantic.Selected {
		if entry.Record.RecordID == issued.RecordID {
			t.Fatal("revoked instruction selected")
		}
	}
}

func TestRevocationRaceHasValidSerialOrder(t *testing.T) {
	ctx := context.Background()
	operator, root := testOperator(t)
	repo := uuid.NewString()
	actor := testStore(t, Channel{Principal: "service:race:" + repo})
	grant, err := operator.Grant(ctx, GrantRequest{RequestID: uuid.NewString(), ParentID: root.ID, Principal: actor.channel.Principal, Repo: repo, Capabilities: []string{"issue"}, Reason: "Grant test authority for a revocation race"})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Kind = "instruction"
	var issueErr, revokeErr error
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		_, issueErr = actor.Issue(ctx, IssueRequest{uuid.NewString(), draft, grant.ID, true, false, "race", "Attempt issuance concurrently with revocation"})
	}()
	go func() {
		defer wg.Done()
		_, revokeErr = operator.RevokeGrant(ctx, RevokeGrantRequest{uuid.NewString(), grant.ID, root.ID, 1, "Revoke authority concurrently with issuance"})
	}()
	wg.Wait()
	if revokeErr != nil {
		t.Fatal(revokeErr)
	}
	if issueErr != nil && Code(issueErr) != "AUTHORITY_DENIED" {
		t.Fatal(issueErr)
	}
	_, err = actor.Issue(ctx, IssueRequest{uuid.NewString(), draft, grant.ID, true, false, "after", "Attempt issuance strictly after revocation"})
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestAuditCannotBeRewritten(t *testing.T) {
	s, _ := testOperator(t)
	_, err := s.pool.Exec(context.Background(), `UPDATE cairn.authority_event SET reason='rewritten history'`)
	if err == nil {
		t.Fatal("authority audit rewrite accepted")
	}
}
