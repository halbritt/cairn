package core

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"errors"
	"strings"

	"github.com/jackc/pgx/v5"
)

type RegisterNativeOwnerLaunchRequest struct {
	RequestID           string `json:"request_id"`
	LaunchID            string `json:"launch_id"`
	Repo                string `json:"repo"`
	ProfilePrincipal    string `json:"profile_principal"`
	Binding             string `json:"binding"`
	PublicKey           string `json:"public_key"`
	AttestationContract string `json:"attestation_contract,omitempty"`
	OriginContract      string `json:"origin_contract"`
	OriginVersion       int    `json:"origin_version"`
	UpstreamCommit      string `json:"upstream_commit"`
	PatchSHA256         string `json:"patch_sha256"`
	RuntimeSHA256       string `json:"runtime_sha256"`
}

type NativeOwnerLaunch struct {
	LaunchID          string `json:"launch_id"`
	RestoreGeneration int64  `json:"restore_generation"`
	Status            string `json:"status"`
}

func isLowerHex(s string, expectedLen int) bool {
	if len(s) != expectedLen {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			return false
		}
	}
	return true
}

func (s *Store) RegisterNativeOwnerLaunch(ctx context.Context, req RegisterNativeOwnerLaunchRequest) (NativeOwnerLaunch, error) {
	var out NativeOwnerLaunch
	if !s.channel.Operator || s.channel.Repo != "" {
		return out, failure("AUTHORITY_DENIED", "native owner launch registration requires an unscoped operator channel")
	}
	if err := validID(req.RequestID); err != nil {
		return out, err
	}
	if err := validID(req.LaunchID); err != nil {
		return out, err
	}
	if strings.TrimSpace(req.Repo) == "" || req.Repo == "*" || len(req.Repo) > 256 {
		return out, failure("INVALID_REQUEST", "repo required")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return out, err
	}
	if strings.TrimSpace(req.ProfilePrincipal) == "" || len(req.ProfilePrincipal) > 256 {
		return out, failure("INVALID_REQUEST", "profile_principal required")
	}
	if strings.TrimSpace(req.Binding) == "" || len(req.Binding) > 256 {
		return out, failure("INVALID_REQUEST", "binding required")
	}
	if strings.Contains(req.PublicKey, "=") {
		return out, failure("INVALID_REQUEST", "public_key must use unpadded base64url")
	}
	pubKeyBytes, err := base64.RawURLEncoding.DecodeString(req.PublicKey)
	if err != nil || len(pubKeyBytes) != ed25519.PublicKeySize {
		return out, failure("INVALID_REQUEST", "public_key must be a 32-byte Ed25519 public key encoded in base64url")
	}
	attestationContract := req.AttestationContract
	if attestationContract == "" {
		attestationContract = "cairn.owner-input-attestation/1"
	}
	if attestationContract != "cairn.owner-input-attestation/1" {
		return out, failure("INVALID_REQUEST", "attestation_contract must be cairn.owner-input-attestation/1")
	}
	if req.OriginContract != "codex.owner-input/1" {
		return out, failure("INVALID_REQUEST", "origin_contract must be codex.owner-input/1")
	}
	if req.OriginVersion != 1 {
		return out, failure("INVALID_REQUEST", "origin_version must be 1")
	}
	if !isLowerHex(req.UpstreamCommit, 40) {
		return out, failure("INVALID_REQUEST", "upstream_commit must be 40 lowercase hex characters")
	}
	if !isLowerHex(req.PatchSHA256, 64) {
		return out, failure("INVALID_REQUEST", "patch_sha256 must be 64 lowercase hex characters")
	}
	if !isLowerHex(req.RuntimeSHA256, 64) {
		return out, failure("INVALID_REQUEST", "runtime_sha256 must be 64 lowercase hex characters")
	}

	guard := func(tx pgx.Tx) error {
		if err := s.checkFeatureEnabledTx(ctx, tx, "next-turn-notices/1"); err != nil {
			return err
		}
		currentGen, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return err
		}
		var launchGen int64
		var status string
		err = tx.QueryRow(ctx, `SELECT restore_generation, status FROM cairn.agent_native_owner_launch WHERE launch_id = $1`, req.LaunchID).Scan(&launchGen, &status)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		if status != "active" {
			return failure("AUTHORITY_DENIED", "launch is not active")
		}
		if launchGen != currentGen {
			return failure("FENCE_BREACH", "launch restore generation does not match current store generation")
		}
		return nil
	}

	return privileged[NativeOwnerLaunch](ctx, s, "native-owner-launch-register", req.RequestID, req, func(tx pgx.Tx) (NativeOwnerLaunch, error) {
		if err := s.checkFeatureEnabledTx(ctx, tx, "next-turn-notices/1"); err != nil {
			return NativeOwnerLaunch{}, err
		}
		currentGen, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return NativeOwnerLaunch{}, err
		}

		var existing struct {
			repo                string
			profilePrincipal    string
			binding             string
			publicKey           []byte
			attestationContract string
			originContract      string
			originVersion       int
			upstreamCommit      string
			patchSHA256         string
			runtimeSHA256       string
			status              string
			restoreGeneration   int64
		}
		err = tx.QueryRow(ctx, `SELECT repo, profile_principal, binding, public_key, attestation_contract, origin_contract, origin_version, upstream_commit, patch_sha256, runtime_sha256, status, restore_generation FROM cairn.agent_native_owner_launch WHERE launch_id = $1`, req.LaunchID).
			Scan(&existing.repo, &existing.profilePrincipal, &existing.binding, &existing.publicKey, &existing.attestationContract, &existing.originContract, &existing.originVersion, &existing.upstreamCommit, &existing.patchSHA256, &existing.runtimeSHA256, &existing.status, &existing.restoreGeneration)

		if err == nil {
			if existing.repo == req.Repo &&
				existing.profilePrincipal == req.ProfilePrincipal &&
				existing.binding == req.Binding &&
				bytes.Equal(existing.publicKey, pubKeyBytes) &&
				existing.attestationContract == attestationContract &&
				existing.originContract == req.OriginContract &&
				existing.originVersion == req.OriginVersion &&
				existing.upstreamCommit == req.UpstreamCommit &&
				existing.patchSHA256 == req.PatchSHA256 &&
				existing.runtimeSHA256 == req.RuntimeSHA256 &&
				existing.restoreGeneration == currentGen &&
				existing.status == "active" {
				return NativeOwnerLaunch{
					LaunchID:          req.LaunchID,
					RestoreGeneration: existing.restoreGeneration,
					Status:            existing.status,
				}, nil
			}
			return NativeOwnerLaunch{}, failure("CONFLICT", "launch registration is immutable; changed identity, artifact, or generation under same launch_id")
		} else if !errors.Is(err, pgx.ErrNoRows) {
			return NativeOwnerLaunch{}, err
		}

		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_native_owner_launch (
			launch_id, repo, profile_principal, binding, public_key, attestation_contract, origin_contract, origin_version,
			upstream_commit, patch_sha256, runtime_sha256, status, restore_generation, registered_by, created_at
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'active', $12, $13, clock_timestamp())`,
			req.LaunchID, req.Repo, req.ProfilePrincipal, req.Binding, pubKeyBytes, attestationContract, req.OriginContract, req.OriginVersion,
			req.UpstreamCommit, req.PatchSHA256, req.RuntimeSHA256, currentGen, s.channel.Principal)
		if err != nil {
			return NativeOwnerLaunch{}, err
		}

		return NativeOwnerLaunch{
			LaunchID:          req.LaunchID,
			RestoreGeneration: currentGen,
			Status:            "active",
		}, nil
	}, guard)
}
