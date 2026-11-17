package native

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"reflect"
	"strings"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/lifecycle/coordination"
	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/ledger"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/procpeer"
	"github.com/halbritt/cairn/localapi"
)

type SessionFacts struct {
	NativeID  string                 `json:"native_id"`
	Joined    procpeer.ProcessRef    `json:"joined"`
	Store     core.SessionQuiescence `json:"store"`
	GrantHash string                 `json:"grant_sha256"`
	Grants    int                    `json:"retained_grants"`
}

type quiescencePass struct {
	sessions []SessionFacts
	files    map[string]string
	readers  []*install.SettingsSnapshot
}

func (p *quiescencePass) observe(ctx context.Context, path string) (*install.SettingsSnapshot, error) {
	snapshot, err := install.InspectSettings(ctx, path)
	if err != nil || !snapshot.Metadata().Present {
		return nil, errors.Join(err, errors.New("required native state/accounting file is missing"))
	}
	p.readers = append(p.readers, snapshot)
	raw, err := json.Marshal(snapshot.Metadata())
	if err != nil {
		return nil, err
	}
	digest := sha256.Sum256(raw)
	p.files[path] = hex.EncodeToString(digest[:])
	return snapshot, nil
}

func (o *Owner) quiescenceLocked(ctx context.Context) Observation {
	if o != nil && o.pendingMutation {
		result := o.inspectLocked(ctx)
		result.Verdict, result.Reason = Unknown, "native publication recovery is unresolved"
		return result
	}
	return o.readQuiescenceLocked(ctx)
}

// readQuiescenceLocked is also used during the live publisher's checked
// transaction, while ordinary admission and competing publication stay closed.
func (o *Owner) readQuiescenceLocked(ctx context.Context) Observation {
	result := o.inspectLocked(ctx)
	if result.Verdict == Busy || result.Owner.PID == 0 || o == nil || o.origin != o || o.closed || o.uncertain {
		return result
	}
	if !o.controller || o.phase != "joined" || len(o.nativeIDs) == 0 || len(o.joined) != len(o.nativeIDs) || o.material == nil {
		result.Reason += "; qualified joined native history and selected profiles are required"
		return result
	}
	started := time.Now()
	bounded, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	first, err := o.quiescencePass(bounded)
	if err != nil {
		result.Reason = err.Error()
		if strings.HasPrefix(result.Reason, "BUSY:") {
			result.Verdict = Busy
		}
		return result
	}
	second, err := o.quiescencePass(bounded)
	if err != nil || !reflect.DeepEqual(first.files, second.files) || len(first.sessions) != len(second.sessions) || time.Since(started) >= 30*time.Second {
		result.Reason = "native/store/state observation changed or expired"
		if err != nil {
			result.Reason += ": " + err.Error()
		}
		return result
	}
	for i := range first.sessions {
		if first.sessions[i].Store.FactsSHA256 != second.sessions[i].Store.FactsSHA256 || first.sessions[i].GrantHash != second.sessions[i].GrantHash {
			result.Reason = "native store or memory grants changed during read"
			return result
		}
	}
	result.Verdict, result.Reason = Quiescent, "joined fresh-only native sessions; custody, journals and accounting correspond; no resume/restart authority"
	result.Sessions = second.sessions
	result.Files = second.files
	return result
}

func (o *Owner) quiescencePass(ctx context.Context) (*quiescencePass, error) {
	m := o.material
	if err := errors.Join(m.recheck(ctx), o.settings.Recheck(ctx), checkImage(ctx, o.image, o.imageInfo, o.config.Client)); err != nil {
		return nil, err
	}
	children, err := controllerChildren()
	if err != nil || len(children) != 0 {
		return nil, errors.Join(err, errors.New("native controller descendants are not joined"))
	}
	api, err := localapi.NewClient(m.coord.Socket, m.coord.TokenFile)
	if err != nil {
		return nil, err
	}
	defer api.Close()
	pass := &quiescencePass{files: map[string]string{}}
	owned := map[string]bool{}
	indexed := []string{}
	for i, nativeID := range o.nativeIDs {
		key := sha256.Sum256([]byte(m.coord.Binding + "\x00" + nativeID))
		path := filepath.Join(m.coord.StateDir, hex.EncodeToString(key[:])+".json")
		snapshot, err := pass.observe(ctx, path)
		if err != nil || !snapshot.Metadata().Present {
			return nil, errors.Join(err, errors.New("owned native state is missing"))
		}
		state, err := value.DecodeBytesWithObjectPolicy(snapshot.Bytes(), 0, value.DuplicateReject)
		if err != nil || state.Kind != value.Object {
			return nil, errors.Join(err, errors.New("owned native state is malformed"))
		}
		var selected struct {
			Schema       string `json:"schema"`
			Registration struct {
				NativeID string `json:"native_session_id"`
				Binding  string `json:"binding"`
				Repo     string `json:"repo"`
			} `json:"registration"`
			Process  procpeer.ProcessRef `json:"process"`
			Agent    core.AgentInstance  `json:"agent"`
			Retired  bool                `json:"retired"`
			Journals []string            `json:"inbox_journals"`
			Policy   *struct {
				Version   int    `json:"version"`
				RequestID string `json:"claim_request_id"`
			} `json:"inbox_result_policy"`
		}
		if err := strictJSON(snapshot.Bytes(), &selected); err != nil {
			return nil, err
		}
		if selected.Schema != "cairn.native-session/1" || selected.Registration.NativeID != nativeID || selected.Registration.Binding != m.coord.Binding || selected.Registration.Repo != m.coord.Repo || selected.Process != o.joined[i] || !selected.Retired || selected.Agent.AgentID == "" || selected.Agent.NativeSessionID != nativeID {
			return nil, errors.New("native state/owner/retirement correspondence is incomplete")
		}
		for _, name := range append(append([]string{}, coordination.AttemptKeys...), "active_prompt", "idle_wake") {
			if name == "inbox_result_policy" {
				continue
			}
			if value.Truth(state.Get(name)) {
				return nil, fmt.Errorf("owned native %s is unresolved", name)
			}
		}
		ref := core.AgentSessionRef{AgentID: selected.Agent.AgentID, ExecutionID: selected.Agent.ExecutionID}
		query := core.SessionQuiescenceQuery{Session: ref}
		if selected.Policy != nil {
			if selected.Policy.Version != 1 || selected.Policy.RequestID == "" {
				return nil, errors.New("native result policy is unknown")
			}
			query.PollRequestID = selected.Policy.RequestID
		}
		remote, err := api.SessionQuiescence(ctx, query)
		if err != nil {
			return nil, err
		}
		if remote.Schema != "cairn.session-quiescence/1" || !remote.Complete || remote.Session != ref || remote.NativeSessionID != nativeID || remote.Binding != m.coord.Binding || remote.Repo != m.coord.Repo || remote.ContextRevision != selected.Agent.ContextRevision || remote.Generation != selected.Agent.DatabaseGeneration || !remote.Stopped {
			return nil, errors.New("native/store identity, generation or terminal correspondence is unknown")
		}
		if remote.Hold != nil || len(remote.Leases) != 0 || len(remote.Wakes) != 0 || remote.Pending != 0 {
			return nil, errors.New("BUSY: native inbox custody, lease or associated wake remains")
		}
		if selected.Policy != nil && (remote.Poll == nil || !remote.PollTerminal || remote.Poll.RequestID != selected.Policy.RequestID || remote.Poll.Session != ref) {
			return nil, errors.New("native result policy has no terminal store correspondence")
		}
		owned[ref.AgentID] = true
		indexed = append(indexed, selected.Journals...)
		ledgerPath := filepath.Join(m.memory.StateDir, nativeID+".inbox-recall.json")
		if _, err := pass.observe(ctx, ledgerPath); err != nil {
			return nil, err
		}
		if _, err := pass.observe(ctx, filepath.Join(m.memory.StateDir, nativeID+".inbox-recall.initialized")); err != nil {
			return nil, err
		}
		grants, err := ledger.InboxGrants(ledgerPath, nativeID)
		if err != nil {
			return nil, err
		}
		// Joined fresh-only sessions can never consume these native IDs again.
		// Retain every charge and positive grant; do not refund/reset accounting.
		raw, err := json.Marshal(grants)
		if err != nil {
			return nil, err
		}
		digest := sha256.Sum256(raw)
		pass.sessions = append(pass.sessions, SessionFacts{nativeID, o.joined[i], remote, hex.EncodeToString(digest[:]), len(grants.Get("grants").Members)})
	}
	inspection, err := coordination.InspectWatchJournals(m.coord.StateDir, indexed)
	if err != nil {
		return nil, err
	}
	required := map[string]bool{}
	for _, id := range indexed {
		required[id] = true
	}
	for _, row := range inspection.Journals {
		status := row.Status
		if !status.Exists || status.NonObject || status.ReadErr != nil || status.ParseErr != nil {
			return nil, errors.New("native journal ownership or contents are unknown")
		}
		if !owned[status.Session.AgentID] {
			if required[row.AttemptID] {
				return nil, errors.New("indexed native journal has a different owner")
			}
			continue
		}
		if !status.Exists || status.NonObject || status.ReadErr != nil || status.ParseErr != nil || status.Schema != "cairn.inbox-intent/1" || status.PendingReplay || status.ReplyPossible || status.UnmaterializedFinishReply {
			return nil, errors.New("owned native journal is unknown, replayable or reply_possible")
		}
		if _, err := pass.observe(ctx, filepath.Join(m.coord.StateDir, "intents", row.AttemptID+".json")); err != nil {
			return nil, err
		}
	}
	for _, snapshot := range pass.readers {
		if err := snapshot.Recheck(ctx); err != nil {
			return nil, err
		}
	}
	return pass, nil
}
