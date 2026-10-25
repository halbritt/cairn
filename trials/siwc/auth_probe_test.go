package siwc

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestAuthProbeDefaultIsOffline(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "bindings.json")
	called := false
	_, err := RunAuthorizationProbe(context.Background(), AuthProbeOptions{
		Alias: "owner", BindingPath: path,
		OpenBrowser: func(string) error { called = true; return nil },
	})
	if err == nil || !strings.Contains(err.Error(), "offline by default") {
		t.Fatalf("default run = %v, want offline refusal", err)
	}
	if called {
		t.Fatal("default run opened a browser")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("default run created binding path: %v", err)
	}
}

func TestAuthorizationProbeUsesStubFlowAndStoresNoCredentials(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "private", "bindings.json")
	var nonce string
	var responseCalls int
	transport := roundTripFunc(func(req *http.Request) (*http.Response, error) {
		switch {
		case req.URL.Host == "auth.openai.com" && req.URL.Path == "/api/accounts/oauth/token":
			body, _ := io.ReadAll(req.Body)
			form, err := url.ParseQuery(string(body))
			if err != nil || form.Get("client_id") != "oaiapp_probe" || form.Get("code") != "fixture-code" || form.Get("code_verifier") == "" {
				t.Fatalf("unexpected token exchange form: %v", err)
			}
			idToken := signedIDToken(t, key, nonce, "opaque-subject", "oaiapp_probe")
			return jsonResponse(http.StatusOK, map[string]any{"access_token": "access-secret-fixture", "refresh_token": "refresh-secret-fixture", "id_token": idToken, "token_type": "Bearer", "scope": strings.Join(requiredScopes, " "), "expires_in": 3600}), nil
		case req.URL.Host == "auth.openai.com" && req.URL.Path == "/.well-known/openid-configuration":
			return jsonResponse(http.StatusOK, map[string]string{"issuer": Issuer, "jwks_uri": "https://auth.openai.com/jwks"}), nil
		case req.URL.Host == "auth.openai.com" && req.URL.Path == "/jwks":
			pub := key.PublicKey
			return jsonResponse(http.StatusOK, map[string]any{"keys": []any{map[string]any{"kty": "RSA", "kid": "test-key", "use": "sig", "alg": "RS256", "n": base64.RawURLEncoding.EncodeToString(pub.N.Bytes()), "e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(pub.E)).Bytes())}}}), nil
		case req.URL.Host == "api.openai.com" && req.URL.Path == "/v1/models":
			if req.Header.Get("Authorization") != "Bearer access-secret-fixture" {
				t.Fatal("model catalog did not use the in-memory access token")
			}
			return jsonResponse(http.StatusOK, map[string]any{"models": []any{map[string]string{"slug": AuthProbeModel, "visibility": "list"}}}), nil
		case req.URL.Host == "api.openai.com" && req.URL.Path == "/v1/responses":
			responseCalls++
			body, _ := io.ReadAll(req.Body)
			var payload map[string]any
			if err := json.Unmarshal(body, &payload); err != nil || payload["model"] != AuthProbeModel || payload["store"] != false || payload["stream"] != true {
				t.Fatalf("unexpected bounded probe request: %s", body)
			}
			return &http.Response{StatusCode: http.StatusOK, Header: http.Header{"Content-Type": []string{"text/event-stream"}}, Body: io.NopCloser(strings.NewReader("data: {\"type\":\"response.completed\",\"response\":{\"id\":\"resp_probe\",\"model\":\"gpt-6-luna\",\"status\":\"completed\",\"output\":[{\"type\":\"message\",\"content\":[{\"type\":\"output_text\",\"text\":\"OK\"}]}],\"usage\":{\"input_tokens\":2,\"output_tokens\":1,\"total_tokens\":3}}}\n\n"))}, nil
		default:
			t.Fatalf("unexpected request: %s", req.URL)
			return nil, nil
		}
	})
	client := &http.Client{Transport: transport}
	result, err := RunAuthorizationProbe(context.Background(), AuthProbeOptions{
		Alias: "codex-a", BindingPath: path, Authorize: true, ProbeEntitlement: true,
		OAuthHTTPClient: client, EntitlementTimeout: 5 * time.Second,
		OpenBrowser: func(raw string) error {
			u, err := url.Parse(raw)
			if err != nil {
				return err
			}
			q := u.Query()
			nonce = q.Get("nonce")
			callback := q.Get("redirect_uri") + "?code=fixture-code&state=" + url.QueryEscape(q.Get("state")) + "&client_id=oaiapp_probe"
			resp, err := http.Get(callback)
			if err != nil {
				return err
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				return errUnexpectedCallback(resp.StatusCode)
			}
			return nil
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Subject != "opaque-subject" || result.ClientID != "oaiapp_probe" || !result.ModelListed || !result.EntitlementProbe || result.Usage.TotalTokens != 3 || responseCalls != 1 {
		t.Fatalf("unexpected result or request count: %+v responses=%d", result, responseCalls)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), "access-secret-fixture") || strings.Contains(string(data), "refresh-secret-fixture") || strings.Contains(string(data), "id_token") || strings.Contains(string(data), "scope") {
		t.Fatalf("binding persisted credentials or granted scopes: %s", data)
	}
	var bindings AuthBindingFile
	if err := json.Unmarshal(data, &bindings); err != nil {
		t.Fatal(err)
	}
	if bindings.HostID == "" || len(bindings.Accounts) != 1 || bindings.Accounts[0] != (AccountBinding{Alias: "codex-a", Subject: "opaque-subject", ClientID: "oaiapp_probe"}) {
		t.Fatalf("unexpected stored nonsecret binding: %+v", bindings)
	}
	if mode := fileMode(t, path); mode != 0600 {
		t.Fatalf("binding mode = %04o, want 0600", mode)
	}
	if mode := fileMode(t, filepath.Dir(path)); mode != 0700 {
		t.Fatalf("binding directory mode = %04o, want 0700", mode)
	}
	hostID := bindings.HostID
	if err := RemoveAccountBinding(path, "codex-a"); err != nil {
		t.Fatal(err)
	}
	updated, exists, err := readBindings(path)
	if err != nil || !exists || updated.HostID != hostID || len(updated.Accounts) != 0 {
		t.Fatalf("explicit alias removal changed host identity or left account binding: %+v err=%v", updated, err)
	}
}

func TestBindingPathRejectsCheckoutAndSymlinkAncestors(t *testing.T) {
	root := t.TempDir()
	checkout := filepath.Join(root, "checkout")
	if err := os.Mkdir(checkout, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(checkout, ".git"), []byte("gitdir: elsewhere"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveBindingPath(filepath.Join(checkout, "private", "bindings.json")); err == nil || !strings.Contains(err.Error(), "outside every Git checkout") {
		t.Fatalf("checkout path accepted: %v", err)
	}
	target := filepath.Join(root, "target")
	if err := os.Mkdir(target, 0700); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "link")
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	if err := preparePrivatePath(filepath.Join(link, "bindings.json")); err == nil || !strings.Contains(err.Error(), "symlink") {
		t.Fatalf("symlink ancestor accepted: %v", err)
	}
}

func TestReadBindingsRejectsTrailingJSONAndDuplicateAccountAssociations(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "bindings.json")
	base := AuthBindingFile{Version: 1, HostID: "urn:uuid:123e4567-e89b-42d3-a456-426614174000"}
	encoded, err := json.Marshal(base)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(encoded, []byte(` {}`)...), 0600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := readBindings(path); err == nil || !strings.Contains(err.Error(), "trailing JSON") {
		t.Fatalf("trailing JSON accepted: %v", err)
	}

	for _, tc := range []struct {
		name     string
		accounts []AccountBinding
	}{
		{name: "duplicate subject", accounts: []AccountBinding{{Alias: "a", Subject: "same-sub", ClientID: "oaiapp_a"}, {Alias: "b", Subject: "same-sub", ClientID: "oaiapp_b"}}},
		{name: "duplicate client ID", accounts: []AccountBinding{{Alias: "a", Subject: "sub-a", ClientID: "oaiapp_same"}, {Alias: "b", Subject: "sub-b", ClientID: "oaiapp_same"}}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			file := AuthBindingFile{Version: 1, HostID: base.HostID, Accounts: tc.accounts}
			data, err := json.Marshal(file)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
			if _, _, err := readBindings(path); err == nil || !strings.Contains(err.Error(), "invalid account metadata") {
				t.Fatalf("duplicate account association accepted: %v", err)
			}
		})
	}
}

func TestEntitlementResultRequiresRequestedModelAndConsistentUsage(t *testing.T) {
	valid := Result{ServedModel: AuthProbeModel, Usage: Usage{InputTokens: 2, OutputTokens: 1, TotalTokens: 3}}
	if err := validateEntitlementResult(valid); err != nil {
		t.Fatalf("valid inference rejected: %v", err)
	}
	wrongModel := valid
	wrongModel.ServedModel = "another-model"
	if err := validateEntitlementResult(wrongModel); err == nil {
		t.Fatal("wrong served model accepted")
	}
	badUsage := valid
	badUsage.Usage.TotalTokens = 5
	if err := validateEntitlementResult(badUsage); err == nil {
		t.Fatal("inconsistent usage accepted")
	}
}

func fileMode(t *testing.T, path string) os.FileMode {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info.Mode().Perm()
}

type statusError int

func (e statusError) Error() string { return "callback status " + strconv.Itoa(int(e)) }

func errUnexpectedCallback(status int) error { return statusError(status) }
