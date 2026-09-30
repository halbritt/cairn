package core

import (
	"fmt"
	"github.com/google/uuid"
	"strings"
	"testing"
	"time"
)

func TestRecoverySegmentsSplitCustodyAcrossAnchoredRecords(t *testing.T) {
	store := restoreTestStore(t)
	root := recoveryRoot(t, store)
	full := RecoveryRecord{Schema: recoverySchema, RootGrantID: root.ID, CapturedAt: time.Now().UTC()}
	digest := strings.Repeat("a", 64)
	for i := 1; i <= 10001; i++ {
		full.Audit = append(full.Audit, AuditMember{EventID: fmt.Sprintf("00000000-0000-4000-8000-%012d", i), Digest: digest})
	}
	w := RecoveryWithdrawal{Kind: "forget", SubjectID: uuid.NewString(), Repo: "fixture", EventID: full.Audit[0].EventID}
	full.Withdrawals = []RecoveryWithdrawal{w}
	for i := 0; i < 2001; i++ {
		full.Contexts = append(full.Contexts, RecoveryContext{RecordID: w.SubjectID, ManagedContext: ManagedContext{OwnershipID: uuid.NewString(), ReceiptID: uuid.NewString(), Directory: "/fixture/context", DirectoryDevice: "1", DirectoryInode: "2", BodySHA256: digest}})
	}
	set, err := splitRecoveryStagedForTest(t, store, full)
	if err != nil {
		t.Fatal(err)
	}
	total := 0
	for _, r := range set {
		if err := r.validate(); err != nil {
			t.Fatal(err)
		}
		total += len(r.Contexts)
	}
	if total != 2001 {
		t.Fatalf("retained %d custody references, want2001", total)
	}
}
