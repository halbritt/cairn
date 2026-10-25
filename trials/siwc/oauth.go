// Package siwc is an isolated, opt-in scaffold for the Sign in with ChatGPT
// open-source subscription flow. It is not imported by Cairn's runtime.
package siwc

import (
	"context"
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	AuthorizeEndpoint = "https://auth.openai.com/api/accounts/authorize"
	TokenEndpoint     = "https://auth.openai.com/api/accounts/oauth/token"
	Issuer            = "https://auth.openai.com"
	Resource          = "https://api.openai.com/v1"
	ModelsEndpoint    = Resource + "/models"
	ResponsesEndpoint = Resource + "/responses"
	discoveryEndpoint = Issuer + "/.well-known/openid-configuration"
	callbackPath      = "/auth/callback"
	registeredPrefix  = "oaiapp_"
)

var requiredScopes = []string{
	"openid", "profile", "email", "offline_access", "resource.invoke", "chatgpt.tokens.use.direct",
}

// Callback is the one authorization response delivered to the loopback listener.
type Callback struct {
	Code     string
	State    string
	ClientID string
	Scope    string
	Error    string
}

// CallbackListener binds only to IPv4 loopback and accepts one callback.
type CallbackListener struct {
	server   *http.Server
	listener net.Listener
	uri      string
	result   chan Callback
}

func ListenCallback() (*CallbackListener, error) {
	ln, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("listen on loopback: %w", err)
	}
	port := ln.Addr().(*net.TCPAddr).Port
	result := make(chan Callback, 1)
	l := &CallbackListener{listener: ln, uri: fmt.Sprintf("http://127.0.0.1:%d%s", port, callbackPath), result: result}
	mux := http.NewServeMux()
	mux.HandleFunc(callbackPath, func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.Host != ln.Addr().String() {
			http.Error(w, "invalid callback", http.StatusBadRequest)
			return
		}
		q := r.URL.Query()
		if len(q["state"]) != 1 || len(q["code"]) > 1 || len(q["error"]) > 1 || len(q["client_id"]) > 1 || len(q["scope"]) > 1 {
			http.Error(w, "invalid callback parameters", http.StatusBadRequest)
			return
		}
		select {
		case result <- Callback{Code: q.Get("code"), State: q.Get("state"), ClientID: q.Get("client_id"), Scope: q.Get("scope"), Error: q.Get("error")}:
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			_, _ = io.WriteString(w, "Sign-in response received. You may close this window.")
		default:
			http.Error(w, "callback already received", http.StatusConflict)
		}
	})
	l.server = &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() { _ = l.server.Serve(ln) }()
	return l, nil
}

func (l *CallbackListener) RedirectURI() string { return l.uri }

func (l *CallbackListener) Wait(ctx context.Context) (Callback, error) {
	select {
	case <-ctx.Done():
		return Callback{}, ctx.Err()
	case c := <-l.result:
		return c, nil
	}
}

func (l *CallbackListener) Close() error {
	if l == nil || l.server == nil {
		return nil
	}
	return l.server.Close()
}

// Attempt contains short-lived OAuth transaction material. Callers must keep it
// in memory and discard it after completion or cancellation.
type Attempt struct {
	State       string
	Nonce       string
	Verifier    string
	RedirectURI string
	HostID      string
	AgentName   string
	ClientID    string
	ExpectedSub string
}

// NewAttempt prepares a first-time dynamic registration. For reauthorization,
// pass the issued client ID and expected subject returned by a prior validated
// session. A per-account client ID is never inferred from an email address.
func NewAttempt(redirectURI, hostID, agentName, issuedClientID, expectedSubject string) (*Attempt, error) {
	u, err := url.Parse(redirectURI)
	if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.Path != callbackPath || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, errors.New("redirect URI must be an HTTP 127.0.0.1 loopback callback at /auth/callback")
	}
	if hostID == "" || !(strings.HasPrefix(hostID, "urn:uuid:") || strings.HasPrefix(hostID, "urn:ietf:params:oauth:jwk-thumbprint:") || strings.HasPrefix(hostID, "did:key:")) {
		return nil, errors.New("host ID must use a supported opaque URI form")
	}
	if strings.TrimSpace(agentName) == "" {
		return nil, errors.New("agent name is required")
	}
	clientID := issuedClientID
	if clientID == "" {
		clientID = "dynamic_agent_client"
	} else if !strings.HasPrefix(clientID, registeredPrefix) || expectedSubject == "" {
		return nil, errors.New("reauthorization requires an issued client ID and expected account subject")
	}
	state, err := randomURLToken(32)
	if err != nil {
		return nil, fmt.Errorf("create state: %w", err)
	}
	nonce, err := randomURLToken(32)
	if err != nil {
		return nil, fmt.Errorf("create nonce: %w", err)
	}
	verifier, err := randomURLToken(32)
	if err != nil {
		return nil, fmt.Errorf("create PKCE verifier: %w", err)
	}
	return &Attempt{State: state, Nonce: nonce, Verifier: verifier, RedirectURI: redirectURI, HostID: hostID, AgentName: agentName, ClientID: clientID, ExpectedSub: expectedSubject}, nil
}

func randomURLToken(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func (a *Attempt) AuthorizationURL() (string, error) {
	if a == nil || a.State == "" || a.Nonce == "" || a.Verifier == "" {
		return "", errors.New("incomplete authorization attempt")
	}
	h := sha256.Sum256([]byte(a.Verifier))
	clientID := a.ClientID
	params := url.Values{
		"client_id": {clientID}, "response_type": {"code"}, "redirect_uri": {a.RedirectURI},
		"scope": {strings.Join(requiredScopes, " ")}, "resource": {Resource}, "state": {a.State}, "nonce": {a.Nonce},
		"ext_agent_host_id": {a.HostID}, "code_challenge_method": {"S256"},
		"code_challenge": {base64.RawURLEncoding.EncodeToString(h[:])},
	}
	if clientID == "dynamic_agent_client" {
		params.Set("agent_name_hint", a.AgentName)
	}
	u, err := url.Parse(AuthorizeEndpoint)
	if err != nil {
		return "", err
	}
	u.RawQuery = params.Encode()
	return u.String(), nil
}

// Credentials are intentionally memory-only in this package. No helper writes
// them to disk, logs, or environment variables.
type Credentials struct {
	clientID    string
	subject     string
	accessToken string
	scopes      []string
	expiresAt   time.Time
	validated   bool
}

func (c Credentials) ClientID() string     { return c.clientID }
func (c Credentials) Subject() string      { return c.subject }
func (c Credentials) ExpiresAt() time.Time { return c.expiresAt }
func (c Credentials) HasScope(scope string) bool {
	return hasScopes(strings.Join(c.scopes, " "), []string{scope})
}

func (a *Attempt) Exchange(ctx context.Context, client *http.Client, callback Callback) (Credentials, error) {
	if a == nil {
		return Credentials{}, errors.New("missing authorization attempt")
	}
	if callback.State == "" || callback.State != a.State {
		return Credentials{}, errors.New("authorization state mismatch")
	}
	if callback.Error != "" {
		return Credentials{}, fmt.Errorf("authorization refused: %s", callback.Error)
	}
	if callback.Code == "" {
		return Credentials{}, errors.New("authorization code missing")
	}
	if a.ClientID == "dynamic_agent_client" {
		if !strings.HasPrefix(callback.ClientID, registeredPrefix) {
			return Credentials{}, errors.New("new registration did not return an issued client ID")
		}
	} else if callback.ClientID != "" && callback.ClientID != a.ClientID {
		return Credentials{}, errors.New("callback client ID does not match selected account registration")
	}
	clientID := callback.ClientID
	if clientID == "" {
		clientID = a.ClientID
	}
	form := url.Values{"grant_type": {"authorization_code"}, "client_id": {clientID}, "code": {callback.Code}, "code_verifier": {a.Verifier}, "redirect_uri": {a.RedirectURI}, "resource": {Resource}}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, TokenEndpoint, strings.NewReader(form.Encode()))
	if err != nil {
		return Credentials{}, fmt.Errorf("create token request: %w", err)
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	if client == nil {
		client = http.DefaultClient
	}
	client = noRedirectClient(client)
	resp, err := client.Do(req)
	if err != nil {
		return Credentials{}, fmt.Errorf("exchange authorization code: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return Credentials{}, fmt.Errorf("token endpoint returned HTTP %d", resp.StatusCode)
	}
	var token tokenResponse
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&token); err != nil {
		return Credentials{}, fmt.Errorf("decode token response: %w", err)
	}
	if token.AccessToken == "" || token.IDToken == "" || token.ExpiresIn <= 0 {
		return Credentials{}, errors.New("token response is missing required credentials")
	}
	if !strings.EqualFold(token.TokenType, "Bearer") {
		return Credentials{}, errors.New("token response is not a Bearer token")
	}
	if !hasScopes(token.Scope, requiredScopes) {
		return Credentials{}, errors.New("token response did not grant all required scopes")
	}
	identity, err := verifyIDToken(ctx, client, token.IDToken, clientID, a.Nonce)
	if err != nil {
		return Credentials{}, fmt.Errorf("validate ID token: %w", err)
	}
	if a.ExpectedSub != "" && identity.Subject != a.ExpectedSub {
		return Credentials{}, errors.New("reauthorized identity does not match selected account")
	}
	return Credentials{clientID: clientID, subject: identity.Subject, accessToken: token.AccessToken, scopes: strings.Fields(token.Scope), expiresAt: time.Now().Add(time.Duration(token.ExpiresIn) * time.Second), validated: true}, nil
}

type tokenResponse struct {
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token"`
	IDToken      string `json:"id_token"`
	TokenType    string `json:"token_type"`
	Scope        string `json:"scope"`
	ExpiresIn    int64  `json:"expires_in"`
}

func hasScopes(got string, required []string) bool {
	set := make(map[string]bool)
	for _, s := range strings.Fields(got) {
		set[s] = true
	}
	for _, s := range required {
		if !set[s] {
			return false
		}
	}
	return true
}

type identity struct{ Subject string }
type jwtHeader struct {
	Alg string `json:"alg"`
	Kid string `json:"kid"`
}
type claims struct {
	Issuer          string          `json:"iss"`
	Audience        json.RawMessage `json:"aud"`
	AuthorizedParty string          `json:"azp"`
	Subject         string          `json:"sub"`
	Nonce           string          `json:"nonce"`
	Expires         int64           `json:"exp"`
	NotBefore       int64           `json:"nbf"`
}
type openIDConfig struct {
	Issuer  string `json:"issuer"`
	JWKSURI string `json:"jwks_uri"`
}
type jwks struct {
	Keys []jwk `json:"keys"`
}
type jwk struct {
	Kty string `json:"kty"`
	Kid string `json:"kid"`
	Use string `json:"use"`
	Alg string `json:"alg"`
	N   string `json:"n"`
	E   string `json:"e"`
	Crv string `json:"crv"`
	X   string `json:"x"`
	Y   string `json:"y"`
}

func verifyIDToken(ctx context.Context, client *http.Client, token, audience, nonce string) (identity, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return identity{}, errors.New("malformed JWT")
	}
	decode := func(s string, dst any) error {
		b, err := base64.RawURLEncoding.DecodeString(s)
		if err != nil {
			return err
		}
		return json.Unmarshal(b, dst)
	}
	var h jwtHeader
	if err := decode(parts[0], &h); err != nil {
		return identity{}, errors.New("malformed JWT header")
	}
	var c claims
	if err := decode(parts[1], &c); err != nil {
		return identity{}, errors.New("malformed JWT claims")
	}
	if h.Kid == "" || c.Issuer != Issuer || c.Subject == "" || c.Nonce != nonce || !audienceContains(c.Audience, audience) || (audienceCount(c.Audience) > 1 && c.AuthorizedParty != audience) || c.Expires <= time.Now().Unix() || (c.NotBefore != 0 && c.NotBefore > time.Now().Unix()) {
		return identity{}, errors.New("JWT identity claims failed validation")
	}
	if client == nil {
		client = http.DefaultClient
	}
	client = noRedirectClient(client)
	configReq, err := http.NewRequestWithContext(ctx, http.MethodGet, discoveryEndpoint, nil)
	if err != nil {
		return identity{}, err
	}
	configResp, err := client.Do(configReq)
	if err != nil {
		return identity{}, fmt.Errorf("load OpenID configuration: %w", err)
	}
	defer configResp.Body.Close()
	if configResp.StatusCode != http.StatusOK {
		return identity{}, errors.New("OpenID configuration unavailable")
	}
	var cfg openIDConfig
	if err := json.NewDecoder(io.LimitReader(configResp.Body, 1<<20)).Decode(&cfg); err != nil {
		return identity{}, errors.New("invalid OpenID configuration")
	}
	if cfg.Issuer != Issuer {
		return identity{}, errors.New("unexpected OpenID issuer")
	}
	u, err := url.Parse(cfg.JWKSURI)
	if err != nil || u.Scheme != "https" || u.Hostname() != "auth.openai.com" {
		return identity{}, errors.New("unexpected JWKS endpoint")
	}
	keyReq, err := http.NewRequestWithContext(ctx, http.MethodGet, cfg.JWKSURI, nil)
	if err != nil {
		return identity{}, err
	}
	keyResp, err := client.Do(keyReq)
	if err != nil {
		return identity{}, fmt.Errorf("load signing keys: %w", err)
	}
	defer keyResp.Body.Close()
	if keyResp.StatusCode != http.StatusOK {
		return identity{}, errors.New("JWKS unavailable")
	}
	var set jwks
	if err := json.NewDecoder(io.LimitReader(keyResp.Body, 1<<20)).Decode(&set); err != nil {
		return identity{}, errors.New("invalid JWKS")
	}
	var key *jwk
	for i := range set.Keys {
		if set.Keys[i].Kid == h.Kid {
			key = &set.Keys[i]
			break
		}
	}
	if key == nil {
		return identity{}, errors.New("signing key not found")
	}
	signature, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		return identity{}, errors.New("malformed JWT signature")
	}
	signed := []byte(parts[0] + "." + parts[1])
	if err := verifySignature(h.Alg, *key, signed, signature); err != nil {
		return identity{}, err
	}
	return identity{Subject: c.Subject}, nil
}

// noRedirectClient preserves the supplied transport and timeouts while making
// fixed-credential endpoints fail closed on every redirect. Go's default
// behavior can resend POST bodies on 307/308, including codes, prompts, and
// selected source text.
func noRedirectClient(client *http.Client) *http.Client {
	copy := *client
	copy.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	return &copy
}

func audienceContains(raw json.RawMessage, want string) bool {
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return s == want
	}
	var list []string
	if json.Unmarshal(raw, &list) == nil {
		for _, x := range list {
			if x == want {
				return true
			}
		}
	}
	return false
}

func audienceCount(raw json.RawMessage) int {
	var single string
	if json.Unmarshal(raw, &single) == nil {
		return 1
	}
	var list []string
	if json.Unmarshal(raw, &list) == nil {
		return len(list)
	}
	return 0
}

func verifySignature(alg string, key jwk, signed, signature []byte) error {
	hash := sha256.Sum256(signed)
	switch alg {
	case "RS256":
		if key.Kty != "RSA" || (key.Use != "" && key.Use != "sig") || (key.Alg != "" && key.Alg != alg) {
			return errors.New("JWT signing key type mismatch")
		}
		n, err := base64.RawURLEncoding.DecodeString(key.N)
		if err != nil {
			return errors.New("invalid RSA modulus")
		}
		eb, err := base64.RawURLEncoding.DecodeString(key.E)
		if err != nil {
			return errors.New("invalid RSA exponent")
		}
		e := 0
		for _, b := range eb {
			e = e<<8 | int(b)
		}
		if e < 3 {
			return errors.New("invalid RSA exponent")
		}
		return rsa.VerifyPKCS1v15(&rsa.PublicKey{N: new(big.Int).SetBytes(n), E: e}, crypto.SHA256, hash[:], signature)
	case "ES256":
		if key.Kty != "EC" || key.Crv != "P-256" || (key.Use != "" && key.Use != "sig") || (key.Alg != "" && key.Alg != alg) {
			return errors.New("JWT signing key type mismatch")
		}
		xb, err := base64.RawURLEncoding.DecodeString(key.X)
		if err != nil {
			return errors.New("invalid EC x coordinate")
		}
		yb, err := base64.RawURLEncoding.DecodeString(key.Y)
		if err != nil {
			return errors.New("invalid EC y coordinate")
		}
		if len(signature) != 64 {
			return errors.New("invalid ES256 signature size")
		}
		r := new(big.Int).SetBytes(signature[:32])
		s := new(big.Int).SetBytes(signature[32:])
		if !ecdsa.Verify(&ecdsa.PublicKey{Curve: elliptic.P256(), X: new(big.Int).SetBytes(xb), Y: new(big.Int).SetBytes(yb)}, hash[:], r, s) {
			return errors.New("JWT signature verification failed")
		}
		return nil
	default:
		return errors.New("unsupported JWT signing algorithm")
	}
}
