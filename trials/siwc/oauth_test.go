package siwc

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestAuthorizationAttemptUsesLoopbackPKCEAndDynamicRegistration(t *testing.T) {
	listener, err := ListenCallback()
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if !strings.HasPrefix(listener.RedirectURI(), "http://127.0.0.1:") || !strings.HasSuffix(listener.RedirectURI(), "/auth/callback") {
		t.Fatalf("unexpected redirect URI %q", listener.RedirectURI())
	}
	attempt, err := NewAttempt(listener.RedirectURI(), "urn:uuid:123e4567-e89b-42d3-a456-426614174000", "Cairn Trial", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(attempt.State) < 32 || len(attempt.Nonce) < 32 || len(attempt.Verifier) < 43 {
		t.Fatal("OAuth state material is too short")
	}
	parsed, err := url.Parse(mustAuthURL(t, attempt))
	if err != nil {
		t.Fatal(err)
	}
	q := parsed.Query()
	if q.Get("client_id") != "dynamic_agent_client" || q.Get("agent_name_hint") != "Cairn Trial" || q.Get("ext_agent_host_id") != attempt.HostID {
		t.Fatalf("incorrect first registration parameters: %v", q)
	}
	if q.Get("redirect_uri") != listener.RedirectURI() || q.Get("resource") != Resource || q.Get("code_challenge_method") != "S256" {
		t.Fatalf("incorrect OAuth contract: %v", q)
	}
	want := sha256.Sum256([]byte(attempt.Verifier))
	if q.Get("code_challenge") != base64.RawURLEncoding.EncodeToString(want[:]) {
		t.Fatal("PKCE challenge does not match verifier")
	}
	if !hasScopes(q.Get("scope"), requiredScopes) {
		t.Fatalf("missing requested scopes: %q", q.Get("scope"))
	}

	response, err := http.Get(listener.RedirectURI() + "?code=opaque-code&state=" + url.QueryEscape(attempt.State) + "&client_id=oaiapp_fixture&scope=" + url.QueryEscape(strings.Join(requiredScopes, " ")))
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("callback status = %d", response.StatusCode)
	}
	callback, err := listener.Wait(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if callback.Code != "opaque-code" || callback.State != attempt.State || callback.ClientID != "oaiapp_fixture" {
		t.Fatalf("unexpected callback: %+v", callback)
	}
}

func TestExchangeBindsIssuedClientAndVerifiedAccountAndFailsClosedOnScopes(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	const subject = "account-subject-1"
	attempt := testAttempt(t, "", "")
	callback := Callback{Code: "one-time-code", State: attempt.State, ClientID: "oaiapp_fixture"}
	client := oauthClient(t, key, attempt.Nonce, subject, "oaiapp_fixture")
	creds, err := attempt.Exchange(context.Background(), client, callback)
	if err != nil {
		t.Fatal(err)
	}
	if creds.ClientID() != callback.ClientID || creds.Subject() != subject || !creds.validated || creds.accessToken != "access-in-memory" {
		t.Fatal("unexpected verified credential identity")
	}
	if !hasScopes(strings.Join(creds.scopes, " "), requiredScopes) {
		t.Fatal("validated credentials lost granted scopes")
	}

	wrong := testAttempt(t, "oaiapp_fixture", "account-subject-2")
	wrongCallback := Callback{Code: "one-time-code", State: wrong.State, ClientID: "oaiapp_other", Scope: strings.Join(requiredScopes, " ")}
	wrongClient := oauthClient(t, key, wrong.Nonce, subject, "oaiapp_fixture")
	if _, err := wrong.Exchange(context.Background(), wrongClient, wrongCallback); err == nil || !strings.Contains(err.Error(), "client ID") {
		t.Fatalf("client/account registration mismatch was accepted: %v", err)
	}
	wrongCallback.ClientID = "oaiapp_fixture"
	if _, err := wrong.Exchange(context.Background(), wrongClient, wrongCallback); err == nil || !strings.Contains(err.Error(), "selected account") {
		t.Fatalf("account subject mismatch was accepted: %v", err)
	}

	denied := testAttempt(t, "", "")
	deniedClient := oauthClientWith(t, key, denied.Nonce, subject, "oaiapp_fixture", "openid profile email")
	deniedCallback := Callback{Code: "one-time-code", State: denied.State, ClientID: "oaiapp_fixture", Scope: "openid profile email"}
	if _, err := denied.Exchange(context.Background(), deniedClient, deniedCallback); err == nil || !strings.Contains(err.Error(), "required scopes") {
		t.Fatalf("missing subscription scope did not fail closed: %v", err)
	}
	badState := Callback{Code: "one-time-code", State: "attacker-state", ClientID: "oaiapp_fixture", Scope: strings.Join(requiredScopes, " ")}
	if _, err := attempt.Exchange(context.Background(), client, badState); err == nil || !strings.Contains(err.Error(), "state mismatch") {
		t.Fatalf("state mismatch was accepted: %v", err)
	}
}

func TestExchangeRejectsTokenScopeAndNonceMismatch(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	attempt := testAttempt(t, "", "")
	client := oauthClientWith(t, key, attempt.Nonce+"bad", "subject", "oaiapp_fixture", "openid profile email")
	callback := Callback{Code: "code", State: attempt.State, ClientID: "oaiapp_fixture", Scope: strings.Join(requiredScopes, " ")}
	if _, err := attempt.Exchange(context.Background(), client, callback); err == nil || !strings.Contains(err.Error(), "required scopes") {
		t.Fatalf("token scope omission was accepted: %v", err)
	}
	client = oauthClient(t, key, attempt.Nonce+"bad", "subject", "oaiapp_fixture")
	if _, err := attempt.Exchange(context.Background(), client, callback); err == nil || !strings.Contains(err.Error(), "claims failed validation") {
		t.Fatalf("nonce mismatch was accepted: %v", err)
	}
}

func TestTokenRedirectNeverResendsAuthorizationCodeOrVerifier(t *testing.T) {
	attempt := testAttempt(t, "", "")
	requests := 0
	client := &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		requests++
		if req.URL.String() != TokenEndpoint {
			t.Fatalf("unexpected redirected request: %s", req.URL)
		}
		return &http.Response{StatusCode: http.StatusTemporaryRedirect, Header: http.Header{"Location": []string{"https://attacker.example/collect"}}, Body: io.NopCloser(strings.NewReader(""))}, nil
	})}
	_, err := attempt.Exchange(context.Background(), client, Callback{Code: "one-time-code", State: attempt.State, ClientID: "oaiapp_fixture"})
	if err == nil || requests != 1 {
		t.Fatalf("token redirect followed: requests=%d err=%v", requests, err)
	}
}

func TestExchangeRejectsWrongStateOnDenialAndInvalidIdentityTokens(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	callback := func(a *Attempt) Callback {
		return Callback{State: a.State, Code: "code", ClientID: "oaiapp_fixture", Scope: strings.Join(requiredScopes, " ")}
	}
	for _, tc := range []struct {
		name, tokenAudience, issuer, tokenType string
		tamper                                 bool
		want                                   string
	}{
		{name: "wrong audience", tokenAudience: "oaiapp_other", issuer: Issuer, tokenType: "Bearer", want: "claims failed validation"},
		{name: "wrong issuer", tokenAudience: "oaiapp_fixture", issuer: "https://wrong.example", tokenType: "Bearer", want: "claims failed validation"},
		{name: "tampered signature", tokenAudience: "oaiapp_fixture", issuer: Issuer, tokenType: "Bearer", tamper: true, want: "verification error"},
		{name: "wrong token type", tokenAudience: "oaiapp_fixture", issuer: Issuer, tokenType: "DPoP", want: "not a Bearer"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a := testAttempt(t, "", "")
			client := oauthClientSettings(t, key, a.Nonce, "subject", "oaiapp_fixture", tc.tokenAudience, tc.issuer, strings.Join(requiredScopes, " "), tc.tokenType, tc.tamper)
			if _, err := a.Exchange(context.Background(), client, callback(a)); err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("invalid OAuth result accepted: %v", err)
			}
		})
	}
	denied := testAttempt(t, "", "")
	if _, err := denied.Exchange(context.Background(), nil, Callback{State: "wrong", Error: "access_denied"}); err == nil || !strings.Contains(err.Error(), "state mismatch") {
		t.Fatalf("denial with wrong state accepted: %v", err)
	}
	if _, err := denied.Exchange(context.Background(), nil, Callback{State: denied.State, Error: "access_denied"}); err == nil || !strings.Contains(err.Error(), "authorization refused") {
		t.Fatalf("valid denied callback did not stop cleanly: %v", err)
	}
}

func TestLoopbackListenerRejectsMalformedCallback(t *testing.T) {
	listener, err := ListenCallback()
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	resp, err := http.Get(listener.RedirectURI() + "?state=first&state=second&code=code")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("malformed callback status = %d", resp.StatusCode)
	}
}

func testAttempt(t *testing.T, clientID, expectedSubject string) *Attempt {
	t.Helper()
	a, err := NewAttempt("http://127.0.0.1:12345/auth/callback", "urn:uuid:123e4567-e89b-42d3-a456-426614174000", "Cairn Trial", clientID, expectedSubject)
	if err != nil {
		t.Fatal(err)
	}
	return a
}

func mustAuthURL(t *testing.T, attempt *Attempt) string {
	t.Helper()
	u, err := attempt.AuthorizationURL()
	if err != nil {
		t.Fatal(err)
	}
	return u
}

func oauthClient(t *testing.T, key *rsa.PrivateKey, nonce, subject, audience string) *http.Client {
	return oauthClientWith(t, key, nonce, subject, audience, strings.Join(requiredScopes, " "))
}

func oauthClientWith(t *testing.T, key *rsa.PrivateKey, nonce, subject, audience, tokenScopes string) *http.Client {
	return oauthClientSettings(t, key, nonce, subject, audience, audience, Issuer, tokenScopes, "Bearer", false)
}

func oauthClientSettings(t *testing.T, key *rsa.PrivateKey, nonce, subject, exchangeAudience, tokenAudience, issuer, tokenScopes, tokenType string, tamper bool) *http.Client {
	t.Helper()
	public := key.PublicKey
	keySet := map[string]any{"keys": []any{map[string]any{"kty": "RSA", "kid": "test-key", "use": "sig", "alg": "RS256", "n": base64.RawURLEncoding.EncodeToString(public.N.Bytes()), "e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(public.E)).Bytes())}}}
	transport := roundTripFunc(func(req *http.Request) (*http.Response, error) {
		switch {
		case req.URL.Host == "auth.openai.com" && req.URL.Path == "/api/accounts/oauth/token":
			body, _ := io.ReadAll(req.Body)
			form, err := url.ParseQuery(string(body))
			if err != nil {
				return nil, err
			}
			if req.Method != http.MethodPost || form.Get("code_verifier") == "" || form.Get("redirect_uri") == "" || form.Get("resource") != Resource || form.Get("client_id") != exchangeAudience {
				return nil, fmt.Errorf("incorrect token exchange: %s", body)
			}
			idToken := signedIDTokenWithIssuer(t, key, nonce, subject, tokenAudience, issuer)
			if tamper {
				parts := strings.Split(idToken, ".")
				parts[2] = "AAAA"
				idToken = strings.Join(parts, ".")
			}
			return jsonResponse(http.StatusOK, map[string]any{"access_token": "access-in-memory", "refresh_token": "refresh-in-memory", "id_token": idToken, "token_type": tokenType, "scope": tokenScopes, "expires_in": 3600}), nil
		case req.URL.Host == "auth.openai.com" && req.URL.Path == "/.well-known/openid-configuration":
			return jsonResponse(http.StatusOK, map[string]string{"issuer": issuer, "jwks_uri": "https://auth.openai.com/jwks"}), nil
		case req.URL.Host == "auth.openai.com" && req.URL.Path == "/jwks":
			return jsonResponse(http.StatusOK, keySet), nil
		default:
			return nil, fmt.Errorf("unexpected OAuth request to %s", req.URL)
		}
	})
	return &http.Client{Transport: transport}
}

func signedIDToken(t *testing.T, key *rsa.PrivateKey, nonce, subject, audience string) string {
	return signedIDTokenWithIssuer(t, key, nonce, subject, audience, Issuer)
}

func signedIDTokenWithIssuer(t *testing.T, key *rsa.PrivateKey, nonce, subject, audience, issuer string) string {
	t.Helper()
	header, _ := json.Marshal(map[string]string{"alg": "RS256", "kid": "test-key", "typ": "JWT"})
	claims, _ := json.Marshal(map[string]any{"iss": issuer, "aud": audience, "sub": subject, "nonce": nonce, "exp": time.Now().Add(time.Hour).Unix()})
	message := base64.RawURLEncoding.EncodeToString(header) + "." + base64.RawURLEncoding.EncodeToString(claims)
	hash := sha256.Sum256([]byte(message))
	sig, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA256, hash[:])
	if err != nil {
		t.Fatal(err)
	}
	return message + "." + base64.RawURLEncoding.EncodeToString(sig)
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func jsonResponse(status int, payload any) *http.Response {
	b, _ := json.Marshal(payload)
	return &http.Response{StatusCode: status, Header: http.Header{"Content-Type": []string{"application/json"}}, Body: io.NopCloser(strings.NewReader(string(b)))}
}
