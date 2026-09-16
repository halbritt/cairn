package wakeup

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

// WakeContext is selected coordination data for the launched process. It contains
// token file locations in the completion command, never credential contents.
type WakeContext struct {
	Schema             string                `json:"schema"`
	AttemptID          string                `json:"attempt_id"`
	NativeRegistration bool                  `json:"native_registration"`
	Session            *core.AgentSessionRef `json:"session,omitempty"`
	Socket             string                `json:"socket"`
	TokenFile          string                `json:"token_file"`
	ExecutionID        string                `json:"execution_id"`
	Binding            string                `json:"binding"`
	Inbox              string                `json:"inbox"`
	Collection         string                `json:"collection"`
	Workspace          string                `json:"workspace"`
	DeliveryID         string                `json:"delivery_id"`
	EventID            string                `json:"event_id"`
	Source             core.RecordVersionRef `json:"source"`
	LeaseID            string                `json:"lease_id"`
	Deadline           time.Time             `json:"deadline"`
	Completion         []string              `json:"completion"`
}

func writeContext(c Config, w core.WakeAttempt, executable string, deadline time.Time) (WakeContext, string, error) {
	context := WakeContext{
		Schema: "cairn.wake-context/1", ExecutionID: w.ID, Binding: c.Name,
		AttemptID: w.ID, NativeRegistration: true, Socket: c.Socket, TokenFile: c.AgentToken,
		Inbox: c.Principal, Collection: c.Repo, Workspace: c.Directory,
		DeliveryID: w.Delivery.DeliveryID, EventID: w.Delivery.Event.EventID,
		Source: w.Delivery.Event.Ref, LeaseID: w.Delivery.LeaseID, Deadline: deadline,
		Completion: []string{executable, "complete", "--socket", c.Socket, "--token-file", c.AgentToken,
			"--request-id", uuid.NewString(), "--lease", w.Delivery.LeaseID, "--shareable", "--stdin", w.Delivery.DeliveryID},
	}
	path := filepath.Join(c.StateDirectory, w.ID+".context.json")
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return context, path, err
	}
	// No process starts until the complete file has been written and closed.
	err = json.NewEncoder(f).Encode(context)
	return context, path, errors.Join(err, f.Close())
}
