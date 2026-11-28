package core

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"regexp"
	"testing"
	"time"

	"github.com/google/uuid"
)

type swarmBoardCase struct {
	Name            string
	Body            string
	Valid           bool
	ExpiresEpoch    float64 `json:"expires_epoch"`
	DefaultDeclared bool    `json:"default_declared"`
	Slot            string
	Documented      bool
}

func (tc swarmBoardCase) testName(index int) string {
	if tc.Name != "" {
		return tc.Name
	}
	return fmt.Sprintf("generated_%04d", index)
}

type swarmBoardFixture struct {
	BoardCommit string    `json:"board_commit"`
	PublishedAt time.Time `json:"published_at"`
	Cards       []swarmBoardCase
	Slots       []struct {
		Name   string
		Events []struct {
			ID         string
			Sender     string
			Kind       string
			Body       string
			AgeSeconds int `json:"age_seconds"`
		}
		LiveRequests         []string `json:"live_requests"`
		AnsweredRequests     []string `json:"answered_requests"`
		RetainConservatively []string `json:"retain_conservatively"`
	}
}

func readSwarmBoardFixture(t *testing.T) swarmBoardFixture {
	t.Helper()
	raw, err := os.ReadFile("testdata/swarm_board_cards.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus swarmBoardFixture
	if err := json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	const pinnedBoardCommit = "5ccacf9768ff872ad7f6c822d96967b09b153fb7"
	if corpus.BoardCommit != pinnedBoardCommit || corpus.PublishedAt.IsZero() || len(corpus.Cards) == 0 || len(corpus.Slots) == 0 {
		t.Fatal("incomplete board fixture or unexpected board commit")
	}
	return corpus
}

func TestSwarmBoardCardCorpus(t *testing.T) {
	corpus := readSwarmBoardFixture(t)
	fence := regexp.MustCompile("(?ms)" + swarmBoardCardFence)
	counts := map[swarmBoardCertainty]int{}
	for i, tc := range corpus.Cards {
		t.Run(tc.testName(i), func(t *testing.T) {
			var block *string
			if parts := fence.FindStringSubmatch(tc.Body); parts != nil {
				block = &parts[1]
			}
			got := swarmBoardCard(block, corpus.PublishedAt)
			counts[got.certainty]++
			checkSwarmBoardCardFacts(t, got, corpus.PublishedAt, tc)
		})
	}
	t.Logf("Board corpus: %d CERTAIN (%d valid, %d cardless), %d UNCERTAIN",
		counts[swarmBoardCertainValid]+counts[swarmBoardCertainCardless],
		counts[swarmBoardCertainValid], counts[swarmBoardCertainCardless], counts[swarmBoardUncertain])
}

func checkSwarmBoardCardFacts(t *testing.T, got swarmBoardCardFacts, published time.Time, tc swarmBoardCase) {
	t.Helper()
	gotLifetime := got.keepUntil.Sub(published).Seconds()
	// Compare removal times at the board's floating-point clock precision.
	boardClock := func(at time.Time) float64 {
		return float64(at.Unix()) + float64(at.Nanosecond())/float64(time.Second)
	}
	gotEpoch := boardClock(got.keepUntil)
	if gotEpoch < tc.ExpiresEpoch {
		t.Errorf("removes at epoch %.12f before the board's %.12f", gotEpoch, tc.ExpiresEpoch)
	}
	if gotLifetime > swarmBoardMaxTTL.Seconds() {
		t.Errorf("retention %v seconds exceeds the 72-hour read horizon", gotLifetime)
	}
	const tolerance = 1e-6
	if got.certainty == swarmBoardUncertain {
		if tc.Documented {
			t.Error("documented card was classified as uncertain")
		}
		if got.defaultDeclared || got.slot != "" {
			t.Errorf("uncertain card supplied a default or slot: %+v", got)
		}
		return
	}
	if (got.certainty == swarmBoardCertainValid) != tc.Valid || math.Abs(gotEpoch-tc.ExpiresEpoch) > tolerance ||
		got.defaultDeclared != tc.DefaultDeclared || got.slot != tc.Slot {
		t.Errorf("got status=%d lifetime=%vs default=%t slot=%q, want valid=%t lifetime=%vs default=%t slot=%q",
			got.certainty, gotLifetime, got.defaultDeclared, got.slot, tc.Valid, tc.ExpiresEpoch-boardClock(published), tc.DefaultDeclared, tc.Slot)
	}
}

func TestSwarmBoardCardPostgresCorpus(t *testing.T) {
	corpus := readSwarmBoardFixture(t)
	op := newNativeFixture(t).operator()
	for i, tc := range corpus.Cards {
		t.Run(tc.testName(i), func(t *testing.T) {
			var block *string
			if err := op.pool.QueryRow(context.Background(),
				`SELECT substring($1::text FROM $2::text)`, tc.Body, swarmBoardCardBlock).Scan(&block); err != nil {
				t.Fatal(err)
			}
			got := swarmBoardCard(block, corpus.PublishedAt)
			checkSwarmBoardCardFacts(t, got, corpus.PublishedAt, tc)
		})
	}
}

func TestSwarmNeedsBoardReviewerCases(t *testing.T) {
	corpus := readSwarmBoardFixture(t)
	f := newSwarmFixture(t, "board-reviewer-cases")
	type reviewerCase struct {
		age       time.Duration
		uncertain bool
	}
	cases := map[string]reviewerCase{
		"expires_reviewer_compact_offset": {25 * time.Hour, false},
		"reviewer_invalid_default":        {2 * time.Hour, false},
		"reviewer_ambiguous_week_date":    {2 * time.Hour, true},
		"reviewer_numeric_slot":           {25 * time.Hour, true},
		"reviewer_epoch_1e_10":            {25 * time.Hour, true},
		"reviewer_epoch_1e_12":            {25 * time.Hour, true},
		"reviewer_epoch_4_9e_10":          {25 * time.Hour, true},
		"numeric_epoch_zero_ttl_86400":    {23 * time.Hour, false},
		"string_epoch_zero_ttl_86400":     {23 * time.Hour, false},
		"negative_zero_exponent":          {23 * time.Hour, false},
		"zero_extra_precision":            {23 * time.Hour, false},
	}

	foundCases := 0
	for _, tc := range corpus.Cards {
		example, ok := cases[tc.Name]
		if !ok {
			continue
		}
		foundCases++
		event := swarmNeedsTestPublish(f, f.rootView, "request", "topic", "agent-board", tc.Body, "")
		// Keep the corpus's absolute timestamp and relative lifetime intact.
		f.exec(`UPDATE cairn.agent_event SET created_at=$2 WHERE event_id=$1`, event.EventID, corpus.PublishedAt)
		tx, err := f.op.pool.Begin(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		r := swarmNeedsReader{repo: f.repo, now: corpus.PublishedAt.Add(example.age), principals: []string{f.root.Inbox}, out: emptySwarmNeedsYou()}
		items, readErr := r.board(context.Background(), tx)
		if err := tx.Rollback(context.Background()); err != nil {
			t.Fatal(err)
		}
		if readErr != nil {
			t.Fatal(readErr)
		}
		expiry := time.UnixMicro(int64(math.Round(tc.ExpiresEpoch * float64(time.Second/time.Microsecond))))
		found := false
		for _, item := range items {
			if item.Refs.EventID != event.EventID {
				continue
			}
			found = true
			if example.uncertain {
				if item.Detail != "Expiry not known exactly" || item.ExpiresAt != nil {
					t.Errorf("%s: got %+v, want unknown expiry and no default", tc.Name, item)
				}
			} else if item.Detail != "" || item.ExpiresAt == nil || !item.ExpiresAt.Equal(expiry) {
				t.Errorf("%s: got %+v, want expiry %s and no default", tc.Name, item, expiry)
			}
		}
		if !found {
			t.Errorf("%s: request missing at age %s", tc.Name, example.age)
		}
	}
	if foundCases != len(cases) {
		t.Fatal("reviewer cases missing from the fixture")
	}
}

func TestSwarmNeedsBoardSlots(t *testing.T) {
	corpus := readSwarmBoardFixture(t)
	for _, tc := range corpus.Slots {
		t.Run(tc.Name, func(t *testing.T) {
			f := newSwarmFixture(t, "board-slots")
			if len(tc.AnsweredRequests) != 0 {
				if _, err := f.sender.SetSubscription(context.Background(), SubscriptionRequest{
					RequestID: uuid.NewString(), Repo: f.repo, Topic: "agent-board", Active: true,
				}); err != nil {
					t.Fatal(err)
				}
			}
			senders := map[string]*Store{}
			events := map[string]AgentEvent{}
			for _, entry := range tc.Events {
				view := senders[entry.Sender]
				if view == nil {
					_, view = f.member(entry.Sender)
					senders[entry.Sender] = view
				}
				event := swarmNeedsTestPublish(f, view, entry.Kind, "topic", "agent-board", entry.Body, "")
				f.exec(`UPDATE cairn.agent_event SET created_at=$2 WHERE event_id=$1`, event.EventID,
					event.CreatedAt.Add(-time.Duration(entry.AgeSeconds)*time.Second))
				events[entry.ID] = event
			}
			for _, id := range tc.AnsweredRequests {
				event := events[id]
				swarmNeedsTestPublish(f, f.sender, "response", "agent", event.From, "Board answer: Yes", event.EventID)
			}
			want := map[string]bool{}
			for _, ids := range [][]string{tc.LiveRequests, tc.RetainConservatively} {
				for _, id := range ids {
					want[events[id].EventID] = true
				}
			}
			for _, item := range swarmNeedsTestSnapshot(f).Items {
				if item.Type != SwarmNeedBoardQuestion {
					continue
				}
				if !want[item.Refs.EventID] {
					t.Errorf("unexpected live question: %+v", item)
				}
				delete(want, item.Refs.EventID)
			}
			if len(want) != 0 {
				t.Errorf("missing live questions: %v", want)
			}
		})
	}
}

func TestSwarmNeedsBoardQuestionSystemOrigin(t *testing.T) {
	ctx := context.Background()
	f := newSwarmFixture(t, "board-system-origin")
	agentQuestion := swarmNeedsTestPublish(f, f.rootView, "request", "topic", "agent-board", swarmNeedsTestBoard, "")
	scheduled, err := f.op.ScheduleEvent(ctx, ScheduleEventRequest{
		RequestID: uuid.NewString(), NotBefore: time.Now().Add(-time.Second), GraceSeconds: 300,
		Publication: PublishEventRequest{
			Repo: f.repo, Kind: "request", Ref: RecordVersionRef{f.source.RecordID, f.source.Version},
			Destination: EventDestination{Type: "topic", Name: "agent-board"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	tick, err := f.op.TickSchedules(ctx, ScheduleTickRequest{Repo: f.repo})
	if err != nil || len(tick.Occurrences) != 1 || tick.Occurrences[0].EventID != scheduled.OccurrenceID {
		t.Fatalf("fire the schedule: %+v, %v", tick, err)
	}
	check := func(needs SwarmNeedsYou, eventID, wantSystem string) {
		t.Helper()
		for _, item := range needs.Items {
			if item.Type == SwarmNeedBoardQuestion && item.Refs.EventID == eventID {
				swarmRequireSystemJSON(t, item, wantSystem)
				if item.System != wantSystem {
					t.Errorf("board question %s has system %q, want %q", eventID, item.System, wantSystem)
				}
				if wantSystem != "" && item.Title != "Board question from Cairn" {
					t.Errorf("scheduled question has title %q, want a Cairn attribution", item.Title)
				}
				return
			}
		}
		t.Fatalf("board question %s is missing", eventID)
	}
	check(swarmNeedsTestLanding(f), scheduled.OccurrenceID, "scheduled_event")
	check(swarmNeedsTestSnapshot(f), agentQuestion.EventID, "")
}
