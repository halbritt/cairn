package core

import (
	"context"
	"testing"
	"time"

	"github.com/google/uuid"
)

// A second request for a frozen turn can return an attempt created by the first
// request. Its cached disclosure must follow that attempt, not the retry UUID.
func TestOrganicClaimAliasRechecksFrozenSource(t *testing.T) {
	for _, scenario := range []struct {
		name             string
		cacheAlias       bool
		advance          bool
		retractSuccessor bool
	}{
		{name: "cached_alias", cacheAlias: true},
		{name: "first_same_root_return"},
		{name: "visible_successor_cannot_authorize_frozen_source", cacheAlias: true, advance: true},
		{name: "hidden_successor_cannot_restrict_frozen_source", cacheAlias: true, advance: true, retractSuccessor: true},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			f := setupOrganicFixture(t, "organic-alias-"+uuid.NewString())
			f.activateCapability(t)
			event := f.publishNotice(t, "source withdrawn after aliased claim")
			var successor AgentEvent
			if scenario.advance {
				successor = f.publishNotice(t, "independent source for successor")
			}
			attestation, payload := f.signAttestation(t, "alias-turn", "owner_composer", "fresh_start", uuid.NewString(), time.Now().UnixMilli())
			firstRequest := SessionOrganicNoticeClaim{
				RequestID: uuid.NewString(), Session: f.sessionRef,
				NativeOwnerTurnID: payload.NativeTurnID, OwnerInputAttestation: &attestation,
			}
			first, err := f.recipient.ClaimOrganicNotice(ctx, firstRequest, f.dest)
			if err != nil {
				t.Fatal(err)
			}
			if first.Attempt == nil || first.Attempt.ID != firstRequest.RequestID || first.Attempt.Delivery.Event.EventID != event.EventID {
				t.Fatalf("first claim did not admit exact event: %+v", first)
			}
			aliasRequest := firstRequest
			aliasRequest.RequestID = uuid.NewString()
			if scenario.cacheAlias {
				alias, err := f.recipient.ClaimOrganicNotice(ctx, aliasRequest, f.dest)
				if err != nil {
					t.Fatal(err)
				}
				if alias.RootID != first.RootID || alias.Attempt == nil || alias.Attempt.ID != first.Attempt.ID || alias.Attempt.ID == aliasRequest.RequestID {
					t.Fatalf("same-root claim did not return the original attempt: %+v", alias)
				}
			}
			if scenario.advance {
				_, err = f.consumerStore(t).CompleteEvent(ctx, CompleteEventRequest{
					RequestID: uuid.NewString(), DeliveryID: first.Attempt.Delivery.DeliveryID,
					LeaseID: first.Attempt.Delivery.LeaseID, Disposition: "handled",
				}, f.dest)
				if err != nil {
					t.Fatal(err)
				}
				advanceRequest := SessionOrganicNoticeAdvance{
					RequestID: uuid.NewString(), Session: f.sessionRef,
					AttemptID: first.Attempt.ID, RootID: first.RootID, Version: 1,
				}
				advanced, err := f.recipient.AdvanceOrganicNotice(ctx, advanceRequest, f.dest)
				if err != nil {
					t.Fatal(err)
				}
				if advanced.Attempt == nil || advanced.Attempt.ID != advanceRequest.RequestID ||
					advanced.Attempt.ID == first.Attempt.ID || advanced.Attempt.OrganicRoot != first.RootID ||
					advanced.Attempt.BatchIndex != 2 || advanced.Attempt.Delivery.Event.EventID != successor.EventID {
					t.Fatalf("advance did not admit the exact independent successor: %+v", advanced)
				}
			}
			withdrawn := event.Ref
			if scenario.retractSuccessor {
				withdrawn = successor.Ref
			}
			retractOrganicClaimSource(t, ctx, withdrawn)
			before := organicClaimEffectCounts(t, ctx, f, first.RootID)
			expectedAdmissions := 1
			if scenario.advance {
				expectedAdmissions = 2
			}
			if before[0] != expectedAdmissions || before[1] != expectedAdmissions || before[2] != expectedAdmissions {
				t.Fatalf("unexpected admission effects before retry: %+v", before)
			}
			replayed, err := f.recipient.ClaimOrganicNotice(ctx, aliasRequest, f.dest)
			if scenario.retractSuccessor {
				if err != nil {
					t.Fatal(err)
				}
				if replayed.Attempt == nil || replayed.Attempt.ID != first.Attempt.ID || replayed.RootID != first.RootID ||
					replayed.Attempt.Delivery.Event.EventID != event.EventID || replayed.Attempt.BatchIndex != 1 {
					t.Fatalf("replay substituted the live successor for the frozen attempt: %+v", replayed)
				}
			} else {
				requireCode(t, err, "NOT_FOUND")
			}
			if after := organicClaimEffectCounts(t, ctx, f, first.RootID); after != before {
				t.Fatalf("replay changed committed effects: before=%+v after=%+v", before, after)
			}
		})
	}
}

func retractOrganicClaimSource(t *testing.T, ctx context.Context, source RecordVersionRef) {
	t.Helper()
	op, grant := testOperator(t)
	preview, err := op.PreviewRetraction(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.Retract(ctx, RetractRequest{
		RequestID: uuid.NewString(), RecordID: source.RecordID, ExpectedVersion: source.Version,
		GrantID: grant.ID, PreviewID: preview.PreviewID, Reason: "Withdraw synthetic organic notice source",
	})
	if err != nil {
		t.Fatal(err)
	}
}

func organicClaimEffectCounts(t *testing.T, ctx context.Context, f *organicFixture, rootID string) [5]int {
	t.Helper()
	var counts [5]int
	err := f.operator.pool.QueryRow(ctx, `SELECT
		(SELECT count(*) FROM cairn.agent_session_attempt WHERE organic_root=$1),
		(SELECT count(*) FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$2),
		(SELECT admitted_count FROM cairn.agent_organic_notice_turn_root WHERE root_id=$1),
		(SELECT count(*) FROM cairn.agent_organic_notice_turn_root WHERE repo=$2),
		(SELECT count(*) FROM cairn.mutation_request WHERE caller=$3 AND operation='session-organic-notice-claim')`,
		rootID, f.repo, f.recipient.channel.Principal).
		Scan(&counts[0], &counts[1], &counts[2], &counts[3], &counts[4])
	if err != nil {
		t.Fatal(err)
	}
	return counts
}
