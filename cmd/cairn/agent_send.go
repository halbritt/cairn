package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

const sendHelp = `cairn agent [connection flags] send --journal ABSOLUTE_PATH --repo COLLECTION
  --stdin --kind EVENT_KIND [--shareable] [--source-kind NOTE_KIND]
  (--to PRINCIPAL | --resolution FILE | --topic TOPIC | --pool POOL)
  [--workspace PATH --harness NAME --model NAME --capability NAME]

Save selected stdin text under coordination/<publication-request-UUID>, then
publish its exact returned version. The helper allocates separate source and
publication retry UUIDs once in an owner-only local journal. Retry with the
same journal, flags and stdin after an uncertain result. Changed intent refuses.
The journal records identities and a digest, never source text or credentials.
Use ordinary remember for reusable repository guidance. Resolution FILE must
contain the exact resolution object from a unique agent-resolve result. The
helper never chooses a recipient or creates a session. Publication failure
returns source_saved and publication_pending without marking delivery complete.
`

var sendEventName = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,128}$`)

type sendIntent struct {
	BodySHA256       string                 `json:"body_sha256"`
	CredentialSHA256 string                 `json:"credential_sha256"`
	Repo             string                 `json:"repo"`
	Kind             string                 `json:"kind"`
	SourceKind       string                 `json:"source_kind"`
	Shareable        bool                   `json:"shareable"`
	Destination      core.EventDestination  `json:"destination"`
	Resolution       *core.AgentResolution  `json:"resolution,omitempty"`
	Pool             *core.PoolRequirements `json:"pool,omitempty"`
	Session          *core.AgentSessionRef  `json:"session,omitempty"`
}

type sendJournal struct {
	Schema               string                `json:"schema"`
	IntentSHA256         string                `json:"intent_sha256"`
	SourceRequestID      string                `json:"source_request_id"`
	PublicationRequestID string                `json:"publication_request_id"`
	Source               core.RecordVersionRef `json:"source"`
	EventID              string                `json:"event_id,omitempty"`
}

type sendResult struct {
	Schema               string                 `json:"schema"`
	SourceSaved          *bool                  `json:"source_saved"`
	PublicationPending   bool                   `json:"publication_pending"`
	Source               *core.RecordVersionRef `json:"source,omitempty"`
	EventID              string                 `json:"event_id,omitempty"`
	SourceRequestID      string                 `json:"source_request_id"`
	PublicationRequestID string                 `json:"publication_request_id"`
}

func sendState(j sendJournal) sendResult {
	result := sendResult{Schema: "cairn.send-result/1", PublicationPending: j.EventID == "", SourceRequestID: j.SourceRequestID, PublicationRequestID: j.PublicationRequestID, EventID: j.EventID}
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

func readSendJournal(path string) (sendJournal, error) {
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if errors.Is(err, os.ErrNotExist) {
		return sendJournal{}, nil
	}
	if err != nil {
		return sendJournal{}, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return sendJournal{}, err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 8192 {
		return sendJournal{}, invalid("send journal must be an owner-only regular file under 8 KiB")
	}
	var journal sendJournal
	decoder := json.NewDecoder(io.LimitReader(file, 8193))
	if err := decoder.Decode(&journal); err != nil {
		return sendJournal{}, invalid("invalid send journal: " + err.Error())
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return sendJournal{}, invalid("send journal contains trailing data")
	}
	if journal.Schema != "cairn.send-journal/1" || uuid.Validate(journal.SourceRequestID) != nil || uuid.Validate(journal.PublicationRequestID) != nil || journal.SourceRequestID == journal.PublicationRequestID || len(journal.IntentSHA256) != 64 {
		return sendJournal{}, invalid("invalid send journal identity")
	}
	if _, err := hex.DecodeString(journal.IntentSHA256); err != nil {
		return sendJournal{}, invalid("invalid send journal digest")
	}
	if journal.Source.RecordID != "" && (uuid.Validate(journal.Source.RecordID) != nil || journal.Source.Version < 1) {
		return sendJournal{}, invalid("invalid send journal source reference")
	}
	if journal.EventID != "" && (uuid.Validate(journal.EventID) != nil || journal.Source.RecordID == "") {
		return sendJournal{}, invalid("invalid send journal event reference")
	}
	return journal, nil
}

func writeSendJournal(path string, journal sendJournal) error {
	directory := filepath.Dir(path)
	file, err := os.CreateTemp(directory, ".cairn-send-*")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if err := json.NewEncoder(file).Encode(journal); err != nil {
		file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := os.Rename(file.Name(), path); err != nil {
		return err
	}
	parent, err := os.Open(directory)
	if err != nil {
		return err
	}
	defer parent.Close()
	return parent.Sync()
}

func sendJournalLock(path string) (*os.File, error) {
	if !filepath.IsAbs(path) {
		return nil, invalid("--journal requires an absolute path")
	}
	directory := filepath.Dir(path)
	info, err := os.Stat(directory)
	if err != nil {
		return nil, err
	}
	if !info.IsDir() || info.Mode().Perm()&0022 != 0 {
		return nil, invalid("send journal directory must not be writable by others")
	}
	lock, err := os.OpenFile(path+".lock", os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0600)
	if err != nil {
		return nil, err
	}
	lockInfo, err := lock.Stat()
	if err != nil || !lockInfo.Mode().IsRegular() || lockInfo.Mode().Perm()&0077 != 0 {
		lock.Close()
		return nil, invalid("send journal lock must be owner-only regular file")
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lock.Close()
		return nil, invalid("send journal is already in use or cannot be locked")
	}
	return lock, nil
}

func agentSend(ctx context.Context, client *localapi.Client, session *core.AgentSessionRef, args []string, input io.Reader) (any, error) {
	f := flags("send")
	journalPath := f.String("journal", "", "owner-only local retry journal")
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
	var capabilities []string
	f.Func("capability", "pool capability; repeat", func(value string) error { capabilities = append(capabilities, value); return nil })
	if err := f.Parse(args); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(sendHelp), nil
		}
		return nil, invalid(err.Error())
	}
	if f.NArg() != 0 || !*stdin || *journalPath == "" || strings.TrimSpace(*repo) == "" || *repo == "*" || len(*repo) > 256 || !sendEventName.MatchString(*kind) {
		return nil, invalid("send requires --journal, --repo, --stdin, --kind and no positional arguments")
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
	bodyHash := sha256.Sum256(body)
	intent := sendIntent{BodySHA256: hex.EncodeToString(bodyHash[:]), CredentialSHA256: client.CredentialFingerprint(), Repo: *repo, Kind: *kind, SourceKind: *sourceKind, Shareable: *shareable, Destination: dest, Resolution: resolution, Pool: needs, Session: session}
	intentHash, err := sendHash(intent)
	if err != nil {
		return nil, err
	}
	lock, err := sendJournalLock(*journalPath)
	if err != nil {
		return nil, err
	}
	defer lock.Close()
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	journal, err := readSendJournal(*journalPath)
	if err != nil {
		return nil, err
	}
	if journal.Schema == "" {
		journal = sendJournal{Schema: "cairn.send-journal/1", IntentSHA256: intentHash, SourceRequestID: uuid.NewString(), PublicationRequestID: uuid.NewString()}
		if err := writeSendJournal(*journalPath, journal); err != nil {
			return nil, err
		}
	} else if journal.IntentSHA256 != intentHash {
		return nil, &core.Error{Code: "IDEMPOTENCY_CONFLICT", Message: "send journal already owns a different selected source or destination"}
	}
	if journal.Source.RecordID == "" {
		sensitivity := "local"
		if *shareable {
			sensitivity = "shareable"
		}
		create := core.CreateRequest{RequestID: journal.SourceRequestID, Draft: core.Draft{Body: string(body), Kind: *sourceKind, ClaimType: "self", Sensitivity: sensitivity, Scope: core.Scope{Repo: *repo, TaskID: "coordination/" + journal.PublicationRequestID, RunID: "*"}}}
		var record core.Record
		if err := client.Call(ctx, "create", create, &record); err != nil {
			return sendState(journal), err
		}
		if uuid.Validate(record.RecordID) != nil || record.Version < 1 || record.Scope != create.Draft.Scope || record.Body != create.Draft.Body || record.Kind != create.Draft.Kind || record.Sensitivity != create.Draft.Sensitivity {
			return sendState(journal), &core.Error{Code: "SCHEMA_MISMATCH", Message: "create returned a source different from the selected assignment"}
		}
		journal.Source = core.RecordVersionRef{RecordID: record.RecordID, Version: record.Version}
		if err := writeSendJournal(*journalPath, journal); err != nil {
			return sendState(journal), err
		}
	}
	if journal.EventID == "" {
		publish := core.PublishEventRequest{RequestID: journal.PublicationRequestID, Repo: *repo, Kind: *kind, Ref: journal.Source, Destination: dest, Resolution: resolution, Pool: needs}
		var event core.AgentEvent
		if err := client.Call(ctx, "event-publish", publish, &event); err != nil {
			return sendState(journal), err
		}
		if uuid.Validate(event.EventID) != nil || event.Ref != journal.Source || event.Destination != dest || event.Repo != *repo || event.Kind != *kind || event.From == "" {
			return sendState(journal), &core.Error{Code: "SCHEMA_MISMATCH", Message: "publish returned a different source or destination"}
		}
		journal.EventID = event.EventID
		if err := writeSendJournal(*journalPath, journal); err != nil {
			return sendState(journal), err
		}
	}
	return sendState(journal), nil
}
