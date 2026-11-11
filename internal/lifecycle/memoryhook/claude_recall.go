package memoryhook

import (
	"context"
	"errors"
	"os"
	"syscall"

	"github.com/halbritt/cairn/internal/indexview"
	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/ledger"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// claudeRecall is the reached Claude Start/prompt consumer. Unbound calls retain
// ordinary Source recall; a binding must be resolved by the checked entry.
func claudeRecall(ctx context.Context, client *memoryclient.Client, config, event, state, status *value.Value, project string) (string, error) {
	if owner := event.Get("prompt_id"); owner != nil && owner.Kind == value.String && !validText(owner) {
		return "", &value.Error{Class: "UnicodeEncodeError", Message: "invalid Claude prompt owner text"}
	}
	return ordinaryRecall(ctx, client, config, event, state, status, project)
}

func requiredMemoryOutput(config, event *value.Value, err error, diagnostic string) Result {
	if boundClaude(config) && value.SameText(event.Get("hook_event_name"), "SessionStart") {
		return Result{ExitCode: 1, Stderr: diagnostic + "Cairn lifecycle: " + err.Error() + "; use native tools or an explicit handoff.\n"}
	}
	return requiredOutput(event, diagnostic)
}

func claudeFailure(config, event *value.Value, err error) Result {
	if boundClaude(config) && isRequired(err) {
		return requiredMemoryOutput(config, event, err, "Cairn lifecycle diagnostic: phase=handle error_class=RequiredContextRefused\n")
	}
	return failure(event, err)
}

// claudeAgentContext runs under the existing memory session lock. Its owner is
// a real host prompt label, never a fabricated Codex native turn or API identity.
func claudeAgentContext(ctx context.Context, client *memoryclient.Client, config, event, result, selected, status *value.Value, index []indexview.Entry, budget int) (string, error) {
	path, err := claudeLedgerPath(config, event)
	if err != nil {
		return "", err
	}
	grants, err := loadClaudeLedger(path, text(event.Get("session_id")))
	if err != nil {
		return "", err
	}
	task, err := claudeTaskKey(config)
	if err != nil {
		return "", err
	}
	grant := claudeRequiredGrant(grants, event, task)
	owner := ""
	if value.SameText(event.Get("hook_event_name"), "UserPromptSubmit") {
		owner = claudePromptOwner(event)
	}
	existing := false
	for _, item := range grants.Get("grants").Members {
		existing = existing || owner != "" && claudeGrantMatches(item.Value, owner, task)
	}
	requiredOnly := owner == "" || existing || binding.TasklessNotification(event.Get("prompt"))
	if grant == nil {
		grant = grants
	}
	rawSelection, err := encoded(selected, false)
	if err != nil {
		return "", err
	}
	ledger.CodexRequiredSelection(grant, selected, rawSelection)
	persist := func() error {
		err := saveClaudeLedger(path, grants)
		var pathErr *os.PathError
		var errno syscall.Errno
		if (errors.As(err, &pathErr) || errors.As(err, &errno)) && len(selected.Items) > 0 {
			return required("current required Claude delivery status could not be persisted")
		}
		return err
	}
	body := ""
	if len(selected.Items) > 0 {
		body, err = ordinaryRender(selected, array(), nil, nil)
		if err != nil {
			return "", err
		}
	}
	cost := func(body string) int {
		if body == "" {
			return 0
		}
		wire, encodeErr := encoded(hookOutputFor(event, body), false)
		if encodeErr != nil {
			panic(encodeErr) // body and event have already passed strict text encoding
		}
		return len(wire) + 1
	}
	refuse := func(message string) (string, error) {
		grant.Get("required").Set("pending", value.Boolean(true))
		if err := persist(); err != nil {
			return "", err
		}
		return "", required(message)
	}
	if cost(body) > budget {
		return refuse("current required Claude context cannot fit whole")
	}
	if requiredOnly {
		if value.Truth(grants.Get("active")) {
			ledger.AddCodexCounter(grant, "required_refresh_bytes", cost(body))
		} else {
			if number(grants.Get("pending_bytes"))+cost(body) > budget {
				return refuse("initial whole required Claude context exceeds grant budget")
			}
			ledger.AddCodexCounter(grants, "pending_bytes", cost(body))
		}
		outcome := "empty"
		if len(selected.Items) > 0 {
			outcome = "required_only"
		}
		status.Set("outcome", value.Text(outcome))
		status.Set("discovery", value.Text("deferred"))
		status.Set("optional_deferred", value.Text("bound_claude_required_refresh"))
	} else {
		prior := number(grants.Get("pending_bytes"))
		remaining := budget - prior
		if cost(body) > remaining {
			return refuse("whole required Claude context exceeds initial allowance")
		}
		body, err = eagerCandidates(ctx, client, config, selected, result, index, remaining, status, cost)
		if err != nil {
			return "", err
		}
		identity := value.Mapping(value.Field("native_owner", value.Text(owner)), value.Field("boundary", value.Text("ordinary_prompt")))
		keyInput := value.Mapping(value.Field("claude_prompt", value.Text(owner)))
		if task != "" {
			identity.Set("recall_task_key", value.Text(task))
			identity.Set("boundary", value.Text("launcher_task"))
			keyInput = value.Mapping(value.Field("recall_task_key", value.Text(task)))
		}
		key, err := claudeGrantKey(keyInput)
		if err != nil {
			return "", err
		}
		current := value.Mapping(value.Field("identity", identity), value.Field("limit_bytes", integer(budget)), value.Field("output_bytes", integer(cost(body)+prior)), value.Field("native_allowance_bytes", integer(number(status.Get("native_allowance_bytes")))))
		if number(current.Get("output_bytes"))+number(current.Get("native_allowance_bytes")) > budget {
			return "", fail("Claude prompt context exceeds combined budget")
		}
		ledger.CodexRequiredSelection(current, selected, rawSelection)
		grants.Get("grants").Set(key, current)
		grants.Set("active", value.Text(key))
		grants.Set("pending_bytes", integer(0))
	}
	scope := "native_prompt"
	if task != "" {
		scope = "launcher_task"
	}
	status.Set("recall_task_scope", value.Text(scope))
	if err := persist(); err != nil {
		return "", err
	}
	status.Set("bytes", integer(len(body)))
	return body, nil
}
