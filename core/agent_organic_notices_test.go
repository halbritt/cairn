package core

import (
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

type organicFixture struct {
	repo         string
	operator     *Store
	publisher    *Store
	recipient    *Store
	source       Record
	sessionRef   AgentSessionRef
	dest         Destination
	launch       NativeOwnerLaunch
	pubKey       ed25519.PublicKey
	privKey      ed25519.PrivateKey
	nativeSessID string
	binding      string
}

func signAttestation(t *testing.T, privKey ed25519.PrivateKey, payload OwnerInputAttestationPayload) OwnerInputAttestation {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	signedBytes := append([]byte("cairn.owner-input-attestation/1\n"), raw...)
	sig := ed25519.Sign(privKey, signedBytes)
	return OwnerInputAttestation{
		Schema:    "cairn.owner-input-attestation/1",
		Payload:   base64.RawURLEncoding.EncodeToString(raw),
		Signature: base64.RawURLEncoding.EncodeToString(sig),
	}
}

func setupOrganicFixture(t *testing.T, repo string) *organicFixture {
	t.Helper()
	return setupOrganicFixtureWithStores(t, repo, func(ch Channel) *Store {
		return testStore(t, ch)
	})
}

func setupOrganicFixtureWithStores(t *testing.T, repo string, openStore func(Channel) *Store) *organicFixture {
	t.Helper()
	ctx := context.Background()
	op := openStore(Channel{Principal: "operator-" + repo, Operator: true})
	pub := openStore(Channel{Principal: "publisher-" + repo, Repo: repo, Operator: true})
	rec := openStore(Channel{Principal: "consumer-" + repo, Repo: repo})
	dest := Destination{Name: "hosted", AllowLocal: false}

	// 1. Enable work feature next-turn-notices/1
	err := op.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID:   uuid.NewString(),
		FeatureName: "next-turn-notices/1",
		Status:      "active",
	})
	if err != nil {
		t.Fatal(err)
	}

	// 2. Generate Ed25519 keypair and register launch via operator
	pubKey, privKey, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	launchID := uuid.NewString()
	binding := "codex-binding"
	launch, err := op.RegisterNativeOwnerLaunch(ctx, RegisterNativeOwnerLaunchRequest{
		RequestID:           uuid.NewString(),
		LaunchID:            launchID,
		Repo:                repo,
		ProfilePrincipal:    "consumer-" + repo,
		Binding:             binding,
		PublicKey:           base64.RawURLEncoding.EncodeToString(pubKey),
		AttestationContract: "cairn.owner-input-attestation/1",
		OriginContract:      "codex.owner-input/1",
		OriginVersion:       1,
		UpstreamCommit:      "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856",
		PatchSHA256:         "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
		RuntimeSHA256:       "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
	})
	if err != nil {
		t.Fatalf("RegisterNativeOwnerLaunch: %v", err)
	}

	// 3. Register recipient agent session with launch_id (reserves pair as pending)
	nativeSessID := "native-sess-1"
	reg, err := rec.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Binding:         binding,
		NativeSessionID: nativeSessID,
		LaunchID:        launch.LaunchID,
		Metadata: AgentMetadata{
			Harness:      "codex",
			Project:      "test-proj",
			Workspace:    "/home/test",
			State:        "idle",
			DeliveryMode: "existing-session",
		},
	}, dest)
	if err != nil {
		t.Fatalf("RegisterAgent: %v", err)
	}
	sessionRef := AgentSessionRef{AgentID: reg.AgentID, ExecutionID: reg.ExecutionID}

	// 4. Create source record
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	draft.Body = "organic notice source"
	source, err := pub.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}

	return &organicFixture{
		repo:         repo,
		operator:     op,
		publisher:    pub,
		recipient:    rec,
		source:       source,
		sessionRef:   sessionRef,
		dest:         dest,
		launch:       launch,
		pubKey:       pubKey,
		privKey:      privKey,
		nativeSessID: nativeSessID,
		binding:      binding,
	}
}

func (f *organicFixture) consumerStore(t *testing.T) *Store {
	t.Helper()
	s, err := f.recipient.ForAgentSession(f.sessionRef, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func (f *organicFixture) activateCapability(t *testing.T) {
	t.Helper()
	nowMS := time.Now().UnixMilli()
	payload := OwnerInputAttestationPayload{
		IssuedAtUnixMS:    nowMS,
		LaunchID:          f.launch.LaunchID,
		RestoreGeneration: f.launch.RestoreGeneration,
		NativeSessionID:   f.nativeSessID,
		NativeTurnID:      "turn-init-activation",
		OriginContract:    "codex.owner-input/1",
		OriginVersion:     1,
		Source:            "unknown",
		Admission:         "fresh_start",
	}
	att := signAttestation(t, f.privKey, payload)
	res, err := f.recipient.ClaimOrganicNotice(context.Background(), SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload.NativeTurnID,
		OwnerInputAttestation: &att,
	}, f.dest)
	if err != nil {
		t.Fatalf("activateCapability failed: %v", err)
	}
	if res.Decision != "none" {
		t.Fatalf("expected decision none for activation input, got %s", res.Decision)
	}
}

func (f *organicFixture) publishNotice(t *testing.T, body string) AgentEvent {
	t.Helper()
	ctx := context.Background()
	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)
	draft := projectNote(f.repo)
	draft.Sensitivity = "shareable"
	draft.Body = body
	source, err := f.publisher.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft})
	if err != nil {
		t.Fatal(err)
	}
	ev, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            f.repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	if err != nil {
		t.Fatalf("PublishEvent: %v", err)
	}
	return ev
}

func (f *organicFixture) signAttestation(t *testing.T, turnID, source, admission, inputID string, issuedAtMS int64) (OwnerInputAttestation, OwnerInputAttestationPayload) {
	t.Helper()
	var inID *string
	if inputID != "" {
		inID = &inputID
	}
	payload := OwnerInputAttestationPayload{
		IssuedAtUnixMS:    issuedAtMS,
		LaunchID:          f.launch.LaunchID,
		RestoreGeneration: f.launch.RestoreGeneration,
		NativeSessionID:   f.nativeSessID,
		NativeTurnID:      turnID,
		OriginContract:    "codex.owner-input/1",
		OriginVersion:     1,
		Source:            source,
		Admission:         admission,
		InputID:           inID,
	}
	att := signAttestation(t, f.privKey, payload)
	return att, payload
}

func TestOrganicNotices_PendingReservationDoesNotGrantPublishCapability(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	ctx := context.Background()

	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)

	// Publishing with next_organic_turn while binding is pending MUST refuse UNSUPPORTED_DELIVERY!
	_, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	requireCode(t, err, "UNSUPPORTED_DELIVERY")

	// Now activate capability with first signed input
	f.activateCapability(t)

	// Now publishing MUST succeed!
	_, err = f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	if err != nil {
		t.Fatalf("PublishEvent after activation failed: %v", err)
	}
}

func TestOrganicNotices_AC28_ZeroIdleWakes(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)

	// Publish quiet notice
	pubReq := PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}
	ev, err := f.publisher.PublishEvent(ctx, pubReq, f.dest)
	if err != nil {
		t.Fatalf("PublishEvent: %v", err)
	}

	// AC-28.1: No rows in cairn.agent_delivery
	var deliveryCount int
	err = f.publisher.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_delivery WHERE event_id=$1`, ev.EventID).Scan(&deliveryCount)
	if err != nil {
		t.Fatal(err)
	}
	if deliveryCount != 0 {
		t.Fatalf("expected 0 deliveries for queued organic notice, got %d", deliveryCount)
	}

	// AC-28.2: NextEvent returns no delivery
	consumer, err := f.recipient.ForAgentSession(f.sessionRef, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	next, err := consumer.NextEvent(ctx, NextEventRequest{Repo: repo, Agent: "agent/" + f.sessionRef.AgentID}, f.dest)
	if err != nil {
		t.Fatalf("NextEvent: %v", err)
	}
	if next.Delivery != nil {
		t.Fatalf("NextEvent returned delivery for queued organic notice: %+v", next.Delivery)
	}

	// AC-28.3: session inbox ready and pending do not report it as ready
	ready, err := f.recipient.SessionInboxReady(ctx, f.sessionRef, f.dest)
	if err != nil {
		t.Fatalf("SessionInboxReady: %v", err)
	}
	if ready.DeliveryID != "" {
		t.Fatalf("SessionInboxReady reported ready for quiet notice: %s", ready.DeliveryID)
	}

	pending, err := f.recipient.SessionInboxPending(ctx, f.sessionRef, f.dest)
	if err != nil {
		t.Fatalf("SessionInboxPending: %v", err)
	}
	if pending.Notices != 0 {
		t.Fatalf("SessionInboxPending notice count = %d, expected 0", pending.Notices)
	}

	// AC-28.4: notice-pending inspection DOES find it queued
	np, err := f.consumerStore(t).NoticePending(ctx, NoticePendingRequest{Repo: repo, AgentID: f.sessionRef.AgentID}, f.dest)
	if err != nil {
		t.Fatalf("NoticePending: %v", err)
	}
	if np.Count != 1 || len(np.Notices) != 1 {
		t.Fatalf("NoticePending count = %d, expected 1", np.Count)
	}
	if np.Notices[0].State != "queued" {
		t.Fatalf("NoticePending notice state = %s, expected queued", np.Notices[0].State)
	}
	if !np.Supported {
		t.Fatalf("expected NoticePending supported = true")
	}
}

func TestOrganicNotices_AttestationVerification_NegativeCases(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	ctx := context.Background()
	nowMS := time.Now().UnixMilli()

	// 1. Missing attestation -> AUTHORITY_DENIED
	_, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:         uuid.NewString(),
		Session:           f.sessionRef,
		NativeOwnerTurnID: "turn-1",
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// 2. Invalid schema -> INVALID_REQUEST
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:         uuid.NewString(),
		Session:           f.sessionRef,
		NativeOwnerTurnID: "turn-1",
		OwnerInputAttestation: &OwnerInputAttestation{
			Schema:    "cairn.wrong-schema/1",
			Payload:   "e30",
			Signature: "e30",
		},
	}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")

	// 3. Base64 padding '=' -> INVALID_REQUEST
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:         uuid.NewString(),
		Session:           f.sessionRef,
		NativeOwnerTurnID: "turn-1",
		OwnerInputAttestation: &OwnerInputAttestation{
			Schema:    "cairn.owner-input-attestation/1",
			Payload:   "eyJmb28iOiJiYXIifQ==",
			Signature: "c2lnbmF0dXJl==",
		},
	}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")

	// 4. Invalid signature with different private key -> AUTHORITY_DENIED
	_, wrongPriv, _ := ed25519.GenerateKey(nil)
	inputID := uuid.NewString()
	payload := OwnerInputAttestationPayload{
		IssuedAtUnixMS:    nowMS,
		LaunchID:          f.launch.LaunchID,
		RestoreGeneration: f.launch.RestoreGeneration,
		NativeSessionID:   f.nativeSessID,
		NativeTurnID:      "turn-bad-sig",
		OriginContract:    "codex.owner-input/1",
		OriginVersion:     1,
		Source:            "owner_composer",
		Admission:         "fresh_start",
		InputID:           &inputID,
	}
	badSigAtt := signAttestation(t, wrongPriv, payload)
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload.NativeTurnID,
		OwnerInputAttestation: &badSigAtt,
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// 5. Native session ID mismatch -> AUTHORITY_DENIED
	payloadMismatch := payload
	payloadMismatch.NativeSessionID = "other-session"
	mismatchAtt := signAttestation(t, f.privKey, payloadMismatch)
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payloadMismatch.NativeTurnID,
		OwnerInputAttestation: &mismatchAtt,
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// 6. Top-level native_owner_turn_id mismatch -> INVALID_REQUEST
	validAtt := signAttestation(t, f.privKey, payload)
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     "different-turn-id",
		OwnerInputAttestation: &validAtt,
	}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")
}

func TestOrganicNotices_FirstBindReplayClosure_CapturedSignatureAcrossExecutionReplacement(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	ctx := context.Background()

	// 1. Initial RegisterAgent req_1 reserved (L, NS1) as pending for execution_1.
	// Verify PublishEvent refuses UNSUPPORTED_DELIVERY while pending:
	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)
	_, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	requireCode(t, err, "UNSUPPORTED_DELIVERY")

	// 2. Sign a valid first UserPromptSubmit attestation S1 for (L, gen, NS1, turn_1) WITHOUT sending it yet!
	nowMS := time.Now().UnixMilli()
	input1 := uuid.NewString()
	payload1 := OwnerInputAttestationPayload{
		IssuedAtUnixMS:    nowMS,
		LaunchID:          f.launch.LaunchID,
		RestoreGeneration: f.launch.RestoreGeneration,
		NativeSessionID:   f.nativeSessID,
		NativeTurnID:      "turn-1",
		OriginContract:    "codex.owner-input/1",
		OriginVersion:     1,
		Source:            "owner_composer",
		Admission:         "fresh_start",
		InputID:           &input1,
	}
	s1 := signAttestation(t, f.privKey, payload1)

	// 3. Call RegisterAgent (req_2, native_session_id = NS1) rotating session to execution_2:
	reg2, err := f.recipient.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Binding:         f.binding,
		NativeSessionID: f.nativeSessID,
		LaunchID:        f.launch.LaunchID,
		Metadata: AgentMetadata{
			Harness:      "codex",
			Project:      "test-proj",
			Workspace:    "/home/test",
			State:        "idle",
			DeliveryMode: "existing-session",
		},
	}, f.dest)
	if err != nil {
		t.Fatalf("RegisterAgent rotation: %v", err)
	}
	sessionRef2 := AgentSessionRef{AgentID: reg2.AgentID, ExecutionID: reg2.ExecutionID}
	if sessionRef2.ExecutionID == f.sessionRef.ExecutionID {
		t.Fatalf("expected execution rotation")
	}

	// 4. Attempt to submit captured S1 against execution_1: MUST REFUSE STALE_SESSION!
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef, // old execution_1
		NativeOwnerTurnID:     payload1.NativeTurnID,
		OwnerInputAttestation: &s1,
	}, f.dest)
	requireCode(t, err, "STALE_SESSION")

	// 5. Attempt to submit captured S1 against execution_2: MUST REFUSE AUTHORITY_DENIED (stale execution)!
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               sessionRef2, // new execution_2
		NativeOwnerTurnID:     payload1.NativeTurnID,
		OwnerInputAttestation: &s1,
	}, f.dest)
	requireCode(t, err, "AUTHORITY_DENIED")

	// Verify no root was created:
	var rootCount int
	err = f.recipient.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_organic_notice_turn_root WHERE repo=$1 AND agent_id=$2`, repo, f.sessionRef.AgentID).Scan(&rootCount)
	if err != nil {
		t.Fatal(err)
	}
	if rootCount != 0 {
		t.Fatalf("expected 0 roots created after refused replays, got %d", rootCount)
	}
}

func TestOrganicNotices_ClockWindowAndReplayOrder(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	ctx := context.Background()

	nowMS := time.Now().UnixMilli()

	// 1. Delayed unseen signature (issued_at_unix_ms = now - 31_000 ms, no prior root):
	// MUST refuse without activating capability or selecting notice!
	oldInput := uuid.NewString()
	delayedPayload := OwnerInputAttestationPayload{
		IssuedAtUnixMS:    nowMS - 31000,
		LaunchID:          f.launch.LaunchID,
		RestoreGeneration: f.launch.RestoreGeneration,
		NativeSessionID:   f.nativeSessID,
		NativeTurnID:      "turn-delayed",
		OriginContract:    "codex.owner-input/1",
		OriginVersion:     1,
		Source:            "owner_composer",
		Admission:         "fresh_start",
		InputID:           &oldInput,
	}
	delayedAtt := signAttestation(t, f.privKey, delayedPayload)
	_, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     delayedPayload.NativeTurnID,
		OwnerInputAttestation: &delayedAtt,
	}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")

	// 2. Future timestamp (issued_at_unix_ms = now + 6_000 ms, no prior root):
	// MUST refuse without activating capability or selecting notice!
	futureInput := uuid.NewString()
	futurePayload := OwnerInputAttestationPayload{
		IssuedAtUnixMS:    nowMS + 6000,
		LaunchID:          f.launch.LaunchID,
		RestoreGeneration: f.launch.RestoreGeneration,
		NativeSessionID:   f.nativeSessID,
		NativeTurnID:      "turn-future",
		OriginContract:    "codex.owner-input/1",
		OriginVersion:     1,
		Source:            "owner_composer",
		Admission:         "fresh_start",
		InputID:           &futureInput,
	}
	futureAtt := signAttestation(t, f.privKey, futurePayload)
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     futurePayload.NativeTurnID,
		OwnerInputAttestation: &futureAtt,
	}, f.dest)
	requireCode(t, err, "INVALID_REQUEST")

	// 3. Old exact committed-root replay:
	// First, activate and admit a valid root at t0
	t0 := time.Now().UnixMilli()
	t0Input := uuid.NewString()
	t0Payload := OwnerInputAttestationPayload{
		IssuedAtUnixMS:    t0,
		LaunchID:          f.launch.LaunchID,
		RestoreGeneration: f.launch.RestoreGeneration,
		NativeSessionID:   f.nativeSessID,
		NativeTurnID:      "turn-t0",
		OriginContract:    "codex.owner-input/1",
		OriginVersion:     1,
		Source:            "owner_composer",
		Admission:         "fresh_start",
		InputID:           &t0Input,
	}
	t0Att := signAttestation(t, f.privKey, t0Payload)

	// Publish notice 1
	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)
	_, err = f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	// (Note: first publish might fail if capability not active yet, let's claim t0 which activates capability and admits notice 1)
	claimT0, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     t0Payload.NativeTurnID,
		OwnerInputAttestation: &t0Att,
	}, f.dest)
	if err != nil {
		t.Fatalf("claimT0 failed: %v", err)
	}
	if claimT0.RootID == "" {
		t.Fatalf("expected non-empty root ID")
	}

	// Now capability is active! Publish notice 2 after t0 root is frozen:
	ev2, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	if err != nil {
		t.Fatalf("Publish notice 2: %v", err)
	}

	// Update the root's created_at and fake clock age if needed, or simulate replay of t0Att
	// with a new request_id:
	replayRes, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(), // NEW request_id UUID
		Session:               f.sessionRef,
		NativeOwnerTurnID:     t0Payload.NativeTurnID,
		OwnerInputAttestation: &t0Att,
	}, f.dest)
	if err != nil {
		t.Fatalf("replay of exact t0 root failed: %v", err)
	}
	if replayRes.RootID != claimT0.RootID {
		t.Fatalf("expected recovered root ID %s, got %s", claimT0.RootID, replayRes.RootID)
	}
	// Verify notice 2 was NOT admitted to this root:
	var notice2State string
	err = f.publisher.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_organic_notice WHERE event_id=$1`, ev2.EventID).Scan(&notice2State)
	if err != nil {
		t.Fatal(err)
	}
	if notice2State != "queued" {
		t.Fatalf("notice 2 state = %s, expected queued (must not be admitted on replay)", notice2State)
	}
}

func TestOrganicNotices_DualUniquenessAndReplaySemantics(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	nowMS := time.Now().UnixMilli()
	inputID1 := uuid.NewString()
	payload := OwnerInputAttestationPayload{
		IssuedAtUnixMS:    nowMS,
		LaunchID:          f.launch.LaunchID,
		RestoreGeneration: f.launch.RestoreGeneration,
		NativeSessionID:   f.nativeSessID,
		NativeTurnID:      "turn-dual-1",
		OriginContract:    "codex.owner-input/1",
		OriginVersion:     1,
		Source:            "owner_composer",
		Admission:         "fresh_start",
		InputID:           &inputID1,
	}
	att1 := signAttestation(t, f.privKey, payload)

	// Publish a notice
	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)
	_, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}

	// 1. Initial claim
	claim1, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload.NativeTurnID,
		OwnerInputAttestation: &att1,
	}, f.dest)
	if err != nil {
		t.Fatalf("claim1: %v", err)
	}
	if claim1.Decision != "admitted" || claim1.Attempt == nil {
		t.Fatalf("expected admitted decision, got %+v", claim1)
	}

	// 2. Replay with NEW request_id UUID -> MUST SUCCEED and return already-frozen root & attempt!
	replayRes, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(), // new request ID!
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload.NativeTurnID,
		OwnerInputAttestation: &att1,
	}, f.dest)
	if err != nil {
		t.Fatalf("replay with new request_id failed: %v", err)
	}
	if replayRes.RootID != claim1.RootID {
		t.Fatalf("expected same root ID %s, got %s", claim1.RootID, replayRes.RootID)
	}
	if replayRes.Decision != "admitted" || replayRes.Attempt == nil || replayRes.Attempt.ID != claim1.Attempt.ID {
		t.Fatalf("expected same attempt recovered, got %+v", replayRes)
	}

	// 3. Existing native_turn_id with a DIFFERENT input_id -> MUST REFUSE CONFLICT!
	differentInputID := uuid.NewString()
	payloadDiffInput := payload
	payloadDiffInput.InputID = &differentInputID
	attDiffInput := signAttestation(t, f.privKey, payloadDiffInput)
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payloadDiffInput.NativeTurnID,
		OwnerInputAttestation: &attDiffInput,
	}, f.dest)
	requireCode(t, err, "CONFLICT")

	// 4. Already-used input_id bound to a DIFFERENT native_turn_id -> MUST REFUSE INPUT_ID_REUSED!
	payloadDiffTurn := payload
	payloadDiffTurn.NativeTurnID = "turn-dual-2"
	attDiffTurn := signAttestation(t, f.privKey, payloadDiffTurn)
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payloadDiffTurn.NativeTurnID,
		OwnerInputAttestation: &attDiffTurn,
	}, f.dest)
	requireCode(t, err, "INPUT_ID_REUSED")
}

func TestOrganicNotices_BurstLimitAndCutoff(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)

	// Publish 4 notices
	for i := 0; i < 4; i++ {
		_, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
			RequestID:       uuid.NewString(),
			Repo:            repo,
			Kind:            "notice",
			Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
			Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
			NoticeDelivery:  "next_organic_turn",
			NoticeExpiresAt: &expiresAt,
		}, f.dest)
		if err != nil {
			t.Fatal(err)
		}
	}

	// Claim turn 1
	nowMS := time.Now().UnixMilli()
	turn1InputID := uuid.NewString()
	payload := OwnerInputAttestationPayload{
		IssuedAtUnixMS:    nowMS,
		LaunchID:          f.launch.LaunchID,
		RestoreGeneration: f.launch.RestoreGeneration,
		NativeSessionID:   f.nativeSessID,
		NativeTurnID:      "turn-burst",
		OriginContract:    "codex.owner-input/1",
		OriginVersion:     1,
		Source:            "owner_composer",
		Admission:         "fresh_start",
		InputID:           &turn1InputID,
	}
	att := signAttestation(t, f.privKey, payload)

	claim1, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload.NativeTurnID,
		OwnerInputAttestation: &att,
	}, f.dest)
	if err != nil || claim1.Decision != "admitted" {
		t.Fatalf("claim1: %+v %v", claim1, err)
	}

	// Settle item 1:
	_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  claim1.Attempt.Delivery.DeliveryID,
		LeaseID:     claim1.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("Complete item 1: %v", err)
	}

	// Advance item 2:
	adv2, err := f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{
		RequestID: uuid.NewString(),
		Session:   f.sessionRef,
		AttemptID: claim1.Attempt.ID,
		RootID:    claim1.RootID,
		Version:   1,
	}, f.dest)
	if err != nil || adv2.Attempt == nil {
		t.Fatalf("advance2: %+v %v", adv2, err)
	}

	// Settle item 2:
	_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  adv2.Attempt.Delivery.DeliveryID,
		LeaseID:     adv2.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("Complete item 2: %v", err)
	}

	// Advance item 3:
	adv3, err := f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{
		RequestID: uuid.NewString(),
		Session:   f.sessionRef,
		AttemptID: adv2.Attempt.ID,
		RootID:    claim1.RootID,
		Version:   1,
	}, f.dest)
	if err != nil || adv3.Attempt == nil {
		t.Fatalf("advance3: %+v %v", adv3, err)
	}

	// Settle item 3:
	_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  adv3.Attempt.Delivery.DeliveryID,
		LeaseID:     adv3.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("Complete item 3: %v", err)
	}

	// Advance item 4 -> Must hit burst limit (3)!
	adv4, err := f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{
		RequestID: uuid.NewString(),
		Session:   f.sessionRef,
		AttemptID: adv3.Attempt.ID,
		RootID:    claim1.RootID,
		Version:   1,
	}, f.dest)
	if err != nil {
		t.Fatalf("advance4: %v", err)
	}
	if adv4.Remainder != "batch_limit" {
		t.Fatalf("expected remainder batch_limit, got %s", adv4.Remainder)
	}
	if adv4.Attempt != nil {
		t.Fatalf("expected nil attempt after hitting burst limit")
	}
}

func TestOrganicNotices_NoticePendingAndFlush(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)

	// Publish 2 notices
	ev1, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}

	ev2, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}

	// Pending check
	pending, err := f.operator.NoticePending(ctx, NoticePendingRequest{Repo: repo, AgentID: f.sessionRef.AgentID}, f.dest)
	if err != nil {
		t.Fatal(err)
	}
	if pending.Count != 2 {
		t.Fatalf("expected count 2, got %d", pending.Count)
	}
	if !pending.Supported {
		t.Fatalf("expected supported true")
	}

	// Flush notice 1 by event_id
	flushRes, err := f.publisher.FlushNotice(ctx, NoticeFlushRequest{
		RequestID: uuid.NewString(),
		EventID:   ev1.EventID,
	}, f.dest)
	if err != nil {
		t.Fatalf("FlushNotice: %v", err)
	}
	if flushRes.Released != 1 {
		t.Fatalf("expected 1 released, got %d", flushRes.Released)
	}

	// Check state in DB
	var state1 string
	err = f.publisher.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_organic_notice WHERE event_id=$1`, ev1.EventID).Scan(&state1)
	if err != nil || state1 != "flushed" {
		t.Fatalf("expected flushed state, got %s (err %v)", state1, err)
	}

	// Flush notice 2 by event_id
	flushRes2, err := f.publisher.FlushNotice(ctx, NoticeFlushRequest{
		RequestID: uuid.NewString(),
		EventID:   ev2.EventID,
	}, f.dest)
	if err != nil {
		t.Fatalf("FlushNotice ev2: %v", err)
	}
	if flushRes2.Released != 1 {
		t.Fatalf("expected 1 released for ev2, got %d", flushRes2.Released)
	}

	var state2 string
	err = f.publisher.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_organic_notice WHERE event_id=$1`, ev2.EventID).Scan(&state2)
	if err != nil || state2 != "flushed" {
		t.Fatalf("expected flushed state, got %s (err %v)", state2, err)
	}
}

func TestOrganicNotices_ExpiryAndSweeper(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	now := time.Now().UTC()
	expiresAt := now.Add(1 * time.Hour)

	ev, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}

	// Backdate notice to expired
	_, err = f.operator.pool.Exec(ctx, `UPDATE cairn.agent_organic_notice SET expires_at=clock_timestamp() - interval '10 minutes' WHERE event_id=$1`, ev.EventID)
	if err != nil {
		t.Fatal(err)
	}

	// Sweep
	sweepRes, err := f.operator.SweepNoticeExpiries(ctx, NoticeExpirySweepRequest{Repo: repo})
	if err != nil {
		t.Fatalf("SweepNoticeExpiries: %v", err)
	}
	if sweepRes.ExpiredCount != 1 {
		t.Fatalf("expected 1 expired, got %d", sweepRes.ExpiredCount)
	}

	var state string
	err = f.operator.pool.QueryRow(ctx, `SELECT state FROM cairn.agent_organic_notice WHERE event_id=$1`, ev.EventID).Scan(&state)
	if err != nil || state != "expired" {
		t.Fatalf("expected expired state, got %s", state)
	}
}

func TestOrganicNotices_RestoreGenerationFencing(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	// Advance restore generation via FenceRestore
	_, err := f.operator.FenceRestore(ctx, RestoreFenceRequest{
		RequestID: uuid.NewString(),
		Reason:    "test restore fence",
	})
	if err != nil {
		t.Fatalf("FenceRestore: %v", err)
	}

	// Old session ref has stale generation: ClaimOrganicNotice MUST refuse STALE_SESSION
	nowMS := time.Now().UnixMilli()
	inputID := uuid.NewString()
	payload := OwnerInputAttestationPayload{
		IssuedAtUnixMS:    nowMS,
		LaunchID:          f.launch.LaunchID,
		RestoreGeneration: f.launch.RestoreGeneration,
		NativeSessionID:   f.nativeSessID,
		NativeTurnID:      "turn-fenced",
		OriginContract:    "codex.owner-input/1",
		OriginVersion:     1,
		Source:            "owner_composer",
		Admission:         "fresh_start",
		InputID:           &inputID,
	}
	att := signAttestation(t, f.privKey, payload)
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload.NativeTurnID,
		OwnerInputAttestation: &att,
	}, f.dest)
	requireCode(t, err, "STALE_SESSION")
}

func TestOrganicNotices_AC32_RaceAndQueueCaps(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)

	// Publish one notice
	ev, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}

	// Concurrent race between Claim and Flush
	var wg sync.WaitGroup
	wg.Add(2)
	var claimErr, flushErr error
	var claimRes SessionOrganicNoticeResult
	var flushRes NoticeFlushResult

	nowMS := time.Now().UnixMilli()
	inputID := uuid.NewString()
	payload := OwnerInputAttestationPayload{
		IssuedAtUnixMS:    nowMS,
		LaunchID:          f.launch.LaunchID,
		RestoreGeneration: f.launch.RestoreGeneration,
		NativeSessionID:   f.nativeSessID,
		NativeTurnID:      "turn-race",
		OriginContract:    "codex.owner-input/1",
		OriginVersion:     1,
		Source:            "owner_composer",
		Admission:         "fresh_start",
		InputID:           &inputID,
	}
	att := signAttestation(t, f.privKey, payload)

	go func() {
		defer wg.Done()
		claimRes, claimErr = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
			RequestID:             uuid.NewString(),
			Session:               f.sessionRef,
			NativeOwnerTurnID:     payload.NativeTurnID,
			OwnerInputAttestation: &att,
		}, f.dest)
	}()

	go func() {
		defer wg.Done()
		flushRes, flushErr = f.publisher.FlushNotice(ctx, NoticeFlushRequest{
			RequestID: uuid.NewString(),
			EventID:   ev.EventID,
		}, f.dest)
	}()

	wg.Wait()

	if claimErr != nil {
		t.Fatalf("Claim in race failed: %v", claimErr)
	}
	if flushErr != nil {
		t.Fatalf("Flush in race failed: %v", flushErr)
	}

	// Winner takes all: either claimed/admitted (and released count 0), or flushed (and claim decision none)
	if claimRes.Decision == "admitted" {
		if flushRes.Released != 0 {
			t.Fatalf("Claim admitted but flush also succeeded: flush released = %d", flushRes.Released)
		}
	} else if flushRes.Released == 1 {
		if claimRes.Decision != "none" {
			t.Fatalf("Flush succeeded but claim decision was %s", claimRes.Decision)
		}
	} else {
		t.Fatalf("Inconsistent race result: claim decision %s, flush released %d", claimRes.Decision, flushRes.Released)
	}
}

func TestOperator_RegisterNativeOwnerLaunch_RetryAndFence(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	op := testStore(t, Channel{Principal: "operator-" + repo, Operator: true})
	ctx := context.Background()

	err := op.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID:   uuid.NewString(),
		FeatureName: "next-turn-notices/1",
		Status:      "active",
	})
	if err != nil {
		t.Fatal(err)
	}

	pubKey, _, err := ed25519.GenerateKey(nil)
	if err != nil {
		t.Fatal(err)
	}
	launchID := uuid.NewString()
	reqID := uuid.NewString()
	req := RegisterNativeOwnerLaunchRequest{
		RequestID:           reqID,
		LaunchID:            launchID,
		Repo:                repo,
		ProfilePrincipal:    "consumer-" + repo,
		Binding:             "codex-binding",
		PublicKey:           base64.RawURLEncoding.EncodeToString(pubKey),
		AttestationContract: "cairn.owner-input-attestation/1",
		OriginContract:      "codex.owner-input/1",
		OriginVersion:       1,
		UpstreamCommit:      "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856",
		PatchSHA256:         "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
		RuntimeSHA256:       "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
	}

	// 1. Initial registration
	launch1, err := op.RegisterNativeOwnerLaunch(ctx, req)
	if err != nil {
		t.Fatalf("RegisterNativeOwnerLaunch initial: %v", err)
	}
	if launch1.LaunchID != launchID || launch1.Status != "active" {
		t.Fatalf("unexpected launch: %+v", launch1)
	}

	// 2. Lost-reply retry with SAME request_id (proves typed privileged[NativeOwnerLaunch] return, no map[string]any panic!)
	launch2, err := op.RegisterNativeOwnerLaunch(ctx, req)
	if err != nil {
		t.Fatalf("RegisterNativeOwnerLaunch cached retry: %v", err)
	}
	if launch2.LaunchID != launch1.LaunchID || launch2.RestoreGeneration != launch1.RestoreGeneration || launch2.Status != launch1.Status {
		t.Fatalf("cached retry mismatch: %+v vs %+v", launch2, launch1)
	}

	// 3. FenceRestore bumps database generation
	_, err = op.FenceRestore(ctx, RestoreFenceRequest{
		RequestID: uuid.NewString(),
		Reason:    "disposable launch fence test",
	})
	if err != nil {
		t.Fatalf("FenceRestore: %v", err)
	}

	// 4. Cached retry with SAME request_id after fence: pre-replay guard MUST reject with FENCE_BREACH
	_, err = op.RegisterNativeOwnerLaunch(ctx, req)
	if err == nil {
		t.Fatalf("expected FenceRestore to cause refusal on cached retry, but succeeded")
	}
}

func TestOrganicNotices_ClaimOrganicNotice_ReplayGuard(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	// 1. Publish notice 1
	ev1 := f.publishNotice(t, "turn-1-notice")

	// 2. Claim notice 1 with stable claimReqID
	claimReqID := uuid.NewString()
	att, payload := f.signAttestation(t, "turn-root-replay-1", "owner_composer", "fresh_start", uuid.NewString(), time.Now().UTC().UnixMilli())
	claimRes, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             claimReqID,
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload.NativeTurnID,
		OwnerInputAttestation: &att,
	}, f.dest)
	if err != nil {
		t.Fatalf("ClaimOrganicNotice initial: %v", err)
	}
	if claimRes.Decision != "admitted" {
		t.Fatalf("expected admitted, got %s", claimRes.Decision)
	}

	// 3. Publish notice 2
	f.publishNotice(t, "turn-2-notice")

	// 4. Exact current retry with SAME request_id: recovers frozen root and does NOT admit notice 2
	claimRetry, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             claimReqID,
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload.NativeTurnID,
		OwnerInputAttestation: &att,
	}, f.dest)
	if err != nil {
		t.Fatalf("ClaimOrganicNotice retry: %v", err)
	}
	if claimRetry.RootID != claimRes.RootID || claimRetry.Decision != claimRes.Decision {
		t.Fatalf("expected identical recovered result, got %+v", claimRetry)
	}
	if claimRetry.Attempt.Delivery.Event.EventID != ev1.EventID {
		t.Fatalf("expected ev1 admitted in recovered attempt, got %s", claimRetry.Attempt.Delivery.Event.EventID)
	}

	// 5. Test replay guard on Execution Replacement:
	// Settle the active attempt before rotating execution
	_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  claimRes.Attempt.Delivery.DeliveryID,
		LeaseID:     claimRes.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("Complete item before rotate: %v", err)
	}
	_, err = f.recipient.ReconcileSessionInbox(ctx, SessionInboxReconcile{
		RequestID: uuid.NewString(),
		Session:   f.sessionRef,
		AttemptID: claimRes.Attempt.ID,
		Reason:    "delivery_completed",
	}, f.dest)
	if err != nil {
		t.Fatalf("ReconcileSessionInbox before rotate: %v", err)
	}

	// Rotate agent session to execution 2
	reg2, err := f.recipient.RegisterAgent(ctx, RegisterAgentRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Binding:         "codex-binding",
		NativeSessionID: f.nativeSessID,
		LaunchID:        f.launch.LaunchID,
		Metadata: AgentMetadata{
			Harness:      "codex",
			Project:      "test-proj",
			Workspace:    "/home/test",
			State:        "idle",
			DeliveryMode: "existing-session",
		},
	}, f.dest)
	if err != nil {
		t.Fatalf("RegisterAgent rotate: %v", err)
	}
	if reg2.ExecutionID == f.sessionRef.ExecutionID {
		t.Fatalf("expected new execution ID")
	}

	// Retrying old claim request_id against old or new session ref MUST FAIL via guard
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             claimReqID,
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload.NativeTurnID,
		OwnerInputAttestation: &att,
	}, f.dest)
	if err == nil {
		t.Fatalf("expected ClaimOrganicNotice to fail on rotated execution, but succeeded")
	}

	// 6. Test replay guard on Forget:
	repoForget := "repo-" + uuid.NewString()
	fForget := setupOrganicFixture(t, repoForget)
	fForget.activateCapability(t)
	evForget := fForget.publishNotice(t, "forget-notice")
	claimReqForgetID := uuid.NewString()
	attForget, payloadForget := fForget.signAttestation(t, "turn-forget-1", "owner_composer", "fresh_start", uuid.NewString(), time.Now().UTC().UnixMilli())
	_, err = fForget.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             claimReqForgetID,
		Session:               fForget.sessionRef,
		NativeOwnerTurnID:     payloadForget.NativeTurnID,
		OwnerInputAttestation: &attForget,
	}, fForget.dest)
	if err != nil {
		t.Fatalf("ClaimOrganicNotice initial forget test: %v", err)
	}

	// Operator forgets the source record
	opForget, root := testOperator(t)
	preview, err := opForget.PreviewDeletion(ctx, evForget.Ref.RecordID)
	if err != nil {
		t.Fatalf("PreviewDeletion: %v", err)
	}
	_, err = opForget.Forget(ctx, ForgetRequest{
		RequestID:       uuid.NewString(),
		RecordID:        evForget.Ref.RecordID,
		ExpectedVersion: 1,
		GrantID:         root.ID,
		PreviewID:       preview.PreviewID,
	})
	if err != nil {
		t.Fatalf("Forget: %v", err)
	}

	// Cached retry with SAME request_id MUST REFUSE with source unavailable
	_, err = fForget.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             claimReqForgetID,
		Session:               fForget.sessionRef,
		NativeOwnerTurnID:     payloadForget.NativeTurnID,
		OwnerInputAttestation: &attForget,
	}, fForget.dest)
	if err == nil {
		t.Fatalf("expected ClaimOrganicNotice cached retry to fail after source record Forget, but succeeded")
	}
}

func TestOrganicNotices_StrictWireValidation(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	ctx := context.Background()

	// 1. Long payload (> 2732 chars)
	longPayload := strings.Repeat("A", 2733)
	_, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:         uuid.NewString(),
		Session:           f.sessionRef,
		NativeOwnerTurnID: "turn-1",
		OwnerInputAttestation: &OwnerInputAttestation{
			Schema:    "cairn.owner-input-attestation/1",
			Payload:   longPayload,
			Signature: base64.RawURLEncoding.EncodeToString(make([]byte, 64)),
		},
	}, f.dest)
	if err == nil {
		t.Fatalf("expected error for long payload, got nil")
	}

	// 2. Bad signature length (!= 86 chars)
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:         uuid.NewString(),
		Session:           f.sessionRef,
		NativeOwnerTurnID: "turn-1",
		OwnerInputAttestation: &OwnerInputAttestation{
			Schema:    "cairn.owner-input-attestation/1",
			Payload:   base64.RawURLEncoding.EncodeToString([]byte(`{"launch_id":"` + f.launch.LaunchID + `"}`)),
			Signature: "short",
		},
	}, f.dest)
	if err == nil {
		t.Fatalf("expected error for bad signature length, got nil")
	}

	// 3. Trailing data after JSON in payload
	payloadWithTrailing := []byte(`{"launch_id":"` + f.launch.LaunchID + `"} extra`)
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:         uuid.NewString(),
		Session:           f.sessionRef,
		NativeOwnerTurnID: "turn-1",
		OwnerInputAttestation: &OwnerInputAttestation{
			Schema:    "cairn.owner-input-attestation/1",
			Payload:   base64.RawURLEncoding.EncodeToString(payloadWithTrailing),
			Signature: base64.RawURLEncoding.EncodeToString(make([]byte, 64)),
		},
	}, f.dest)
	if err == nil {
		t.Fatalf("expected error for trailing data in payload, got nil")
	}

	// 4. Negative / zero issued_at_unix_ms
	attBad, _ := f.signAttestation(t, "turn-bad-ts", "owner_composer", "fresh_start", uuid.NewString(), -100)
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     "turn-bad-ts",
		OwnerInputAttestation: &attBad,
	}, f.dest)
	if err == nil {
		t.Fatalf("expected error for negative timestamp, got nil")
	}
}

func TestOrganicNotices_MultiNoticeBurstAndCutoff(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)

	// Publish 3 notices ahead of turn
	for i := 1; i <= 3; i++ {
		_, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
			RequestID:       uuid.NewString(),
			Repo:            repo,
			Kind:            "notice",
			Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
			Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
			NoticeDelivery:  "next_organic_turn",
			NoticeExpiresAt: &expiresAt,
		}, f.dest)
		if err != nil {
			t.Fatal(err)
		}
	}

	// Claim turn 1 (fresh_start) -> creates Root R, admits notice 1
	att1, payload1 := f.signAttestation(t, "turn-cutoff-1", "owner_composer", "fresh_start", uuid.NewString(), time.Now().UTC().UnixMilli())
	claim1, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload1.NativeTurnID,
		OwnerInputAttestation: &att1,
	}, f.dest)
	if err != nil || claim1.Decision != "admitted" || claim1.Attempt == nil {
		t.Fatalf("claim1 failed: %+v %v", claim1, err)
	}

	// Brief pause to ensure subsequent publishes have created_at strictly after Root R cutoff
	time.Sleep(50 * time.Millisecond)

	// Publish 4th and 5th notices AFTER Root R cutoff
	for i := 4; i <= 5; i++ {
		_, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
			RequestID:       uuid.NewString(),
			Repo:            repo,
			Kind:            "notice",
			Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
			Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
			NoticeDelivery:  "next_organic_turn",
			NoticeExpiresAt: &expiresAt,
		}, f.dest)
		if err != nil {
			t.Fatal(err)
		}
	}

	// Settle notice 1:
	_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  claim1.Attempt.Delivery.DeliveryID,
		LeaseID:     claim1.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("Complete item 1: %v", err)
	}

	// Advance 1 -> admits notice 2:
	adv2, err := f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{
		RequestID: uuid.NewString(),
		Session:   f.sessionRef,
		AttemptID: claim1.Attempt.ID,
		RootID:    claim1.RootID,
		Version:   1,
	}, f.dest)
	if err != nil || adv2.Attempt == nil {
		t.Fatalf("advance2 failed: %+v %v", adv2, err)
	}

	// Settle notice 2:
	_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  adv2.Attempt.Delivery.DeliveryID,
		LeaseID:     adv2.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("Complete item 2: %v", err)
	}

	// Advance 2 -> admits notice 3:
	adv3, err := f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{
		RequestID: uuid.NewString(),
		Session:   f.sessionRef,
		AttemptID: adv2.Attempt.ID,
		RootID:    claim1.RootID,
		Version:   1,
	}, f.dest)
	if err != nil || adv3.Attempt == nil {
		t.Fatalf("advance3 failed: %+v %v", adv3, err)
	}

	// Settle notice 3:
	_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  adv3.Attempt.Delivery.DeliveryID,
		LeaseID:     adv3.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("Complete item 3: %v", err)
	}

	// Advance 3 -> burst limit (3) reached; notice 4 and 5 are NOT admitted
	adv4, err := f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{
		RequestID: uuid.NewString(),
		Session:   f.sessionRef,
		AttemptID: adv3.Attempt.ID,
		RootID:    claim1.RootID,
		Version:   1,
	}, f.dest)
	if err != nil {
		t.Fatalf("advance4 error: %v", err)
	}
	if adv4.Remainder != "batch_limit" {
		t.Fatalf("expected remainder batch_limit, got %s", adv4.Remainder)
	}
	if adv4.Attempt != nil {
		t.Fatalf("expected nil attempt after hitting burst limit")
	}

	// Now start a NEW turn (turn-cutoff-2) -> new root admits notice 4!
	att2, payload2 := f.signAttestation(t, "turn-cutoff-2", "owner_composer", "fresh_start", uuid.NewString(), time.Now().UTC().UnixMilli())
	claim2, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload2.NativeTurnID,
		OwnerInputAttestation: &att2,
	}, f.dest)
	if err != nil || claim2.Decision != "admitted" || claim2.Attempt == nil {
		t.Fatalf("claim2 on new turn failed: %+v %v", claim2, err)
	}
}

func TestOrganicNotices_SameTurnOwnerSteering(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)

	// Publish 2 notices
	for i := 1; i <= 2; i++ {
		_, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
			RequestID:       uuid.NewString(),
			Repo:            repo,
			Kind:            "notice",
			Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
			Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
			NoticeDelivery:  "next_organic_turn",
			NoticeExpiresAt: &expiresAt,
		}, f.dest)
		if err != nil {
			t.Fatal(err)
		}
	}

	// 1. fresh_start(A, T1) admits notice 1
	turnID := "turn-steering-1"
	inputIDA := uuid.NewString()
	attA, _ := f.signAttestation(t, turnID, "owner_composer", "fresh_start", inputIDA, time.Now().UTC().UnixMilli())
	claimReqIDA := uuid.NewString()

	claimA, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             claimReqIDA,
		Session:               f.sessionRef,
		NativeOwnerTurnID:     turnID,
		OwnerInputAttestation: &attA,
	}, f.dest)
	if err != nil || claimA.Decision != "admitted" || claimA.Attempt == nil {
		t.Fatalf("claimA failed: %+v %v", claimA, err)
	}

	// 2. Same turn T1 with signed joined(B, T2) (non-fresh / non-composer steering)
	inputIDB := uuid.NewString()
	attB, _ := f.signAttestation(t, turnID, "owner_composer", "joined", inputIDB, time.Now().UTC().UnixMilli())
	claimReqIDB := uuid.NewString()

	claimB, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             claimReqIDB,
		Session:               f.sessionRef,
		NativeOwnerTurnID:     turnID,
		OwnerInputAttestation: &attB,
	}, f.dest)
	if err != nil {
		t.Fatalf("claimB joined steering failed: %v", err)
	}
	if claimB.Attempt != nil {
		t.Fatalf("expected nil attempt for joined steering, got %+v", claimB.Attempt)
	}
	if claimB.Decision != "none" || claimB.Remainder != "non_composer_input" {
		t.Fatalf("expected Decision none, Remainder non_composer_input; got %+v", claimB)
	}

	// 3. Exact retry of fresh_start(A, T1) with original claimReqIDA recovers frozen outcome
	claimARetry, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             claimReqIDA,
		Session:               f.sessionRef,
		NativeOwnerTurnID:     turnID,
		OwnerInputAttestation: &attA,
	}, f.dest)
	if err != nil || claimARetry.Decision != "admitted" || claimARetry.Attempt == nil {
		t.Fatalf("claimARetry failed: %+v %v", claimARetry, err)
	}
	if claimARetry.Attempt.ID != claimA.Attempt.ID {
		t.Fatalf("expected recovered attempt ID %s, got %s", claimA.Attempt.ID, claimARetry.Attempt.ID)
	}

	// 4. Different fresh_start(C, T3) claiming same turnID -> CONFLICT
	inputIDC := uuid.NewString()
	attC, _ := f.signAttestation(t, turnID, "owner_composer", "fresh_start", inputIDC, time.Now().UTC().UnixMilli())
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     turnID,
		OwnerInputAttestation: &attC,
	}, f.dest)
	if err == nil {
		t.Fatalf("expected CONFLICT for different fresh_start on same turn, but got nil")
	}
}

func TestOrganicNotices_AdvanceReplayGuard_Forget(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)

	// Publish notice 1 and notice 2
	for i := 1; i <= 2; i++ {
		_, err := f.publisher.PublishEvent(ctx, PublishEventRequest{
			RequestID:       uuid.NewString(),
			Repo:            repo,
			Kind:            "notice",
			Ref:             RecordVersionRef{RecordID: f.source.RecordID, Version: f.source.Version},
			Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
			NoticeDelivery:  "next_organic_turn",
			NoticeExpiresAt: &expiresAt,
		}, f.dest)
		if err != nil {
			t.Fatal(err)
		}
	}

	// Claim notice 1
	att, payload := f.signAttestation(t, "turn-adv-forget", "owner_composer", "fresh_start", uuid.NewString(), time.Now().UTC().UnixMilli())
	claim1, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             uuid.NewString(),
		Session:               f.sessionRef,
		NativeOwnerTurnID:     payload.NativeTurnID,
		OwnerInputAttestation: &att,
	}, f.dest)
	if err != nil || claim1.Decision != "admitted" {
		t.Fatalf("claim1: %+v %v", claim1, err)
	}

	// Settle notice 1
	_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  claim1.Attempt.Delivery.DeliveryID,
		LeaseID:     claim1.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("Complete item 1: %v", err)
	}

	// Advance to notice 2
	advReqID := uuid.NewString()
	adv2, err := f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{
		RequestID: advReqID,
		Session:   f.sessionRef,
		AttemptID: claim1.Attempt.ID,
		RootID:    claim1.RootID,
		Version:   1,
	}, f.dest)
	if err != nil || adv2.Attempt == nil {
		t.Fatalf("adv2: %+v %v", adv2, err)
	}

	// Settle notice 2
	_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  adv2.Attempt.Delivery.DeliveryID,
		LeaseID:     adv2.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("Complete item 2: %v", err)
	}

	// Forget source record
	op, root := testOperator(t)
	preview, err := op.PreviewDeletion(ctx, f.source.RecordID)
	if err != nil {
		t.Fatalf("PreviewDeletion: %v", err)
	}
	_, err = op.Forget(ctx, ForgetRequest{
		RequestID:       uuid.NewString(),
		RecordID:        f.source.RecordID,
		ExpectedVersion: 1,
		GrantID:         root.ID,
		PreviewID:       preview.PreviewID,
	})
	if err != nil {
		t.Fatalf("Forget: %v", err)
	}

	// Cached replay of advReqID MUST FAIL via guard with NOT_FOUND
	_, err = f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{
		RequestID: advReqID,
		Session:   f.sessionRef,
		AttemptID: claim1.Attempt.ID,
		RootID:    claim1.RootID,
		Version:   1,
	}, f.dest)
	if err == nil {
		t.Fatalf("expected AdvanceOrganicNotice replay to fail after Forget, but succeeded")
	}
	if !strings.Contains(err.Error(), "NOT_FOUND") {
		t.Fatalf("expected NOT_FOUND error, got %v", err)
	}
}

func TestOrganicNotices_SettledForgottenAttemptRefusesNotFoundAcrossAttempts(t *testing.T) {
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixture(t, repo)
	f.activateCapability(t)
	ctx := context.Background()

	now := time.Now().UTC()
	expiresAt := now.Add(2 * time.Hour)

	// Create two separate memory records so we can forget one without affecting the other:
	draft1 := projectNote(repo)
	draft1.Sensitivity = "shareable"
	draft1.Body = "source text 1"
	record1, err := f.publisher.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft1})
	if err != nil {
		t.Fatal(err)
	}

	draft2 := projectNote(repo)
	draft2.Sensitivity = "shareable"
	draft2.Body = "source text 2"
	record2, err := f.publisher.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: draft2})
	if err != nil {
		t.Fatal(err)
	}

	// Publish Notice 1 bound to record1
	_, err = f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: record1.RecordID, Version: record1.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}

	// Publish Notice 2 bound to record2
	_, err = f.publisher.PublishEvent(ctx, PublishEventRequest{
		RequestID:       uuid.NewString(),
		Repo:            repo,
		Kind:            "notice",
		Ref:             RecordVersionRef{RecordID: record2.RecordID, Version: record2.Version},
		Destination:     EventDestination{Type: "agent", Name: "agent/" + f.sessionRef.AgentID},
		NoticeDelivery:  "next_organic_turn",
		NoticeExpiresAt: &expiresAt,
	}, f.dest)
	if err != nil {
		t.Fatal(err)
	}

	// Turn starts: ClaimOrganicNotice admits notice 1 (attempt 1)
	claimReqID1 := uuid.NewString()
	turnID := "turn-multi-source-1"
	att, _ := f.signAttestation(t, turnID, "owner_composer", "fresh_start", uuid.NewString(), time.Now().UTC().UnixMilli())

	claim1, err := f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             claimReqID1,
		Session:               f.sessionRef,
		NativeOwnerTurnID:     turnID,
		OwnerInputAttestation: &att,
	}, f.dest)
	if err != nil || claim1.Decision != "admitted" || claim1.Attempt == nil {
		t.Fatalf("claim1 failed: %+v %v", claim1, err)
	}

	// Settle attempt 1
	_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  claim1.Attempt.Delivery.DeliveryID,
		LeaseID:     claim1.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("Complete item 1: %v", err)
	}

	// Advance to notice 2 (attempt 2 on the SAME turn)
	advReqID2 := uuid.NewString()
	adv2, err := f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{
		RequestID: advReqID2,
		Session:   f.sessionRef,
		AttemptID: claim1.Attempt.ID,
		RootID:    claim1.RootID,
		Version:   1,
	}, f.dest)
	if err != nil || adv2.Attempt == nil {
		t.Fatalf("adv2 failed: %+v %v", adv2, err)
	}

	// Settle attempt 2
	_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  adv2.Attempt.Delivery.DeliveryID,
		LeaseID:     adv2.Attempt.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("Complete item 2: %v", err)
	}

	// Operator forgets record1 (the source of attempt 1)
	op, root := testOperator(t)
	preview1, err := op.PreviewDeletion(ctx, record1.RecordID)
	if err != nil {
		t.Fatalf("PreviewDeletion: %v", err)
	}
	_, err = op.Forget(ctx, ForgetRequest{
		RequestID:       uuid.NewString(),
		RecordID:        record1.RecordID,
		ExpectedVersion: 1,
		GrantID:         root.ID,
		PreviewID:       preview1.PreviewID,
	})
	if err != nil {
		t.Fatalf("Forget record1: %v", err)
	}

	// Replay of attempt 1 (claimReqID1) MUST REFUSE with NOT_FOUND via pre-replay guard,
	// strictly checking attempt 1's source without being fooled by visible attempt 2!
	_, err = f.recipient.ClaimOrganicNotice(ctx, SessionOrganicNoticeClaim{
		RequestID:             claimReqID1,
		Session:               f.sessionRef,
		NativeOwnerTurnID:     turnID,
		OwnerInputAttestation: &att,
	}, f.dest)
	if err == nil {
		t.Fatalf("expected ClaimOrganicNotice replay to fail with NOT_FOUND after record1 Forget, but succeeded")
	}
	if !strings.Contains(err.Error(), "NOT_FOUND") {
		t.Fatalf("expected NOT_FOUND error, got %v", err)
	}

	// In contrast, replaying attempt 2 (advReqID2) MUST SUCCEED because record2 is STILL VISIBLE!
	adv2Replay, err := f.recipient.AdvanceOrganicNotice(ctx, SessionOrganicNoticeAdvance{
		RequestID: advReqID2,
		Session:   f.sessionRef,
		AttemptID: claim1.Attempt.ID,
		RootID:    claim1.RootID,
		Version:   1,
	}, f.dest)
	if err != nil || adv2Replay.Attempt == nil {
		t.Fatalf("expected AdvanceOrganicNotice replay for attempt 2 to succeed, got %+v %v", adv2Replay, err)
	}
}

func TestOrganicNotices_WorkFeatureRetirement(t *testing.T) {
	_, openStore := waitTestDB(t)
	repo := "repo-" + uuid.NewString()
	f := setupOrganicFixtureWithStores(t, repo, openStore)
	f.activateCapability(t)
	ctx := context.Background()

	// 1. Negative test: feature in use with queued notice cannot be retired
	ev := f.publishNotice(t, "A payload")
	err := f.operator.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID:   uuid.NewString(),
		FeatureName: "next-turn-notices/1",
		Status:      "retired",
	})
	if err == nil {
		t.Fatalf("expected SetWorkFeatureStatus retired to fail while active notices exist, but succeeded")
	}
	if !strings.Contains(err.Error(), "INVALID_REQUEST") {
		t.Fatalf("expected INVALID_REQUEST error, got %v", err)
	}

	// 2. Disable feature
	err = f.operator.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID:   uuid.NewString(),
		FeatureName: "next-turn-notices/1",
		Status:      "disabled",
	})
	if err != nil {
		t.Fatalf("failed to disable feature: %v", err)
	}

	// 3. Flush pending notice
	flushRes, err := f.publisher.FlushNotice(ctx, NoticeFlushRequest{
		RequestID: uuid.NewString(),
		EventID:   ev.EventID,
	}, f.dest)
	if err != nil || flushRes.Released != 1 {
		t.Fatalf("FlushNotice failed: released=%d err=%v", flushRes.Released, err)
	}

	// Settle the flushed delivery via consumerStore
	cStore := f.consumerStore(t)
	next, err := cStore.NextEvent(ctx, NextEventRequest{
		Repo:  repo,
		Agent: "agent/" + f.sessionRef.AgentID,
	}, f.dest)
	if err != nil || next.Delivery == nil {
		t.Fatalf("NextEvent for flushed delivery: %v", err)
	}
	_, err = cStore.CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   uuid.NewString(),
		DeliveryID:  next.Delivery.DeliveryID,
		LeaseID:     next.Delivery.LeaseID,
		Disposition: "handled",
	}, f.dest)
	if err != nil {
		t.Fatalf("CompleteEvent for flushed delivery: %v", err)
	}

	// 4. Positive test: once settled/flushed, retirement succeeds
	err = f.operator.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID:   uuid.NewString(),
		FeatureName: "next-turn-notices/1",
		Status:      "retired",
	})
	if err != nil {
		t.Fatalf("SetWorkFeatureStatus retired failed: %v", err)
	}
	feats, err := f.operator.ListWorkFeatures(ctx)
	if err != nil {
		t.Fatalf("ListWorkFeatures failed: %v", err)
	}
	foundRetired := false
	for _, ft := range feats {
		if ft.FeatureName == "next-turn-notices/1" && ft.Status == "retired" && ft.RetiredAt != nil {
			foundRetired = true
		}
	}
	if !foundRetired {
		t.Fatalf("expected next-turn-notices/1 to be retired with RetiredAt populated")
	}

	// Attempting to re-enable retired feature fails with FEATURE_RETIRED
	err = f.operator.SetWorkFeatureStatus(ctx, SetWorkFeatureStatusRequest{
		RequestID:   uuid.NewString(),
		FeatureName: "next-turn-notices/1",
		Status:      "active",
	})
	if err == nil || !strings.Contains(err.Error(), "FEATURE_RETIRED") {
		t.Fatalf("expected FEATURE_RETIRED error, got %v", err)
	}
}
