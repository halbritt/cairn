package memoryclient

import (
	"context"
	"encoding/json"
	"strings"
)

func (c *Client) Checkpoint(ctx context.Context, title string) (json.RawMessage, error) {
	room := 16000
	view, err := c.Search(ctx, `"`+title+`"`, SearchOptions{Room: &room})
	if err != nil {
		return nil, err
	}
	prefix := title + "\n"
	for _, entry := range view.Index {
		if !strings.HasPrefix(entry.Summary, prefix) {
			continue
		}
		raw, err := c.Pull(ctx, entry.PullArguments, callTimeout)
		if err != nil {
			return nil, err
		}
		record, body, err := checkpointRecord(raw)
		if err != nil {
			return nil, err
		}
		if strings.HasPrefix(body, prefix) {
			return record, nil
		}
	}
	return nil, nil
}

func checkpointRecord(raw json.RawMessage) (json.RawMessage, string, error) {
	var expansion map[string]json.RawMessage
	if err := json.Unmarshal(raw, &expansion); err != nil {
		return nil, "", &responseError{reason: ErrCheckpointRecord, cause: err}
	}
	var selection map[string]json.RawMessage
	if err := json.Unmarshal(expansion["selection"], &selection); err != nil || selection == nil {
		return nil, "", &responseError{reason: ErrCheckpointRecord, cause: err}
	}
	var members map[string]json.RawMessage
	if err := json.Unmarshal(selection["record"], &members); err != nil || members == nil {
		return nil, "", &responseError{reason: ErrCheckpointRecord, cause: err}
	}
	var body *string
	if err := json.Unmarshal(members["body"], &body); err != nil || body == nil {
		return nil, "", &responseError{reason: ErrCheckpointRecord, cause: err}
	}
	return selection["record"], *body, nil
}
