package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math/rand/v2"
	"os"
	"strconv"
	"testing"

	"github.com/google/uuid"
)

// Used only by scripts/measure-auth-retrieval.py, which owns a disposable cluster.
func TestAuthScaleSeed(t *testing.T) {
	if os.Getenv("CAIRN_AUTH_SCALE") != "1" {
		t.Skip("opt-in synthetic authenticated scale fixture")
	}
	from, err := strconv.Atoi(os.Getenv("CAIRN_SCALE_FROM"))
	if err != nil {
		t.Fatal(err)
	}
	to, err := strconv.Atoi(os.Getenv("CAIRN_SCALE_TO"))
	if err != nil || from < 0 || to <= from || to > 10000 {
		t.Fatal("invalid seed bounds")
	}
	repo := "fixture:authenticated-scale"
	s := testStore(t, Channel{Principal: "scale-seeder", Repo: repo})
	ctx := context.Background()
	r := rand.New(rand.NewPCG(235, 116))
	zipf := rand.NewZipf(r, 1.1, 1, uint64(len(scaleCommon)+3000-1))
	hash := sha256.New()
	kinds := []string{"note", "lesson", "procedure", "decision"}
	for i := 0; i < to; i++ {
		body := scaleBody(r, zipf, i)
		hash.Write([]byte(body))
		hash.Write([]byte{0})
		if i < from {
			continue
		}
		_, err = s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: kinds[i%4], Body: body, Sensitivity: "shareable", Scope: Scope{repo, "*", "*"}, ClaimType: "self"}})
		if err != nil {
			t.Fatal(err)
		}
	}
	if from == 0 {
		op, grant := testOperator(t)
		d := projectNote(repo)
		d.Sensitivity = "shareable"
		d.Kind = "instruction"
		d.Body = "REQUIRED_SCALE_CONTEXT: synthetic guidance must remain complete."
		if _, err = op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: d, GrantID: grant.ID, Mandatory: true, PolicyKey: "scale-required", Reason: "Required fixture context"}); err != nil {
			t.Fatal(err)
		}
		for _, private := range []bool{true, false} {
			d := projectNote(repo)
			d.Sensitivity = "shareable"
			d.Body = "EXCLUDED_SCALE_CONTEXT retry backoff scheduler postgres migration rollback procedure bounded window w2871 w1450 w0999"
			if private {
				d.Sensitivity = "local"
			} else {
				d.Scope.TaskID = "unrelated-task"
			}
			if _, err = s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d}); err != nil {
				t.Fatal(err)
			}
		}
	}
	data, err := json.Marshal(map[string]any{"notes": to, "body_sha256": hex.EncodeToString(hash.Sum(nil)), "extra_controls": 3})
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(os.Getenv("CAIRN_SCALE_SEED_RESULT"), data, 0600); err != nil {
		t.Fatal(err)
	}
}
