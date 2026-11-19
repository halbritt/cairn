package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

const sendHelp = `cairn agent [connection flags] send (--campaign NAME --op-key KEY | --journal ABSOLUTE_PATH) --repo COLLECTION
  --stdin --kind EVENT_KIND [--shareable] [--source-kind NOTE_KIND]
  (--to PRINCIPAL | --resolution FILE | --topic TOPIC | --pool POOL)
  [--workspace PATH --harness NAME --model NAME --capability NAME]
  [--summary TEXT] [--blocker] [--action none|review|decide] [--supersedes TOPIC_KEY] [--expect-next-by RFC3339]
  [--expect-by RFC3339]

Save selected stdin text under coordination/<publication-request-UUID>, then
publish its exact returned version. Prefer --campaign NAME --op-key KEY so
idempotency is journaled on the host in the campaign ledger without private
journal files on disk. Legacy --journal ABSOLUTE_PATH remains available for
non-campaign sends. Retry with the same campaign/op-key (or journal), flags and
stdin after an uncertain result. Changed intent refuses.
`

var (
	sendEventName = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,128}$`)
	sendTopicKey  = regexp.MustCompile(`^[A-Za-z0-9_.:/-]{1,128}$`)
)

type sendIntent struct {
	BodySHA256       string                 `json:"body_sha256"`
	SummarySHA256    string                 `json:"summary_sha256,omitempty"`
	CredentialSHA256 string                 `json:"credential_sha256"`
	Repo             string                 `json:"repo"`
	Kind             string                 `json:"kind"`
	SourceKind       string                 `json:"source_kind"`
	Shareable        bool                   `json:"shareable"`
	Destination      core.EventDestination  `json:"destination"`
	Resolution       *core.AgentResolution  `json:"resolution,omitempty"`
	Pool             *core.PoolRequirements `json:"pool,omitempty"`
	Session          *core.AgentSessionRef  `json:"session,omitempty"`
	Action           string                 `json:"action,omitempty"`
	Supersedes       string                 `json:"supersedes,omitempty"`
	Blocker          bool                   `json:"blocker,omitempty"`
	ExpectNextBy     *time.Time             `json:"expect_next_by,omitempty"`
	ExpectBy         *time.Time             `json:"expect_by,omitempty"`
}

type sendJournal struct {
	Schema               string                `json:"schema"`
	IntentSHA256         string                `json:"intent_sha256"`
	SourceRequestID      string                `json:"source_request_id"`
	PublicationRequestID string                `json:"publication_request_id"`
	Source               core.RecordVersionRef `json:"source"`
	EventID              string                `json:"event_id,omitempty"`
	Routing              *core.EventRouting    `json:"routing,omitempty"`
}

type sendResult struct {
	Schema               string                 `json:"schema"`
	SourceSaved          *bool                  `json:"source_saved"`
	PublicationPending   bool                   `json:"publication_pending"`
	Source               *core.RecordVersionRef `json:"source,omitempty"`
	EventID              string                 `json:"event_id,omitempty"`
	Routing              *core.EventRouting     `json:"routing,omitempty"`
	SourceRequestID      string                 `json:"source_request_id"`
	PublicationRequestID string                 `json:"publication_request_id"`
}

func sendState(j sendJournal) sendResult {
	result := sendResult{Schema: "cairn.send-result/1", PublicationPending: j.EventID == "", SourceRequestID: j.SourceRequestID, PublicationRequestID: j.PublicationRequestID, EventID: j.EventID, Routing: j.Routing}
	if j.Source.RecordID != "" {
		confirmed := true
		result.SourceSaved = &confirmed
		result.Source = &j.Source
	}
	return result
}

func sendHash(intent sendIntent) (string, error) {
	encoded, err := json.Marshal(intent)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:]), nil
}

func validateSendJournal(journal sendJournal) error {
	if journal.Schema != "cairn.send-journal/1" || uuid.Validate(journal.SourceRequestID) != nil || uuid.Validate(journal.PublicationRequestID) != nil || journal.SourceRequestID == journal.PublicationRequestID || len(journal.IntentSHA256) != 64 {
		return invalid("invalid send journal identity")
	}
	if _, err := hex.DecodeString(journal.IntentSHA256); err != nil {
		return invalid("invalid send journal digest")
	}
	if journal.Source.RecordID != "" && (uuid.Validate(journal.Source.RecordID) != nil || journal.Source.Version < 1) {
		return invalid("invalid send journal source reference")
	}
	if journal.EventID != "" && (uuid.Validate(journal.EventID) != nil || journal.Source.RecordID == "") {
		return invalid("invalid send journal event reference")
	}
	return nil
}

func sendJournalStore(path string) LockedStateFile[sendJournal] {
	return LockedStateFile[sendJournal]{
		Path:               path,
		TempPattern:        ".cairn-send-*",
		MaxBytes:           8192,
		RequirePrivateDir:  true,
		RequirePrivateFile: true,
		StrictJSON:         true,
		NonBlocking:        true,
		AbsPathError:       "--journal requires an absolute path",
		SizeLimitError:     "send journal must be an owner-only regular file under 8 KiB",
		ParseErrorPrefix:   "invalid send journal",
		Validate:           validateSendJournal,
	}
}

func agentSend(ctx context.Context, client *localapi.Client, session *core.AgentSessionRef, args []string, input io.Reader) (any, error) {
	f := flags("send")
	journalPath := f.String("journal", "", "owner-only local retry journal (legacy; prefer --campaign and --op-key)")
	campaign := f.String("campaign", "", "hosted campaign ledger name (replaces local --journal)")
	opKey := f.String("op-key", "", "campaign operation idempotency key (with --campaign)")
	repo := f.String("repo", "", "collection repository")
	kind := f.String("kind", "", "event kind")
	sourceKind := f.String("source-kind", "note", "ordinary source record kind")
	stdin := f.Bool("stdin", false, "read selected source text from stdin")
	shareable := f.Bool("shareable", false, "allow hosted reads of the selected source")
	to := f.String("to", "", "exact recipient principal")
	resolutionFile := f.String("resolution", "", "exact unique agent resolution JSON")
	topic := f.String("topic", "", "explicit subscriber topic")
	pool := f.String("pool", "", "explicit fresh-work pool")
	workspace := f.String("workspace", "", "pool workspace")
	harness := f.String("harness", "", "pool harness")
	model := f.String("model", "", "pool model")
	eventSummary := f.String("summary", "", "concise single-line header summarizing what changed or is requested (1-280 UTF-8 bytes)")
	eventBlocker := f.Bool("blocker", false, "mark event as a blocker")
	noticeAction := f.String("action", "", "notice intent action (none|review|decide)")
	noticeSupersedes := f.String("supersedes", "", "sender-scoped notice topic key superseded by this notice")
	noticeExpectNextBy := f.String("expect-next-by", "", "expected next update instant for notice (RFC3339)")
	requestExpectBy := f.String("expect-by", "", "advisory completion or response deadline for request (RFC3339)")
	var capabilities []string
	f.Func("capability", "pool capability; repeat", func(value string) error { capabilities = append(capabilities, value); return nil })
	if err := f.Parse(args); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(sendHelp), nil
		}
		return nil, invalid(err.Error())
	}
	useLedger := *campaign != "" || *opKey != ""
	if useLedger && (*campaign == "" || *opKey == "" || *journalPath != "") {
		return nil, invalid("choose either (--campaign and --op-key) or --journal, not both")
	}
	if f.NArg() != 0 || !*stdin || (!useLedger && *journalPath == "") || strings.TrimSpace(*repo) == "" || *repo == "*" || len(*repo) > 256 || !sendEventName.MatchString(*kind) {
		return nil, invalid("send requires (--campaign and --op-key | --journal), --repo, --stdin, --kind and no positional arguments")
	}
	provided := map[string]bool{}
	f.Visit(func(fl *flag.Flag) { provided[fl.Name] = true })
	if provided["summary"] {
		if strings.TrimSpace(*eventSummary) == "" {
			return nil, invalid("--summary cannot be explicitly empty")
		}
		if err := core.ValidateEventSummary(*eventSummary); err != nil {
			return nil, err
		}
	}
	if *eventBlocker && *noticeAction == "none" {
		return nil, invalid("--blocker cannot combine with --action none")
	}
	var parsedExpectNextBy *time.Time
	if provided["action"] || provided["supersedes"] || provided["expect-next-by"] {
		if *kind != "notice" {
			return nil, invalid("notice intent flags (--action, --supersedes, --expect-next-by) require --kind notice")
		}
		if provided["action"] && *noticeAction != "none" && *noticeAction != "review" && *noticeAction != "decide" {
			return nil, invalid("invalid --action: must be none, review or decide")
		}
		if provided["supersedes"] && !sendTopicKey.MatchString(*noticeSupersedes) {
			return nil, invalid("invalid --supersedes: must be a 1-128 byte topic key")
		}
		if provided["expect-next-by"] {
			t, err := parseEventTimestamp(*noticeExpectNextBy)
			if err != nil || t.IsZero() || t.Year() < 1 || t.Year() > 9999 {
				return nil, invalid("invalid --expect-next-by: must be an RFC3339 timestamp")
			}
			parsedExpectNextBy = &t
		}
	}
	var parsedExpectBy *time.Time
	if provided["expect-by"] {
		if *kind != "request" {
			return nil, invalid("--expect-by requires --kind request")
		}
		t, err := parseEventTimestamp(*requestExpectBy)
		if err != nil || t.IsZero() || t.Year() < 1 || t.Year() > 9999 {
			return nil, invalid("invalid --expect-by: must be an RFC3339 timestamp")
		}
		parsedExpectBy = &t
	}
	switch *sourceKind {
	case "note", "observation", "claim", "lesson", "procedure", "decision", "preference":
	default:
		return nil, invalid("unsupported source kind")
	}
	choices := 0
	for _, choice := range []string{*to, *resolutionFile, *topic, *pool} {
		if choice != "" {
			choices++
		}
	}
	if choices != 1 {
		return nil, invalid("choose exactly one of --to, --resolution, --topic and --pool")
	}
	if *pool == "" && (*workspace != "" || *harness != "" || *model != "" || len(capabilities) != 0) {
		return nil, invalid("pool selectors require --pool")
	}
	if *pool != "" && *kind != "request" {
		return nil, invalid("pool destinations require request kind")
	}
	if *topic != "" && !sendEventName.MatchString(*topic) || *pool != "" && !sendEventName.MatchString(*pool) {
		return nil, invalid("topic and pool names must be bounded exact labels")
	}
	if *pool != "" {
		if !filepath.IsAbs(*workspace) || filepath.Clean(*workspace) != *workspace || len(*workspace) > 4096 || strings.ContainsRune(*workspace, 0) || *harness != "" && !sendEventName.MatchString(*harness) || len(*model) > 256 || strings.ContainsRune(*model, 0) || len(capabilities) > 16 {
			return nil, invalid("pool requires canonical workspace and bounded selectors")
		}
		seen := map[string]bool{}
		for _, capability := range capabilities {
			if !sendEventName.MatchString(capability) || seen[capability] {
				return nil, invalid("pool capabilities require distinct bounded labels")
			}
			seen[capability] = true
		}
	}
	body, err := io.ReadAll(io.LimitReader(input, 65537))
	if err != nil {
		return nil, err
	}
	if len(body) == 0 || len(body) > 65536 || strings.TrimSpace(string(body)) == "" || !utf8.Valid(body) {
		return nil, invalid("send source requires 1-65536 bytes of nonblank UTF-8 text")
	}
	if len(body) > core.MaxEventSummaryBytes && *eventSummary == "" {
		return nil, invalid("send requires --summary (1-280 single-line UTF-8 bytes) when body exceeds 280 bytes; state what changed in --summary and keep uncompressed details in body")
	}
	dest := core.EventDestination{Type: "agent", Name: *to}
	var resolution *core.AgentResolution
	if *resolutionFile != "" {
		file, err := os.Open(*resolutionFile)
		if err != nil {
			return nil, err
		}
		err = decodeBounded(file, &resolution, 4096)
		closeErr := file.Close()
		if err != nil {
			return nil, invalid("invalid resolution JSON: " + err.Error())
		}
		if closeErr != nil {
			return nil, closeErr
		}
		if resolution == nil || resolution.AgentID == "" || resolution.ExecutionID == "" || resolution.Repo != *repo {
			return nil, invalid("resolution must be a unique match in the selected collection")
		}
		dest.Name = "agent/" + resolution.AgentID
	}
	var needs *core.PoolRequirements
	if *topic != "" {
		dest = core.EventDestination{Type: "topic", Name: *topic}
	}
	if *pool != "" {
		dest = core.EventDestination{Type: "pool", Name: *pool}
		needs = &core.PoolRequirements{Workspace: *workspace, Harness: *harness, Model: *model, Capabilities: capabilities}
	}
	if strings.TrimSpace(dest.Name) == "" || len(dest.Name) > 256 {
		return nil, invalid("explicit destination required")
	}
	if useLedger {
		var sent core.CampaignSendResult
		err := client.Call(ctx, "ledger-send", core.CampaignSendRequest{
			Repo:       *repo,
			Campaign:   *campaign,
			OpKey:      *opKey,
			Body:       string(body),
			SourceKind: *sourceKind,
			Shareable:  *shareable,
			Publication: core.PublishEventRequest{
				Summary: *eventSummary, Action: *noticeAction, Supersedes: *noticeSupersedes,
				Blocker: *eventBlocker, ExpectNextBy: parsedExpectNextBy, ExpectBy: parsedExpectBy,
				Repo: *repo, Kind: *kind, Destination: dest, Resolution: resolution, Pool: needs,
			},
		}, &sent)
		if err != nil {
			return nil, err
		}
		confirmed := true
		src := sent.Source
		return sendResult{
			Schema:               "cairn.send-result/1",
			SourceSaved:          &confirmed,
			PublicationPending:   false,
			Source:               &src,
			EventID:              sent.Event.EventID,
			Routing:              sent.Event.Routing,
			SourceRequestID:      sent.SourceRequestID,
			PublicationRequestID: sent.PublicationRequestID,
		}, nil
	}
	bodyHash := sha256.Sum256(body)
	var summaryHashHex string
	if *eventSummary != "" {
		sumHash := sha256.Sum256([]byte(*eventSummary))
		summaryHashHex = hex.EncodeToString(sumHash[:])
	}
	intent := sendIntent{BodySHA256: hex.EncodeToString(bodyHash[:]), SummarySHA256: summaryHashHex, CredentialSHA256: client.CredentialFingerprint(), Repo: *repo, Kind: *kind, SourceKind: *sourceKind, Shareable: *shareable, Destination: dest, Resolution: resolution, Pool: needs, Session: session, Action: *noticeAction, Supersedes: *noticeSupersedes, Blocker: *eventBlocker, ExpectNextBy: parsedExpectNextBy, ExpectBy: parsedExpectBy}
	intentHash, err := sendHash(intent)
	if err != nil {
		return nil, err
	}
	var out any
	err = sendJournalStore(*journalPath).Update(func(journal sendJournal, save func(sendJournal) error) (sendJournal, bool, error) {
		if journal.Schema == "" {
			journal = sendJournal{Schema: "cairn.send-journal/1", IntentSHA256: intentHash, SourceRequestID: uuid.NewString(), PublicationRequestID: uuid.NewString()}
			if err := save(journal); err != nil {
				return journal, false, err
			}
		} else if journal.IntentSHA256 != intentHash {
			return journal, false, &core.Error{Code: "IDEMPOTENCY_CONFLICT", Message: "send journal already owns a different selected source or destination"}
		}
		if journal.Source.RecordID == "" {
			sensitivity := "local"
			if *shareable {
				sensitivity = "shareable"
			}
			create := core.CreateRequest{RequestID: journal.SourceRequestID, Draft: core.Draft{Body: string(body), Kind: *sourceKind, ClaimType: "self", Sensitivity: sensitivity, Scope: core.Scope{Repo: *repo, TaskID: "coordination/" + journal.PublicationRequestID, RunID: "*"}}}
			var record core.Record
			if err := client.Call(ctx, "create", create, &record); err != nil {
				out = sendState(journal)
				return journal, false, err
			}
			if uuid.Validate(record.RecordID) != nil || record.Version < 1 || record.Scope != create.Draft.Scope || record.Body != create.Draft.Body || record.Kind != create.Draft.Kind || record.Sensitivity != create.Draft.Sensitivity {
				out = sendState(journal)
				return journal, false, &core.Error{Code: "SCHEMA_MISMATCH", Message: "create returned a source different from the selected assignment"}
			}
			journal.Source = core.RecordVersionRef{RecordID: record.RecordID, Version: record.Version}
			if err := save(journal); err != nil {
				out = sendState(journal)
				return journal, false, err
			}
		}
		if journal.EventID == "" {
			publish := core.PublishEventRequest{Summary: *eventSummary, Action: *noticeAction, Supersedes: *noticeSupersedes, Blocker: *eventBlocker, ExpectNextBy: parsedExpectNextBy, ExpectBy: parsedExpectBy, RequestID: journal.PublicationRequestID, Repo: *repo, Kind: *kind, Ref: journal.Source, Destination: dest, Resolution: resolution, Pool: needs}
			var event core.AgentEvent
			if err := client.Call(ctx, "event-publish", publish, &event); err != nil {
				out = sendState(journal)
				return journal, false, err
			}
			destValid := event.Destination == dest || (event.Routing != nil && event.Routing.Rerouted && event.Routing.OriginalDestination != nil && *event.Routing.OriginalDestination == dest && event.Destination == event.Routing.EffectiveDestination)
			if uuid.Validate(event.EventID) != nil || event.Ref != journal.Source || !destValid || event.Repo != *repo || event.Kind != *kind || event.From == "" {
				out = sendState(journal)
				return journal, false, &core.Error{Code: "SCHEMA_MISMATCH", Message: "publish returned a different source or destination"}
			}
			journal.EventID = event.EventID
			journal.Routing = event.Routing
			if err := save(journal); err != nil {
				out = sendState(journal)
				return journal, false, err
			}
		}
		out = sendState(journal)
		return journal, false, nil
	})
	return out, err
}
