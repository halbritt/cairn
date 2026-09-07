package core

import (
	"context"
	"errors"
	"github.com/google/uuid"
	"testing"
)

func TestPolicyRefusalIsDurableBoundedAndDoesNotRetainQuery(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Kind = "instruction"
	if _, err := op.Issue(ctx, IssueRequest{uuid.NewString(), d, root.ID, true, true, "runtime", "Require unsupported mediation for refusal fixture"}); err != nil {
		t.Fatal(err)
	}
	request := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "private-transient-refusal-canary", Purpose: "context", AvailableTokens: 64000}
	_, err := op.Compile(ctx, request, Destination{"local", true})
	requireCode(t, err, "POLICY_UNENFORCEABLE")
	var failure *Error
	if !errors.As(err, &failure) || failure.RefusalID == "" {
		t.Fatalf("refusal lacks durable reference: %v", err)
	}
	refusal, err := op.Refusal(ctx, failure.RefusalID)
	if err != nil || refusal.RequestID != request.RequestID || refusal.Code != "POLICY_UNENFORCEABLE" || refusal.QuerySHA256 == "" {
		t.Fatalf("refusal: %+v %v", refusal, err)
	}
	_, err = op.Compile(ctx, request, Destination{"local", true})
	var retry *Error
	if !errors.As(err, &retry) || retry.RefusalID != failure.RefusalID {
		t.Fatal("refusal retry duplicated observation")
	}
	var leak bool
	if err = op.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.refusal WHERE detail::text LIKE '%private-transient-refusal-canary%')`).Scan(&leak); err != nil || leak {
		t.Fatal("refusal retained raw query")
	}
	outsider := testStore(t, Channel{Principal: "refusal-outsider", Repo: repo})
	_, err = outsider.Refusal(ctx, failure.RefusalID)
	requireCode(t, err, "AUTHORITY_DENIED")
}
func TestOpenConflictRetractionRefusalIsDurable(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	a, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	b, err := op.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = op.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{a.RecordID, b.RecordID}, "Keep unresolved dispute visible"}); err != nil {
		t.Fatal(err)
	}
	_, err = op.Retract(ctx, RetractRequest{RequestID: uuid.NewString(), RecordID: a.RecordID, ExpectedVersion: 1, GrantID: root.ID, Reason: "Attempt conflicting retraction"})
	var failure *Error
	if Code(err) != "OPEN_CONFLICT" || !errors.As(err, &failure) || failure.RefusalID == "" {
		t.Fatalf("missing durable conflict refusal: %v", err)
	}
	refusal, err := op.Refusal(ctx, failure.RefusalID)
	if err != nil || refusal.Operation != "retract" {
		t.Fatalf("refusal: %+v %v", refusal, err)
	}
}

func TestRefusalPersistenceFailureIsExplicit(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	d := projectNote(repo)
	d.Kind = "instruction"
	if _, err := op.Issue(ctx, IssueRequest{uuid.NewString(), d, root.ID, true, true, "unrecorded", "Require refusal persistence fault fixture"}); err != nil {
		t.Fatal(err)
	}
	_, err := op.pool.Exec(ctx, `CREATE FUNCTION cairn.test_refusal_write_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.caller='refusal-write-fault' THEN RAISE EXCEPTION 'synthetic refusal write failure'; END IF; RETURN NEW; END; $$;
 CREATE TRIGGER test_refusal_write_failure BEFORE INSERT ON cairn.refusal FOR EACH ROW EXECUTE FUNCTION cairn.test_refusal_write_failure()`)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := op.pool.Exec(context.Background(), `DROP TRIGGER test_refusal_write_failure ON cairn.refusal; DROP FUNCTION cairn.test_refusal_write_failure()`); err != nil {
			t.Error(err)
		}
	})
	caller := testStore(t, Channel{Principal: "refusal-write-fault", Repo: repo})
	_, err = caller.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	requireCode(t, err, "REFUSAL_UNRECORDED")
	var failure *Error
	if !errors.As(err, &failure) || failure.RefusalID != "" {
		t.Fatal("failed write claimed durable refusal identity")
	}
}
