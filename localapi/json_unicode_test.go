package localapi_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"path/filepath"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestEvidenceJSONRejectsLossyUnicodeBeforeCapture(t *testing.T) {
	client, _, repo, home := authenticatedHost(t, "agent", "hosted", nil)
	ctx := context.Background()
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "unix", filepath.Join(home, "api.sock"))
	}}
	defer transport.CloseIdleConnections()
	raw := &http.Client{Transport: transport, Timeout: 10 * time.Second}
	for _, source := range []string{string([]byte{'"', 0xff, '"'}), `"\ud800"`, `"\udc00"`, `"\ud800x"`} {
		id := uuid.NewString()
		payload := []byte(fmt.Sprintf(`{"request_id":%q,"repo":%q,"source":"selected source","body":%s}`, id, repo, source))
		request, err := http.NewRequestWithContext(ctx, "POST", "http://cairn/v1/evidence", bytes.NewReader(payload))
		if err != nil {
			t.Fatal(err)
		}
		request.Header.Set("Authorization", "Bearer synthetic-observer-token")
		response, err := raw.Do(request)
		if err != nil {
			t.Fatal(err)
		}
		var envelope struct {
			Status string `json:"status"`
		}
		err = json.NewDecoder(response.Body).Decode(&envelope)
		response.Body.Close()
		if err != nil {
			t.Fatal(err)
		}
		if response.StatusCode != 400 || envelope.Status != "INVALID_REQUEST" {
			t.Fatalf("lossy Unicode source accepted: HTTP%d status=%s", response.StatusCode, envelope.Status)
		}
		// A refusal must not capture replacement bytes or reserve this identity.
		text := "Selected valid source: é 😀 �\r\n"
		var saved core.Evidence
		err = client.Call(ctx, "evidence", core.EvidenceRequest{RequestID: id, Repo: repo, Source: "selected source", Body: text}, &saved)
		digest := sha256.Sum256([]byte(text))
		if err != nil || saved.Digest != hex.EncodeToString(digest[:]) {
			t.Fatalf("refusal consumed identity or changed valid Unicode: %+v %v", saved, err)
		}
	}
}
