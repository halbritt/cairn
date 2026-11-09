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
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/indexview"
	"github.com/halbritt/cairn/internal/jsontext"
)

type CheckpointRefreshInput struct {
	ProjectBase string
	Workstream  *string
	Selected    []core.Selection
}

// CheckpointRefreshResult is an ordinary advisory view. Cause is for inspection
// only; callers must not render it or turn an optional miss into a required refusal.
type CheckpointRefreshResult struct {
	Text   string
	Reason string
	Cause  error
}

func refreshInputError() error {
	return &core.Error{Code: "INVALID_REQUEST", Message: "checkpoint refresh requires valid Unicode input and an enclosing deadline"}
}

// RefreshCheckpoint restores at most one whole unfinished note. The caller owns
// the enclosing deadline, SDK lifetime, output budget and all frontend state.
func (c *Client) RefreshCheckpoint(ctx context.Context, input CheckpointRefreshInput) (CheckpointRefreshResult, error) {
	title, err := refreshTitle(input)
	if err != nil || title == nil {
		return CheckpointRefreshResult{}, err
	}
	prefix := *title + "\n"
	for _, selected := range input.Selected {
		if strings.HasPrefix(selected.Record.Body, prefix) {
			return CheckpointRefreshResult{Reason: "covered_by_required"}, nil
		}
	}
	if ctx == nil {
		return CheckpointRefreshResult{}, refreshInputError()
	}
	if _, hasDeadline := ctx.Deadline(); !hasDeadline {
		return CheckpointRefreshResult{}, refreshInputError()
	}
	cue := "Current unfinished checkpoint was not restored for " + *title +
		"; search current Cairn memory before relying on earlier context."
	entry, reason, err := c.refreshEntry(ctx, *title)
	if err != nil {
		return CheckpointRefreshResult{Text: cue, Reason: "unavailable", Cause: err}, nil
	}
	if reason != "" {
		return CheckpointRefreshResult{Text: cue, Reason: reason}, nil
	}
	raw, body, err := c.refreshPull(ctx, *entry)
	if err != nil {
		return CheckpointRefreshResult{Text: cue, Reason: "unavailable", Cause: err}, nil
	}
	expansion, _ := captureObject(raw) // Pull has already confirmed object data.
	extent, _ := captureText(expansion["source_extent"])
	span, hasSpan := expansion["span"]
	if extent == "partial_span" || (hasSpan && !captureNull(span)) ||
		!strings.HasPrefix(body, prefix) || len(body) > 4500 {
		return CheckpointRefreshResult{Text: cue, Reason: "not_whole"}, nil
	}
	leading := strings.TrimLeftFunc(body[len(*title):], func(r rune) bool {
		return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
	})
	if strings.HasPrefix(leading, "Status: complete\n") {
		return CheckpointRefreshResult{Reason: "complete"}, nil
	}
	return CheckpointRefreshResult{Text: refreshText(*entry, body), Reason: "restored"}, nil
}

func refreshTitle(input CheckpointRefreshInput) (*string, error) {
	if input.Workstream == nil {
		return nil, nil
	}
	if !utf8.ValidString(input.ProjectBase) {
		return nil, refreshInputError()
	}
	prefix := "Handoff: " + captureClip(strings.ReplaceAll(input.ProjectBase, "\"", "")) + " / "
	title := *input.Workstream
	if !strings.HasPrefix(title, prefix) {
		return nil, nil
	}
	if !utf8.ValidString(title) {
		return nil, refreshInputError()
	}
	if len(title) > 256 || strings.ContainsAny(title, "\r\n\"") {
		return nil, nil
	}
	return &title, nil
}

func refreshCallLimit(remaining time.Duration) (time.Duration, error) {
	if remaining <= 0 {
		return 0, context.DeadlineExceeded
	}
	return min(2*time.Second, remaining), nil
}

func refreshTimeout(ctx context.Context) (time.Duration, error) {
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	deadline, _ := ctx.Deadline() // RefreshCheckpoint admits only deadline-bearing contexts.
	return refreshCallLimit(time.Until(deadline))
}

func (c *Client) refreshEntry(ctx context.Context, title string) (*indexview.Entry, string, error) {
	room, offset := 16000, 0
	var entries []indexview.Entry
	for range 3 {
		timeout, err := refreshTimeout(ctx)
		if err != nil {
			return nil, "", err
		}
		view, err := c.Search(ctx, `"`+title+`"`, SearchOptions{
			Room: &room, Kinds: []string{"note"}, Offset: &offset, Timeout: &timeout,
		})
		if err != nil {
			return nil, "", err
		}
		if view.Page == nil || view.Page.Offset != offset {
			return nil, "incomplete_search", nil
		}
		for _, entry := range view.Index {
			if entry.Class == "A" && entry.Kind == "note" && strings.HasPrefix(entry.Summary, title+"\n") && len(entry.Conflicts) == 0 {
				entries = append(entries, entry)
			}
		}
		if len(entries) > 1 {
			return nil, "ambiguous", nil
		}
		if view.Page.NextOffset == nil {
			if len(entries) == 0 {
				return nil, "not_found", nil
			}
			return &entries[0], "", nil
		}
		if *view.Page.NextOffset <= offset {
			return nil, "incomplete_search", nil
		}
		offset = *view.Page.NextOffset
	}
	return nil, "incomplete_search", nil
}

func refreshPullID(original core.ExpandRequest) string {
	var name strings.Builder
	name.WriteString(`["cairn.lifecycle.whole-pull/1",{"handle":`)
	captureNameString(&name, original.Handle)
	name.WriteString(`,"receipt_id":`)
	captureNameString(&name, original.ReceiptID)
	name.WriteString(`,"request_id":`)
	captureNameString(&name, original.RequestID)
	name.WriteString(`}]`)
	return uuid.NewSHA1(uuid.NameSpaceURL, []byte(name.String())).String()
}

func (c *Client) refreshPull(ctx context.Context, entry indexview.Entry) (json.RawMessage, string, error) {
	request := entry.PullArguments
	request.RequestID = refreshPullID(request)
	timeout, err := refreshTimeout(ctx)
	if err != nil {
		return nil, "", err
	}
	raw, err := c.Pull(ctx, request, timeout)
	if err != nil {
		return nil, "", err
	}
	expansion, _ := captureObject(raw)
	selection, _ := captureObject(expansion["selection"])
	record, _ := captureObject(selection["record"])
	id, idOK := captureText(record["record_id"])
	version, versionErr := strconv.Atoi(string(bytes.TrimSpace(record["version"])))
	body, bodyOK := captureText(record["body"])
	if !idOK || id != entry.RecordID || versionErr != nil || version != entry.Version || !bodyOK ||
		jsontext.CheckUnicode(record["record_id"]) != nil || jsontext.CheckUnicode(record["body"]) != nil {
		return nil, "", errors.New("optional body identity or version changed")
	}
	if entry.BodySHA256 != "" {
		sum := sha256.Sum256([]byte(body))
		if hex.EncodeToString(sum[:]) != entry.BodySHA256 {
			return nil, "", errors.New("optional body hash changed")
		}
	}
	return raw, body, nil
}

func refreshText(entry indexview.Entry, body string) string {
	var text strings.Builder
	text.WriteString("Current saved unfinished work (ordinary note, verify against this task):\n")
	text.WriteString(`{"schema":"cairn.checkpoint-refresh/1","record_id":`)
	captureNameString(&text, entry.RecordID)
	text.WriteString(`,"version":`)
	text.WriteString(strconv.Itoa(entry.Version))
	text.WriteString(`,"source_extent":"whole","body":`)
	captureNameString(&text, body)
	text.WriteByte('}')
	return text.String()
}
