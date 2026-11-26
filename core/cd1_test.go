package core

import (
	"strings"
	"testing"
	"time"
)

func TestCD1StructuralInvariantsAndCounterexamples(t *testing.T) {
	validReq := strings.Join([]string{
		"cd1 req cairn CAIRN-325 pending act=construct ttl=1h",
		"ref a1=agent/c2ba44eb-8da7-41f4-8dfd-0dd912ab0c04",
		"auth root@2026-11-09; scope=P1..P5",
		"base main@d1c78f8cbffc01a19bbc5469f4fca0e65210e9f6; clean",
		"rw rust/semantic-tokenizer/",
		"no fake_backend",
		"req build tokenizers=0.23.2 for a1",
	}, "\n")
	if res := ValidateCD1Message(validReq, CD1ValidateOptions{}); !res.Valid {
		t.Fatalf("expected validReq to pass, got %v", res.Errors)
	}

	// Count-free chk pass & verified with unrelated blk
	verifiedWithBlk := strings.Join([]string{
		"cd1 notice striatum-next e512025-oscillation-recovery verified act=review",
		"chk make_check pass",
		"blk E512025_UNRESOLVED",
	}, "\n")
	if res := ValidateCD1Message(verifiedWithBlk, CD1ValidateOptions{}); !res.Valid {
		t.Fatalf("expected verified with passing chk and unrelated blk to pass, got %v", res.Errors)
	}

	badCases := []struct {
		name string
		msg  string
		want string
	}{
		{"verified_no_chk", "cd1 notice p t verified\nobs ready", "VERIFIED_WITHOUT_PASSING_CHK"},
		{"verified_untested", "cd1 resp p t verified\nchk pytest 0/0/0; untested", "VERIFIED_WITHOUT_PASSING_CHK"},
		{"verified_fail", "cd1 resp p t verified\nchk pytest 3/1/0", "VERIFIED_WITHOUT_PASSING_CHK"},
		{"req_expand_no_auth", "cd1 req p t pending\nreq work\nexpand re=387bd21d-6a8f-4b40-8835-7be2d2f42539 why=missing_ref", "MISSING_AUTHORITY"},
		{"dup_hdr_act", "cd1 notice p t done act=work act=none\nobs ready", "DUPLICATE_HEADER_KEY"},
		{"act_none_req", "cd1 notice p t active act=none\nauth root@2026-11-25\nreq work", "ACT_NONE_CONFLICT"},
		{"act_none_blk_q", "cd1 notice p t active act=none\nblk wait\nq why", "ACT_NONE_CONFLICT"},
		{"partial_opened", "cd1 resp p t partial\nobs opened window", "PARTIAL_WITHOUT_REMAINDER"},
		{"partial_unmet", "cd1 resp p t partial\nobs no unmet items", "PARTIAL_WITHOUT_REMAINDER"},
		{"trunc_ref", "cd1 resp p t done\nref d1=eaa4a820\nobs @delivery:d1", "TRUNCATED_REF_TARGET"},
		{"bare_at", "cd1 resp p t done\nobs proof @e1", "UNBOUND_HANDLE"},
		{"multi_re", "cd1 resp p t done re=ev1\nobs ok", "UNBOUND_HANDLE"},
		{"trunc_sha", "cd1 req p t pending\nauth root@2026-11-25\nbase main@d1c78f8; clean\nreq go", "TRUNCATED_COMMIT_SHA"},
		{"lt_order", "cd1 notice p t active\nho a<b -> c", "UNDEFINED_ORDER_OPERATOR"},
	}
	for _, tc := range badCases {
		res := ValidateCD1Message(tc.msg, CD1ValidateOptions{})
		if res.Valid {
			t.Fatalf("%s: expected failure (%s), got valid", tc.name, tc.want)
		}
		joined := strings.Join(res.Errors, "; ")
		if !strings.Contains(joined, tc.want) {
			t.Fatalf("%s: expected %q in errors, got %q", tc.name, tc.want, joined)
		}
	}
}

func TestCD1ParentContextAndEnvelopeCrossCheck(t *testing.T) {
	t1 := strings.Join([]string{
		"cd1 req cairn CAIRN-401 pending act=work",
		"ref e1=7c3a1b9e-4d2f-4a8c-9e1b-0f2a3c4d5e6f; a1=agent/11223344-5566-4777-8888-99aabbccdde0",
		"auth root@2026-11-20",
		"req run unit tests",
	}, "\n")
	t2 := "cd1 resp cairn CAIRN-401 verified re=e1 to=a1\nchk unit 18/0/0"

	now := time.Date(2026, 11, 25, 12, 0, 0, 0, time.UTC)
	ctx, err := BuildCD1ParentContext([]string{t1}, "thread-401", 2, "2026-11-25T13:00:00Z", &now)
	if err != nil {
		t.Fatal(err)
	}
	if res := ValidateCD1Message(t2, CD1ValidateOptions{ParentContext: ctx, ThreadID: "thread-401", Now: &now}); !res.Valid {
		t.Fatalf("expected valid L1 under ParentContext, got %v", res.Errors)
	}
	rendered, err := RenderCD1Human(t2, CD1ValidateOptions{ParentContext: ctx, ThreadID: "thread-401", Now: &now})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(rendered, "7c3a1b9e-4d2f-4a8c-9e1b-0f2a3c4d5e6f") || !strings.Contains(rendered, "agent/11223344-5566-4777-8888-99aabbccdde0") {
		t.Fatalf("expected inherited handles resolved in rendered prose, got:\n%s", rendered)
	}

	later := now.Add(2 * time.Hour)
	if res := ValidateCD1Message(t2, CD1ValidateOptions{ParentContext: ctx, ThreadID: "thread-401", Now: &later}); res.Valid || !strings.Contains(strings.Join(res.Errors, ";"), "EXPIRED_PARENT_CONTEXT") {
		t.Fatalf("expected EXPIRED_PARENT_CONTEXT, got valid=%v errors=%v", res.Valid, res.Errors)
	}
	if res := ValidateCD1Message(t2, CD1ValidateOptions{ParentContext: ctx, ThreadID: "other-thread", Now: &now}); res.Valid || !strings.Contains(strings.Join(res.Errors, ";"), "CROSS_THREAD_HANDLE_REUSE") {
		t.Fatalf("expected CROSS_THREAD_HANDLE_REUSE, got valid=%v errors=%v", res.Valid, res.Errors)
	}

	// Envelope action cross-check + 4-token envelope-derived header
	if res := ValidateCD1Message("cd1 notice cairn T1 done act=info\nobs ok", CD1ValidateOptions{EnvelopeKind: "notice", EnvelopeAction: "decide"}); res.Valid || !strings.Contains(strings.Join(res.Errors, ";"), "ENVELOPE_ACTION_MISMATCH") {
		t.Fatalf("expected ENVELOPE_ACTION_MISMATCH, got valid=%v errors=%v", res.Valid, res.Errors)
	}
	if res := ValidateCD1Message("cd1 cairn T1 done\nobs ok", CD1ValidateOptions{EnvelopeKind: "notice", EnvelopeAction: "none"}); !res.Valid {
		t.Fatalf("expected 4-token envelope-derived header to pass, got %v", res.Errors)
	}
	if proto, ok := DetectCompactProtocol("Plain prose message."); ok || proto != "" {
		t.Fatalf("expected plain prose not detected as compact protocol, got %q %v", proto, ok)
	}
}
