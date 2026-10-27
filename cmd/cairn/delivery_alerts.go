package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"html"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/halbritt/cairn/core"
)

// delivery-alerts tells the owner once per new starvation condition. It reads
// the same review rows as coordination-review attention, never claims,
// releases or reissues a delivery, and records only alert bookkeeping.

const deliveryAlertSchema = "cairn.delivery-alerts/1"

type deliveryAlertsRequest struct {
	Repo         string                 `json:"repo"`
	StateFile    string                 `json:"state_file,omitempty"`
	PlaneProject string                 `json:"plane_project,omitempty"`
	MaxNew       int                    `json:"max_new,omitempty"`
	MaxPages     int                    `json:"max_pages,omitempty"`
	Starvation   core.StarvationOptions `json:"starvation,omitempty"`
}

type deliveryAlertLimits struct {
	MaxNew int
}

type deliveryAlertEntry struct {
	Recipient       string    `json:"recipient"`
	ExecutionID     string    `json:"execution_id,omitempty"`
	Condition       string    `json:"condition"`
	OnsetDeliveryID string    `json:"onset_delivery_id"`
	ExternalID      string    `json:"external_id"`
	AlertedAt       time.Time `json:"alerted_at"`
	Ref             string    `json:"ref"`
}

type deliveryAlertState struct {
	Schema string                        `json:"schema"`
	Scope  string                        `json:"scope"`
	Alerts map[string]deliveryAlertEntry `json:"alerts"`
}

type deliveryAlertOutcome struct {
	Key    string `json:"key"`
	Ref    string `json:"ref,omitempty"`
	Status string `json:"status"`
	Error  string `json:"error,omitempty"`
}

type deliveryAlertsResult struct {
	Complete        bool                   `json:"complete"`
	Scanned         int                    `json:"scanned"`
	Coverage        string                 `json:"coverage"`
	Notifier        string                 `json:"notifier"`
	StarvedSessions []core.StarvedSession  `json:"starved_sessions"`
	New             []deliveryAlertOutcome `json:"new"`
	Ongoing         int                    `json:"ongoing"`
	Cleared         []string               `json:"cleared"`
	Sent            int                    `json:"sent"`
	Failed          int                    `json:"failed"`
	Deferred        int                    `json:"deferred"`
}

type starvationAlert struct {
	Session         core.StarvedSession
	Key             string
	ExternalID      string
	Title           string
	DescriptionHTML string
}

type alertNotifier func(context.Context, starvationAlert) (string, error)

func deliveryAlertKey(s core.StarvedSession) string {
	return s.Recipient + "|" + s.ExecutionID + "|" + s.Condition
}

// The external ID names one episode: the condition plus the delivery that was
// oldest when it was first alerted. A retry after a lost state write reuses it.
func deliveryAlertExternalID(key, onset string) string {
	sum := sha256.Sum256([]byte(deliveryAlertSchema + "|" + key + "|" + onset))
	return "starvation-" + hex.EncodeToString(sum[:16])
}

func newStarvationAlert(s core.StarvedSession, name string, now time.Time) starvationAlert {
	if name == "" {
		name = s.Recipient
	}
	key := deliveryAlertKey(s)
	age := (time.Duration(s.OldestAgeSeconds) * time.Second).String()
	var title string
	if s.Condition == "hold_stalled" {
		title = fmt.Sprintf("Stalled delivery hold: %s holds %d for %s with no open tools (%s)", name, s.Count, age, s.RecipientState)
	} else {
		title = fmt.Sprintf("Inbox starvation: %s has %d pending for %s (%s)", name, s.Count, age, s.RecipientState)
	}
	last := "none recorded"
	if s.LastHandledAt != nil {
		last = s.LastHandledAt.UTC().Format(time.RFC3339)
	}
	review := `{"repo":"<repo>","consumer":"` + s.Recipient + `","limit":100,"attention":{}}`
	e := html.EscapeString
	var b strings.Builder
	b.WriteString("<p>" + e(s.Diagnosis) + "</p><ul>")
	for _, row := range [][2]string{
		{"Session", name + " (" + s.Recipient + ")"},
		{"Agent / execution", s.AgentID + " / " + s.ExecutionID},
		{"Condition", s.Condition + ", recipient " + s.RecipientState + ", host " + s.HostCondition},
		{"Deliveries", fmt.Sprintf("%d (requests %d, responses %d, notices %d)", s.Count, s.Requests, s.Responses, s.Notices)},
		{"Oldest", age + " (" + s.AgeBasis + "), delivery " + s.OldestDeliveryID},
		{"Threshold", (time.Duration(s.ThresholdSeconds) * time.Second).String()},
		{"Last handled delivery", last},
		{"Detected", now.UTC().Format(time.RFC3339)},
	} {
		b.WriteString("<li><strong>" + e(row[0]) + ":</strong> " + e(row[1]) + "</li>")
	}
	b.WriteString("</ul><p>Inspect: <code>cairn coordination-review &lt;&lt;&lt; '" + e(review) + "'</code></p>")
	b.WriteString("<p>cairn delivery-alerts takes no recovery action; it alerts once per new condition and stays quiet while the condition continues.</p>")
	return starvationAlert{Session: s, Key: key, ExternalID: deliveryAlertExternalID(key, s.OldestDeliveryID), Title: title, DescriptionHTML: b.String()}
}

// applyDeliveryAlerts is the dedupe policy. A nil notifier reports without
// recording. Failed notifications are not recorded, so the next run retries.
func applyDeliveryAlerts(ctx context.Context, current []core.StarvedSession, complete bool, previous deliveryAlertState, limits deliveryAlertLimits, notify alertNotifier, now time.Time) (deliveryAlertsResult, deliveryAlertState, error) {
	res := deliveryAlertsResult{Complete: complete, StarvedSessions: current, New: []deliveryAlertOutcome{}, Cleared: []string{}}
	next := deliveryAlertState{Schema: deliveryAlertSchema, Scope: previous.Scope, Alerts: map[string]deliveryAlertEntry{}}
	for k, v := range previous.Alerts {
		next.Alerts[k] = v
	}
	live := map[string]bool{}
	var failures []string
	for _, s := range current {
		key := deliveryAlertKey(s)
		live[key] = true
		if _, ok := previous.Alerts[key]; ok {
			res.Ongoing++
			continue
		}
		alert := newStarvationAlert(s, s.DisplayName, now)
		if notify == nil {
			res.New = append(res.New, deliveryAlertOutcome{Key: key, Status: "report_only"})
			continue
		}
		if res.Sent+res.Failed >= limits.MaxNew {
			res.Deferred++
			res.New = append(res.New, deliveryAlertOutcome{Key: key, Status: "deferred"})
			continue
		}
		ref, err := notify(ctx, alert)
		if err != nil {
			res.Failed++
			failures = append(failures, s.Recipient+": "+err.Error())
			res.New = append(res.New, deliveryAlertOutcome{Key: key, Status: "failed", Error: err.Error()})
			continue
		}
		res.Sent++
		next.Alerts[key] = deliveryAlertEntry{Recipient: s.Recipient, ExecutionID: s.ExecutionID, Condition: s.Condition,
			OnsetDeliveryID: s.OldestDeliveryID, ExternalID: alert.ExternalID, AlertedAt: now.UTC(), Ref: ref}
		res.New = append(res.New, deliveryAlertOutcome{Key: key, Ref: ref, Status: "sent"})
	}
	if complete {
		for key := range previous.Alerts {
			if !live[key] {
				delete(next.Alerts, key)
				res.Cleared = append(res.Cleared, key)
			}
		}
		sort.Strings(res.Cleared)
	}
	if len(next.Alerts) > 1000 {
		return res, previous, invalid("delivery alert state exceeds 1000 conditions")
	}
	if len(failures) > 0 {
		return res, next, fmt.Errorf("delivery alert notification failed for %d condition(s): %s", len(failures), strings.Join(failures, "; "))
	}
	return res, next, nil
}

// planeNotifier creates one Plane work item per alert through the installed
// plane CLI. Its external ID makes a repeated create return the existing item.
func planeNotifier(project string) alertNotifier {
	return func(ctx context.Context, a starvationAlert) (string, error) {
		data, _ := json.Marshal(map[string]string{"external_source": "cairn-delivery-alerts", "external_id": a.ExternalID})
		ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
		defer cancel()
		cmd := exec.CommandContext(ctx, "plane", "issue", "create", "-p", project, "--name", a.Title,
			"--priority", "high", "--description-html", a.DescriptionHTML, "--data", string(data))
		var stdout, stderr bytes.Buffer
		cmd.Stdout, cmd.Stderr = &stdout, &stderr
		runErr := cmd.Run()
		var envelope struct {
			OK   bool `json:"ok"`
			Data struct {
				ID         string `json:"id"`
				SequenceID int    `json:"sequence_id"`
			} `json:"data"`
			Error struct {
				Message    string `json:"message"`
				HTTPStatus int    `json:"http_status"`
				Details    struct {
					ID string `json:"id"`
				} `json:"details"`
			} `json:"error"`
		}
		if err := json.Unmarshal(stdout.Bytes(), &envelope); err != nil {
			return "", fmt.Errorf("plane: unreadable response (%v): %v %s", runErr, err, strings.TrimSpace(stderr.String()))
		}
		if envelope.OK && runErr == nil {
			if envelope.Data.SequenceID > 0 {
				return project + "-" + strconv.Itoa(envelope.Data.SequenceID), nil
			}
			return envelope.Data.ID, nil
		}
		if envelope.Error.HTTPStatus == 409 && envelope.Error.Details.ID != "" {
			return envelope.Error.Details.ID, nil // Already created for this episode.
		}
		return "", fmt.Errorf("plane: %s (http %d, %v)", envelope.Error.Message, envelope.Error.HTTPStatus, runErr)
	}
}

func deliveryAlerts(ctx context.Context, store *core.Store, input io.Reader) (any, error) {
	var req deliveryAlertsRequest
	if err := decode(input, &req); err != nil {
		return nil, invalid(err.Error())
	}
	if req.MaxNew == 0 {
		req.MaxNew = 5
	}
	if req.MaxPages == 0 {
		req.MaxPages = 10
	}
	if req.MaxNew < 1 || req.MaxNew > 20 || req.MaxPages < 1 || req.MaxPages > 50 {
		return nil, invalid("delivery-alerts max_new must be 1..20 and max_pages 1..50")
	}
	if req.PlaneProject != "" && req.StateFile == "" {
		return nil, invalid("delivery-alerts plane_project requires state_file for deduplication")
	}
	if req.StateFile != "" && !filepath.IsAbs(req.StateFile) {
		return nil, invalid("delivery-alerts state_file must be absolute")
	}
	rows, scanned, complete, err := store.ScanStarvationDeliveries(ctx, req.Repo, req.MaxPages)
	if err != nil {
		return nil, err
	}
	now := time.Now()
	current, err := core.DetectStarvation(rows, req.Starvation, now, func(consumers []string) (map[string]time.Time, error) {
		return store.LastHandledDeliveries(ctx, req.Repo, consumers)
	})
	if err != nil {
		return nil, err
	}
	if err = nameStarvedSessions(ctx, store, req.Repo, current); err != nil {
		return nil, err
	}
	var notify alertNotifier
	notifier := "none (report only)"
	if req.PlaneProject != "" {
		notify, notifier = planeNotifier(req.PlaneProject), "plane:"+req.PlaneProject
	}
	scope := attentionScope(struct{ Repo, Database, Home string }{req.Repo, os.Getenv("CAIRN_DATABASE_URL"), os.Getenv("CAIRN_HOME")})
	var res deliveryAlertsResult
	err = withDeliveryAlertState(req.StateFile, scope, notify != nil, func(previous deliveryAlertState) (deliveryAlertState, error) {
		var next deliveryAlertState
		var applyErr error
		res, next, applyErr = applyDeliveryAlerts(ctx, current, complete, previous, deliveryAlertLimits{MaxNew: req.MaxNew}, notify, now)
		return next, applyErr
	})
	res.Scanned, res.Complete, res.Notifier = scanned, complete, notifier
	res.Coverage = "Pending, leased and held deliveries for this repository, up to max_pages pages of each."
	if !complete {
		res.Coverage += " Partial scan: more deliveries exist; absence does not clear an alert."
	}
	return res, err
}

// nameStarvedSessions adds display names from the online agent directory.
// A name is presentation only; a missing name leaves the inbox as the label.
func nameStarvedSessions(ctx context.Context, store *core.Store, repo string, sessions []core.StarvedSession) error {
	if len(sessions) == 0 {
		return nil
	}
	names := map[string]string{}
	var after int64
	for page := 0; page < 10; page++ {
		dir, err := store.AgentDirectory(ctx, core.AgentDirectoryQuery{Repo: repo, After: after, Limit: 100}, core.Destination{Name: "local", AllowLocal: true})
		if err != nil {
			return err
		}
		for _, a := range dir.Agents {
			if a.ExecutionID != "" {
				names[a.Inbox+"|"+a.ExecutionID] = a.DisplayName
			}
		}
		if !dir.More {
			break
		}
		after = dir.NextAfter
	}
	for i := range sessions {
		sessions[i].DisplayName = names[sessions[i].Recipient+"|"+sessions[i].ExecutionID]
	}
	return nil
}

// withDeliveryAlertState holds an exclusive lock across read, notify and
// write so overlapping timer runs cannot alert twice. Without a state file, or
// in report-only mode, nothing is written.
func withDeliveryAlertState(path, scope string, write bool, run func(deliveryAlertState) (deliveryAlertState, error)) error {
	stateFile := LockedStateFile[deliveryAlertState]{
		Path:             path,
		TempPattern:      ".cairn-alerts-*",
		MaxBytes:         1024 * 1024,
		CreateDir:        true,
		AbsPathError:     "delivery-alerts state_file must be absolute",
		SizeLimitError:   "delivery alert state exceeds size limit",
		ParseErrorPrefix: "invalid delivery alert state",
		Default: func() deliveryAlertState {
			return deliveryAlertState{Schema: deliveryAlertSchema, Scope: scope, Alerts: map[string]deliveryAlertEntry{}}
		},
		Validate: func(s deliveryAlertState) error {
			if s.Schema != deliveryAlertSchema || s.Scope != scope || s.Alerts == nil {
				return invalid("delivery alert state scope or schema mismatch; use a separate state file")
			}
			return nil
		},
	}
	return stateFile.Update(func(state deliveryAlertState, _ func(deliveryAlertState) error) (deliveryAlertState, bool, error) {
		next, runErr := run(state)
		return next, write, runErr
	})
}
