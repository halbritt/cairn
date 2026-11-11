package memoryclient

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/jsontext"
)

var (
	ErrCaptureSelection = errors.New("capture selection refused")
	ErrCaptureDomain    = errors.New("capture input is outside the supported JSON and Unicode domain")
	ErrCaptureWrite     = errors.New("capture write failed")
)

var errCaptureRecord = errors.New("Cairn did not confirm the selected record")

// CaptureRecord is supplied record data, never an authenticated identity.
type CaptureRecord struct {
	RecordID string
	Version  int
	Kind     string
	Body     string
}

type CaptureInput struct {
	Selection          json.RawMessage
	ProjectBase        string
	ExplicitWorkstream string
	Previous           *CaptureRecord
	Candidates         []CaptureRecord
	Handoffs           []CaptureRecord
}

// CaptureResult retains confirmed progress when a later write fails. Workstream
// is absent until the final note save, including a no-op, succeeds.
type CaptureResult struct {
	Saved      []string
	Workstream *string
}

type captureError struct {
	class   error
	message string
	cause   error
}

func (e *captureError) Error() string        { return e.message }
func (e *captureError) Is(target error) bool { return target == e.class }
func (e *captureError) Unwrap() error        { return e.cause }

func captureRefusal(message string) error {
	return &captureError{class: ErrCaptureSelection, message: message}
}

type captureSelection struct {
	checkpoint *string
	state      string
	topic      json.RawMessage
	notes      []json.RawMessage
}

type captureWrite struct {
	body     string
	kind     string
	previous *CaptureRecord
	noop     bool
}

// CommitCaptureSelection validates the complete selection before mutating any
// record. Validation may perform Checkpoint lookups. Confirmed writes remain in
// the result if a later write fails; the caller owns any frontend state commit.
func (c *Client) CommitCaptureSelection(ctx context.Context, input CaptureInput) (CaptureResult, error) {
	writes, err := c.capturePlan(ctx, input)
	if err != nil {
		return CaptureResult{}, err
	}
	result := CaptureResult{Saved: []string{}}
	for _, write := range writes {
		identity, err := c.commitCaptureWrite(ctx, write)
		if err != nil {
			return result, err
		}
		if write.kind == "note" {
			title, _, _ := strings.Cut(write.body, "\n")
			result.Workstream = &title
		}
		if identity != "" {
			result.Saved = append(result.Saved, identity)
		}
	}
	return result, nil
}

func (c *Client) capturePlan(ctx context.Context, input CaptureInput) ([]captureWrite, error) {
	if !captureInputUnicode(input) || !utf8.ValidString(c.scope.Repo) {
		return nil, ErrCaptureDomain
	}
	selection, err := parseCaptureSelection(input.Selection)
	if err != nil {
		return nil, err
	}
	known := make(map[string]CaptureRecord, len(input.Candidates))
	for _, record := range input.Candidates {
		known[record.RecordID] = record
	}
	var writes []captureWrite
	used := make(map[string]bool)
	for _, raw := range selection.notes {
		write, key, err := c.captureDurable(ctx, input.ProjectBase, raw, known)
		if err != nil {
			return nil, err
		}
		if used[key] {
			return nil, captureRefusal("memory selection repeated a topic")
		}
		used[key] = true
		writes = append(writes, write)
	}
	if selection.checkpoint != nil {
		write, err := c.captureWorkstream(ctx, input, selection)
		if err != nil {
			return nil, err
		}
		writes = append(writes, write)
	} else if !captureNull(selection.topic) {
		return nil, captureRefusal("workstream requires a checkpoint")
	}
	return writes, nil
}

func captureInputUnicode(input CaptureInput) bool {
	if jsontext.CheckUnicode(input.Selection) != nil || !utf8.ValidString(input.ProjectBase) || !utf8.ValidString(input.ExplicitWorkstream) {
		return false
	}
	valid := func(record CaptureRecord) bool {
		return utf8.ValidString(record.RecordID) && utf8.ValidString(record.Kind) && utf8.ValidString(record.Body)
	}
	if input.Previous != nil && !valid(*input.Previous) {
		return false
	}
	for _, records := range [][]CaptureRecord{input.Candidates, input.Handoffs} {
		for _, record := range records {
			if !valid(record) {
				return false
			}
		}
	}
	return true
}

func parseCaptureSelection(raw json.RawMessage) (captureSelection, error) {
	var selection captureSelection
	if len(bytes.Trim(raw, " \t\r\n")) == 0 {
		return selection, captureRefusal("memory selection did not return the required schema")
	}
	if !json.Valid(raw) {
		return selection, ErrCaptureDomain
	}
	members, ok := captureObject(raw)
	if !ok || len(members) < 3 || len(members) > 4 {
		return selection, captureRefusal("memory selection did not return the required schema")
	}
	for _, key := range []string{"checkpoint", "workstream", "memories"} {
		if _, exists := members[key]; !exists {
			return selection, captureRefusal("memory selection did not return the required schema")
		}
	}
	for key := range members {
		if key != "checkpoint" && key != "workstream" && key != "memories" && key != "checkpoint_state" {
			return selection, captureRefusal("memory selection did not return the required schema")
		}
	}
	checkpointNull := captureNull(members["checkpoint"])
	stateRaw, explicit := members["checkpoint_state"]
	if !explicit && !checkpointNull {
		selection.state = "open"
	} else if explicit && !captureNull(stateRaw) {
		var valid bool
		selection.state, valid = captureText(stateRaw)
		if !valid || (selection.state != "open" && selection.state != "complete") {
			return selection, captureRefusal("memory selection returned an invalid checkpoint state")
		}
	}
	if checkpointNull != (selection.state == "") {
		return selection, captureRefusal("memory selection returned an invalid checkpoint state")
	}
	if !checkpointNull {
		body, valid := captureText(members["checkpoint"])
		if !valid || !captureValidBody(body) || len(body) > 4500 {
			return selection, captureRefusal("memory selection returned an invalid checkpoint")
		}
		selection.checkpoint = &body
	}
	if !bytes.HasPrefix(bytes.TrimSpace(members["memories"]), []byte("[")) ||
		json.Unmarshal(members["memories"], &selection.notes) != nil || len(selection.notes) > 3 {
		return selection, captureRefusal("memory selection returned invalid reusable notes")
	}
	selection.topic = members["workstream"]
	return selection, nil
}

func (c *Client) captureDurable(ctx context.Context, project string, raw json.RawMessage, known map[string]CaptureRecord) (captureWrite, string, error) {
	note, valid := captureObject(raw)
	if !valid || len(note) != 4 || note["record_id"] == nil || note["kind"] == nil || note["title"] == nil || note["body"] == nil {
		return captureWrite{}, "", captureRefusal("memory selection returned an invalid reusable note")
	}
	kind, kindOK := captureText(note["kind"])
	title, titleOK := captureText(note["title"])
	body, bodyOK := captureText(note["body"])
	if !kindOK || (kind != "decision" && kind != "preference" && kind != "lesson" && kind != "procedure") ||
		!bodyOK || !captureValidBody(body) || !titleOK {
		return captureWrite{}, "", captureRefusal("memory selection returned invalid note fields")
	}
	write := captureWrite{body: body, kind: kind}
	if !captureNull(note["record_id"]) {
		identity, valid := captureText(note["record_id"])
		old, supplied := known[identity]
		if !valid || !supplied || old.Kind != kind {
			return write, "", captureRefusal("memory selection referenced an unsupplied record")
		}
		write.previous, write.noop = &old, old.Body == body
		return write, identity, nil
	}
	if !captureValidTitle(title) {
		return write, "", captureRefusal("memory selection returned an invalid topic title")
	}
	title = captureClip(project) + ": " + captureStrip(title)
	write.body = title + "\n\n" + captureStrip(body)
	if !captureValidBody(write.body) {
		return write, "", captureRefusal("memory selection returned invalid note fields")
	}
	found, oldBody, err := c.captureLookupBody(ctx, title)
	if err != nil {
		return write, "", err
	}
	if found && oldBody != write.body {
		return write, "", captureRefusal("matching topic needs reconciliation; use an explicit memory edit")
	}
	write.noop = found
	return write, title, nil
}

func (c *Client) captureWorkstream(ctx context.Context, input CaptureInput, selection captureSelection) (captureWrite, error) {
	topic, valid := captureText(selection.topic)
	if input.ExplicitWorkstream != "" && (!valid || topic != input.ExplicitWorkstream) {
		return captureWrite{}, captureRefusal("selection changed the explicitly chosen workstream")
	}
	if !valid || !captureValidTitle(topic) {
		return captureWrite{}, captureRefusal("memory selection returned an invalid workstream topic")
	}
	title := "Handoff: " + captureClip(strings.ReplaceAll(input.ProjectBase, "\"", "")) + " / " + captureStrip(topic)
	write := captureWrite{kind: "note", body: title + "\n\n"}
	if selection.state == "complete" {
		write.body += "Status: complete\n"
	}
	write.body += captureStrip(*selection.checkpoint)
	for _, record := range input.Handoffs {
		if strings.HasPrefix(record.Body, title+"\n") {
			old := record
			write.previous = &old
			break
		}
	}
	if write.previous == nil && input.Previous != nil && strings.HasPrefix(input.Previous.Body, title+"\n") {
		old := *input.Previous
		write.previous = &old
	}
	if selection.state == "complete" && write.previous == nil {
		return write, captureRefusal("completion requires a supplied existing handoff")
	}
	if write.previous != nil {
		write.noop = write.previous.Body == write.body
		return write, nil
	}
	found, oldBody, err := c.captureLookupBody(ctx, title)
	if err != nil {
		return write, err
	}
	if found && oldBody != write.body {
		return write, captureRefusal("matching workstream needs reconciliation; use an explicit handoff edit")
	}
	// A lookup can prove an exact no-op, but cannot supply a mutation target.
	write.noop = found
	return write, nil
}

func (c *Client) captureLookupBody(ctx context.Context, title string) (bool, string, error) {
	raw, err := c.Checkpoint(ctx, title)
	if err != nil || raw == nil {
		return false, "", err
	}
	members, valid := captureObject(raw)
	body, bodyOK := captureText(members["body"])
	if !valid || !bodyOK {
		return false, "", ErrCheckpointRecord
	}
	return true, body, nil
}

func (c *Client) commitCaptureWrite(ctx context.Context, write captureWrite) (string, error) {
	if write.noop {
		return "", nil
	}
	identity := captureRequestID(c.scope.Repo, write)
	operation := "create"
	var request any = core.CreateRequest{RequestID: identity, Draft: core.Draft{
		Kind: write.kind, Body: write.body, Scope: core.Scope{Repo: c.scope.Repo, TaskID: "*", RunID: "*"},
		Sensitivity: "shareable", ClaimType: "self"}}
	if write.previous != nil {
		operation = "revise"
		request = core.ReviseRequest{RequestID: identity, RecordID: write.previous.RecordID,
			ExpectedVersion: write.previous.Version, Repo: c.scope.Repo, Body: write.body}
	}
	callCtx, cancel := context.WithTimeout(ctx, callTimeout)
	defer cancel()
	var raw json.RawMessage
	if err := c.api.Call(callCtx, operation, request, &raw); err != nil {
		return "", &captureError{class: ErrCaptureWrite, message: ErrCaptureWrite.Error(), cause: err}
	}
	reply, object := captureObject(raw)
	if !object {
		return "", &captureError{class: ErrCaptureWrite, message: ErrCaptureWrite.Error(), cause: ErrNotConfirmed}
	}
	recordID, valid := captureText(reply["record_id"])
	if !valid || recordID == "" {
		return "", &captureError{class: ErrCaptureWrite, message: errCaptureRecord.Error(), cause: errCaptureRecord}
	}
	return recordID, nil
}

func captureObject(raw json.RawMessage) (map[string]json.RawMessage, bool) {
	var members map[string]json.RawMessage
	err := json.Unmarshal(raw, &members)
	return members, err == nil && members != nil
}

func captureText(raw json.RawMessage) (string, bool) {
	if !bytes.HasPrefix(bytes.TrimSpace(raw), []byte("\"")) {
		return "", false
	}
	var text string
	err := json.Unmarshal(raw, &text)
	return text, err == nil
}

func captureNull(raw json.RawMessage) bool { return bytes.Equal(bytes.TrimSpace(raw), []byte("null")) }

func captureStrip(text string) string {
	return strings.TrimFunc(text, func(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) })
}

func captureValidBody(body string) bool { return captureStrip(body) != "" && len(body) <= 6000 }

func captureValidTitle(title string) bool {
	return captureStrip(title) != "" && len(title) <= 90 && !strings.ContainsAny(title, "\n\r\"\x00")
}

func captureClip(text string) string {
	text = text[:min(40, len(text))]
	for !utf8.ValidString(text) {
		text = text[:len(text)-1]
	}
	return text
}

func captureRequestID(repo string, write captureWrite) string {
	digest := sha256.Sum256([]byte(write.body))
	var name strings.Builder
	name.WriteByte('[')
	captureNameString(&name, repo)
	name.WriteByte(',')
	captureNameString(&name, write.kind)
	name.WriteByte(',')
	version := 0
	if write.previous == nil {
		name.WriteString("null")
	} else {
		captureNameString(&name, write.previous.RecordID)
		version = write.previous.Version
	}
	name.WriteByte(',')
	name.WriteString(strconv.Itoa(version))
	name.WriteByte(',')
	captureNameString(&name, hex.EncodeToString(digest[:]))
	name.WriteByte(']')
	return uuid.NewSHA1(uuid.NameSpaceURL, []byte(name.String())).String()
}

// Python's ensure_ascii=False leaves HTML and line separators literal, unlike
// encoding/json even when its HTML escaping is disabled.
func captureNameString(name *strings.Builder, text string) {
	const hex = "0123456789abcdef"
	name.WriteByte('"')
	for _, r := range text {
		switch r {
		case '"', '\\':
			name.WriteByte('\\')
			name.WriteRune(r)
		case '\b':
			name.WriteString(`\b`)
		case '\f':
			name.WriteString(`\f`)
		case '\n':
			name.WriteString(`\n`)
		case '\r':
			name.WriteString(`\r`)
		case '\t':
			name.WriteString(`\t`)
		default:
			if r < 0x20 {
				name.WriteString(`\u00`)
				name.WriteByte(hex[r>>4])
				name.WriteByte(hex[r&15])
			} else {
				name.WriteRune(r)
			}
		}
	}
	name.WriteByte('"')
}
