package localapi_test

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAuthenticatedBinaryEvidencePreservesBytesAndRetry(t *testing.T) {
	client, _, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	body := []byte{0, 0xff, 0xfe, '\r', '\n', 0xc3, 0xa9}
	encoded := base64.StdEncoding.EncodeToString(body)
	request := map[string]string{"request_id": uuid.NewString(), "repo": repo, "body_base64": encoded, "source": "selected binary source", "sensitivity": "shareable"}
	var got, retry core.Evidence
	if err := client.Call(ctx, "evidence", request, &got); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(body)
	if got.Digest != hex.EncodeToString(digest[:]) || got.Witness != "testimony" {
		t.Fatalf("source or attribution changed: %+v", got)
	}
	if err := client.Call(ctx, "evidence", request, &retry); err != nil || retry != got {
		t.Fatalf("retry: %+v %v", retry, err)
	}
	request["body_base64"] = base64.StdEncoding.EncodeToString([]byte{0, 0xff, 0xfd, '\r', '\n', 0xc3, 0xa9})
	if err := client.Call(ctx, "evidence", request, &retry); core.Code(err) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("different source reused identity: %v", err)
	}
	store, err := core.Open(ctx, os.Getenv("CAIRN_TEST_DATABASE_URL"), core.Channel{Principal: "host:test", Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	stored, err := store.ReadEvidence(ctx, got.ID)
	if err != nil || stored.Body != "" || stored.BodyBase64 != encoded {
		t.Fatalf("retained bytes differ: %+v %v", stored, err)
	}
}

func TestAuthenticatedBinaryEvidenceBoundsAndRepresentation(t *testing.T) {
	client, _, repo, _ := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	var got core.Evidence
	request := map[string]string{"request_id": uuid.NewString(), "repo": repo, "source": "bounded binary source"}
	for name, encoding := range map[string]string{
		"empty": "", "invalid": "!!!!", "unpadded": "/w", "pad_bits": "/x==", "newline": "/w==\n",
		"oversized_decoded": base64.StdEncoding.EncodeToString(make([]byte, 1048577)),
	} {
		t.Run(name, func(t *testing.T) {
			request["body_base64"] = encoding
			if err := client.Call(ctx, "evidence", request, &got); core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("accepted invalid binary source: %v", err)
			}
		})
	}
	request["body_base64"], request["body"] = "/w==", "two sources"
	if err := client.Call(ctx, "evidence", request, &got); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("ambiguous source: %v", err)
	}
	delete(request, "body")
	// Refused representations must not reserve this UUID; exercise the exact decoded cap.
	body := make([]byte, 1048576)
	for i := range body {
		body[i] = byte(i)
	}
	request["body_base64"] = base64.StdEncoding.EncodeToString(body)
	if err := client.Call(ctx, "evidence", request, &got); err != nil {
		t.Fatal(err)
	}
	store, err := core.Open(ctx, os.Getenv("CAIRN_TEST_DATABASE_URL"), core.Channel{Principal: "host:test", Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	stored, err := store.ReadEvidence(ctx, got.ID)
	if err != nil || stored.BodyBase64 != request["body_base64"] || stored.Sensitivity != "local" {
		t.Fatalf("limit or local default changed: %v", err)
	}
	request["request_id"], request["repo"] = uuid.NewString(), uuid.NewString()
	if err := client.Call(ctx, "evidence", request, &got); core.Code(err) != "AUTHORITY_DENIED" {
		t.Fatalf("cross-repository capture: %v", err)
	}
}
