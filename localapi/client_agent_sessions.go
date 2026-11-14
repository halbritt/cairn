package localapi

import (
	"context"

	"github.com/halbritt/cairn/core"
)

// SessionInboxAttemptStatus reads a retained native attempt through the base
// profile and its current session. A missing attempt is an error, never a
// terminal-state result or permission to release a hold.
func (c *Client) SessionInboxAttemptStatus(ctx context.Context, session core.AgentSessionRef, attemptID string) (core.SessionInboxAttempt, error) {
	var out core.SessionInboxAttempt
	err := c.Call(ctx, "session-inbox-attempt-status", core.SessionInboxAttemptQuery{Session: session, AttemptID: attemptID}, &out)
	return out, err
}

// SessionInboxPollStatus reads a durable poll, including an explicit empty
// result. It does not claim work or settle a native attempt.
func (c *Client) SessionInboxPollStatus(ctx context.Context, session core.AgentSessionRef, requestID string) (core.SessionInboxPoll, error) {
	var out core.SessionInboxPoll
	err := c.Call(ctx, "session-inbox-poll-status", core.SessionInboxPollQuery{Session: session, RequestID: requestID}, &out)
	return out, err
}
