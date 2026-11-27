package core

import (
	"context"
	"encoding/json"
	"math"
	"os"
	"regexp"
	"testing"
	"time"
)

type swarmBoardFixture struct {
	BoardCommit string    `json:"board_commit"`
	PublishedAt time.Time `json:"published_at"`
	Cards       []struct {
		Name            string
		Body            string
		Valid           bool
		LifetimeSeconds float64 `json:"lifetime_seconds"`
		DefaultDeclared bool    `json:"default_declared"`
		Slot            string
	}
	Slots []struct {
		Name   string
		Events []struct {
			ID         string
			Sender     string
			Kind       string
			Body       string
			AgeSeconds int `json:"age_seconds"`
		}
		LiveRequests []string `json:"live_requests"`
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
	for _, tc := range corpus.Cards {
		t.Run(tc.Name, func(t *testing.T) {
			var block string
			if parts := fence.FindStringSubmatch(tc.Body); parts != nil {
				block = parts[1]
			}
			got := swarmBoardCard(block, corpus.PublishedAt)
			checkSwarmBoardCardFacts(t, got, corpus.PublishedAt, tc.Valid, tc.LifetimeSeconds, tc.DefaultDeclared, tc.Slot)
		})
	}
}

func checkSwarmBoardCardFacts(t *testing.T, got swarmBoardCardFacts, published time.Time, valid bool, lifetime float64, defaultDeclared bool, slot string) {
	t.Helper()
	gotLifetime := got.expires.Sub(published).Seconds()
	// The board computes from floating-point Unix timestamps.
	if got.valid != valid || math.Abs(gotLifetime-lifetime) > 1e-6 || got.defaultDeclared != defaultDeclared || got.slot != slot {
		t.Errorf("got valid=%t lifetime=%vs default=%t slot=%q, want valid=%t lifetime=%vs default=%t slot=%q",
			got.valid, gotLifetime, got.defaultDeclared, got.slot, valid, lifetime, defaultDeclared, slot)
	}
}

func TestSwarmBoardCardPostgresCorpus(t *testing.T) {
	corpus := readSwarmBoardFixture(t)
	op := newNativeFixture(t).operator()
	for _, tc := range corpus.Cards {
		t.Run(tc.Name, func(t *testing.T) {
			var block string
			if err := op.pool.QueryRow(context.Background(),
				`SELECT COALESCE(substring($1::text FROM $2::text), '')`, tc.Body, swarmBoardCardBlock).Scan(&block); err != nil {
				t.Fatal(err)
			}
			got := swarmBoardCard(block, corpus.PublishedAt)
			checkSwarmBoardCardFacts(t, got, corpus.PublishedAt, tc.Valid, tc.LifetimeSeconds, tc.DefaultDeclared, tc.Slot)
		})
	}
}

func TestSwarmNeedsBoardReviewerCases(t *testing.T) {
	corpus := readSwarmBoardFixture(t)
	f := newSwarmFixture(t, "board-reviewer-cases")
	cases := map[string]time.Duration{
		"expires_reviewer_compact_offset": 25 * time.Hour,
		"reviewer_invalid_default":        2 * time.Hour,
	}

	foundCases := 0
	for _, tc := range corpus.Cards {
		age, ok := cases[tc.Name]
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
		r := swarmNeedsReader{repo: f.repo, now: corpus.PublishedAt.Add(age), principals: []string{f.root.Inbox}, out: emptySwarmNeedsYou()}
		items, readErr := r.board(context.Background(), tx)
		if err := tx.Rollback(context.Background()); err != nil {
			t.Fatal(err)
		}
		if readErr != nil {
			t.Fatal(readErr)
		}
		expiry := corpus.PublishedAt.Add(time.Duration(tc.LifetimeSeconds * float64(time.Second)))
		found := false
		for _, item := range items {
			if item.Refs.EventID != event.EventID {
				continue
			}
			found = true
			if item.Detail != "" || item.ExpiresAt == nil || !item.ExpiresAt.Equal(expiry) {
				t.Errorf("%s: got %+v, want expiry %s and no default", tc.Name, item, expiry)
			}
		}
		if !found {
			t.Errorf("%s: request missing at age %s", tc.Name, age)
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
			senders := map[string]*Store{}
			eventIDs := map[string]string{}
			for _, entry := range tc.Events {
				view := senders[entry.Sender]
				if view == nil {
					_, view = f.member(entry.Sender)
					senders[entry.Sender] = view
				}
				event := swarmNeedsTestPublish(f, view, entry.Kind, "topic", "agent-board", entry.Body, "")
				f.exec(`UPDATE cairn.agent_event SET created_at=$2 WHERE event_id=$1`, event.EventID,
					event.CreatedAt.Add(-time.Duration(entry.AgeSeconds)*time.Second))
				eventIDs[entry.ID] = event.EventID
			}
			want := map[string]bool{}
			for _, id := range tc.LiveRequests {
				want[eventIDs[id]] = true
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
