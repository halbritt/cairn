package main

import (
	"context"
	"io"
	"net/url"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"time"

	"github.com/halbritt/cairn/artifacts"
	"github.com/halbritt/cairn/core"
)

const help = `Cairn: local memory for agents

Everyday commands:
  agent [--token-file FILE] [--socket PATH] OPERATION < request.json
  agent [--token-file FILE] [--socket PATH] search --task TASK --run RUN [--repo REPO] QUERY
  agent [--token-file FILE] [--socket PATH] pull [--request-id UUID] RECEIPT_UUID HANDLE_UUID
  agent [--token-file FILE] [--socket PATH] pull-evidence [--request-id UUID] RECEIPT_UUID HANDLE_UUID EVIDENCE_UUID EXPECTED_SHA256
  agent [--token-file OBSERVER_TOKEN] [--socket PATH] run RUN_FLAGS -- COMMAND ARGS...
  agent [--token-file FILE] [--socket PATH] remember [--repo REPO] [--kind KIND] [--shareable] [--task TASK] [--run RUN] [--request-id UUID] TEXT
  agent [--token-file FILE] [--socket PATH] remember [FLAGS] --stdin < note.txt
  remember [--repo PATH] [--kind KIND] [--shareable] [--task TASK] [--run RUN] [--request-id UUID] TEXT
  remember [FLAGS] --stdin < note.txt
  search [--repo PATH] [--purpose context] [--destination local] QUERY
  run [--repo PATH] [--prompt TEXT] [--carrier stdin|argv] [--destination local|hosted] -- COMMAND ARGS...
  preview-delete RECORD_UUID | deletion-status DELETION_UUID | purge-deletion DELETION_UUID
  conflicts [--record UUID] [--include-resolved] [--limit N] [--offset N] REPO | conflict UUID
  proposal-group [--limit N] [--offset N] REPO GROUP_DIGEST
  list REPO | get UUID | use-report REPO | run-report [--limit N] [--offset N] REPO | report REPO | docket REPO | impact UUID | replay RECEIPT_UUID | explain RECEIPT_UUID | preview-retract RECORD_UUID

JSON commands (read one request from stdin):
  create edit delete compile index expand expand-evidence bootstrap grant revoke-grant capture-evidence check-evidence
  promote demote issue correct supersede retract forget dispute resolve usage assess-run recompile generate-proposals review-proposal
  supersession RECORD_UUID
  authorize-scope | scope-authorization RECORD_UUID
  policy-revise | policy REPO | policy-revision REVISION_UUID
  instruction-policy RECORD_UUID
  runs [--policy-rev REVISION_UUID|local-loop/1] [--limit N] [--offset N] REPO
  grants (no input)
  run-status RECEIPT_UUID (owner-only process observation; never launch permission)
  recover-run RECEIPT_UUID (retry a runner-owned pending outcome)

Administration: recovery-export FILE | recovery-inspect FILE
  recovery-reapply --request-id UUID --expected-sha256 DIGEST --reason TEXT FILE
  begin-restore | restore-status | rebuild-restore | verify-restore | resume-restore
  migrate | fence-restore < request.json | invalidate-handles < request.json | checkpoint < request.json | verify-checkpoint < expectation.json | serve [--identities FILE] [--socket PATH]
Default store: ~/.local/share/cairn/socket, database cairn.
Override with CAIRN_DATABASE_URL. Initialize with scripts/local-store.sh start.
CLI is trusted operator administration. Agents use a host-established core Channel.
run forwards process output; its final receipt envelope is on stderr.
replay is historical inspection, not a current delivery authorization.`

func invalid(message string) error { return &core.Error{Code: "INVALID_REQUEST", Message: message} }
func dataDirectory() (string, error) {
	if configured := os.Getenv("CAIRN_HOME"); configured != "" {
		return filepath.Abs(configured)
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "share", "cairn"), nil
}
func databaseURL() (string, error) {
	if dsn := os.Getenv("CAIRN_DATABASE_URL"); dsn != "" {
		return dsn, nil
	}
	directory, err := dataDirectory()
	if err != nil {
		return "", err
	}
	owner, err := user.Current()
	if err != nil {
		return "", err
	}
	uri := url.URL{Scheme: "postgresql", User: url.User(owner.Username), Path: "/cairn", RawQuery: url.Values{"host": {filepath.Join(directory, "socket")}, "sslmode": {"disable"}}.Encode()}
	return uri.String(), nil
}
func run(ctx context.Context, args []string, input io.Reader) (any, error) {
	if len(args) == 1 && (args[0] == "help" || args[0] == "--help") {
		return help, nil
	}
	if len(args) == 0 {
		return nil, invalid("expected a command; use --help")
	}
	if args[0] == "agent" {
		return agentRequest(ctx, args[1:], input)
	}
	channel := core.Channel{Principal: "local-uid:" + strconv.Itoa(os.Geteuid()), Operator: true}
	if args[0] == "run" || args[0] == "recover-run" || args[0] == "purge-deletion" {
		channel.Instrumented = true
	}
	dsn, err := databaseURL()
	if err != nil {
		return nil, err
	}
	if args[0] == "serve" {
		return nil, serveLocal(ctx, dsn, args[1:])
	}
	connectCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	store, err := core.Open(connectCtx, dsn, channel)
	cancel()
	if err != nil {
		return nil, err
	}
	defer store.Close()
	if args[0] == "run" {
		return runTask(ctx, store, args[1:])
	}
	ctx, cancel = context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	switch args[0] {
	case "recovery-export", "recovery-inspect":
		if len(args) != 2 {
			return nil, invalid("recovery command requires one file path")
		}
		if args[0] == "recovery-export" {
			return exportRecovery(ctx, store, args[1])
		}
		return inspectRecovery(ctx, store, args[1])
	case "recovery-reapply":
		f := flags("recovery-reapply")
		requestID := f.String("request-id", "", "stable UUID for retrying this application")
		expected := f.String("expected-sha256", "", "trusted recovery record content digest")
		reason := f.String("reason", "", "reason for reapplying these restrictions")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if f.NArg() != 1 || *requestID == "" || *expected == "" || *reason == "" {
			return nil, invalid("recovery-reapply requires request-id, expected-sha256, reason and one file")
		}
		record, err := readRecoveryFile(f.Arg(0))
		if err != nil {
			return nil, err
		}
		if record.SHA256 != *expected {
			return nil, &core.Error{Code: "INTEGRITY_FAILURE", Message: "recovery record differs from the expected content digest"}
		}
		return store.ReapplyRecovery(ctx, core.RecoveryReapplyRequest{RequestID: *requestID, Record: record, Reason: *reason})
	case "recover-run":
		if len(args) != 2 {
			return nil, invalid("recover-run requires a receipt UUID")
		}
		return recoverRun(ctx, store, args[1])
	case "remember":
		return remember(ctx, store, args[1:], input)
	case "search":
		return search(ctx, store, args[1:])
	case "proposal-group":
		f := flags("proposal-group")
		limit := f.Int("limit", 100, "maximum source proposals (1-200)")
		offset := f.Int("offset", 0, "source proposals to skip")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if f.NArg() != 2 {
			return nil, invalid("proposal-group requires repository and group digest")
		}
		return store.ProposalGroup(ctx, core.ProposalGroupRequest{Repo: f.Arg(0), Key: f.Arg(1), Limit: *limit, Offset: *offset})
	case "conflicts":
		f := flags("conflicts")
		limit := f.Int("limit", 100, "maximum rows (1-200)")
		offset := f.Int("offset", 0, "rows to skip")
		record := f.String("record", "", "filter by a member record UUID")
		resolved := f.Bool("include-resolved", false, "include resolved history")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if f.NArg() != 1 {
			return nil, invalid("conflicts requires one repository")
		}
		return store.Conflicts(ctx, core.ConflictsRequest{Repo: f.Arg(0), RecordID: *record, IncludeResolved: *resolved, Limit: *limit, Offset: *offset})
	case "run-report", "runs":
		f := flags("run-report")
		limit := f.Int("limit", 100, "maximum rows (1-200)")
		offset := f.Int("offset", 0, "rows to skip")
		policy := f.String("policy-rev", "", "filter by policy revision UUID or local-loop/1")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if f.NArg() != 1 {
			return nil, invalid("run-report requires one repository")
		}
		return store.RunReport(ctx, core.RunReportRequest{Repo: f.Arg(0), PolicyRevision: *policy, Limit: *limit, Offset: *offset})
	case "run-status":
		if len(args) != 2 {
			return nil, invalid("run-status requires a receipt UUID")
		}
		return store.RunStatus(ctx, args[1])
	case "policy", "policy-revision", "instruction-policy":
		if len(args) != 2 {
			return nil, invalid("policy requires a repository; policy-revision requires a revision UUID; instruction-policy requires a record UUID")
		}
		if args[0] == "policy" {
			return store.Policy(ctx, args[1])
		}
		if args[0] == "instruction-policy" {
			return store.InstructionPolicy(ctx, args[1])
		}
		return store.PolicyRevision(ctx, args[1])
	case "get", "replay", "report", "list", "impact", "docket", "explain", "preview-retract", "use-report", "assessments", "proposal", "evidence", "refusal", "evidence-checks", "preview-delete", "deletion-status", "purge-deletion", "conflict", "supersession", "scope-authorization":
		if len(args) != 2 {
			return nil, invalid("command requires one identifier or repository")
		}
		switch args[0] {
		case "preview-delete":
			return store.PreviewDeletion(ctx, args[1])
		case "deletion-status":
			return store.DeletionStatus(ctx, args[1])
		case "purge-deletion":
			return artifacts.PurgeDeletion(ctx, store, args[1])
		case "evidence-checks":
			return store.EvidenceChecks(ctx, args[1])
		case "refusal":
			return store.Refusal(ctx, args[1])
		case "proposal":
			return store.Proposal(ctx, args[1])
		case "evidence":
			return store.ReadEvidence(ctx, args[1])
		case "assessments":
			return store.Assessments(ctx, args[1])
		case "conflict":
			return store.Conflict(ctx, args[1])
		case "use-report":
			return store.UseReport(ctx, core.UseReportRequest{Repo: args[1], Limit: 100})
		case "preview-retract":
			return store.PreviewRetraction(ctx, args[1])
		case "supersession":
			return store.Supersession(ctx, args[1])
		case "scope-authorization":
			return store.ScopeAuthorization(ctx, args[1])
		case "explain":
			return store.Explain(ctx, args[1])
		case "docket":
			return store.Docket(ctx, args[1])
		case "get":
			return store.Get(ctx, args[1])
		case "replay":
			p, err := store.Replay(ctx, args[1])
			return struct {
				Historical bool         `json:"historical"`
				Package    core.Package `json:"package"`
			}{true, p}, err
		case "report":
			return store.Report(ctx, args[1])
		case "list":
			return store.List(ctx, core.ListRequest{Repo: args[1], Limit: 100})
		case "impact":
			return store.Impact(ctx, args[1], 0)
		}
	}
	if len(args) != 1 {
		return nil, invalid("unexpected arguments")
	}
	switch args[0] {
	case "begin-restore":
		return invoke(ctx, input, store.BeginRestore)
	case "restore-status":
		return store.RestoreStatus(ctx)
	case "rebuild-restore":
		return invoke(ctx, input, store.RebuildRestore)
	case "verify-restore":
		var req core.VerifyRestoreRequest
		if err := decodeBounded(input, &req, 20*1024*1024); err != nil {
			return nil, invalid(err.Error())
		}
		result, err := store.VerifyRestore(ctx, req)
		if err == nil && !result.Ready {
			err = &core.Error{Code: "RESTORE_INCOMPLETE", Message: "restore checks failed; service remains paused"}
		}
		return result, err
	case "resume-restore":
		var req core.ResumeRestoreRequest
		if err := decodeBounded(input, &req, 20*1024*1024); err != nil {
			return nil, invalid(err.Error())
		}
		return store.ResumeRestore(ctx, req)
	case "fence-restore":
		return invoke(ctx, input, store.FenceRestore)
	case "invalidate-handles":
		return invoke(ctx, input, store.InvalidateHandles)
	case "checkpoint":
		return invoke(ctx, input, store.Checkpoint)
	case "verify-checkpoint":
		result, err := invoke(ctx, input, store.VerifyCheckpoint)
		if err == nil && !result.Valid {
			err = &core.Error{Code: "INTEGRITY_FAILURE", Message: "audit members differ from expected restore set"}
		}
		return result, err
	case "migrate":
		return nil, store.Migrate(ctx)
	case "grants":
		return store.Grants(ctx)
	case "create":
		return invoke(ctx, input, store.Create)
	case "edit":
		return invoke(ctx, input, store.Edit)
	case "delete":
		return invoke(ctx, input, store.Delete)
	case "bootstrap":
		return invoke(ctx, input, store.Bootstrap)
	case "grant":
		return invoke(ctx, input, store.Grant)
	case "revoke-grant":
		return invoke(ctx, input, store.RevokeGrant)
	case "check-evidence":
		return invoke(ctx, input, store.CheckEvidence)
	case "capture-evidence":
		return invoke(ctx, input, store.CaptureEvidence)
	case "demote":
		return invoke(ctx, input, store.Demote)
	case "promote":
		return invoke(ctx, input, store.Promote)
	case "issue":
		return invoke(ctx, input, store.Issue)
	case "correct":
		return invoke(ctx, input, store.Correct)
	case "supersede":
		return invoke(ctx, input, store.Supersede)
	case "authorize-scope":
		return invoke(ctx, input, store.AuthorizeScope)
	case "policy-revise":
		return invoke(ctx, input, store.RevisePolicy)
	case "forget":
		return invoke(ctx, input, store.Forget)
	case "retract":
		return invoke(ctx, input, store.Retract)
	case "dispute":
		return invoke(ctx, input, store.Dispute)
	case "resolve":
		return invoke(ctx, input, store.Resolve)
	case "generate-proposals":
		return invoke(ctx, input, store.GenerateProposals)
	case "review-proposal":
		return invoke(ctx, input, store.ReviewProposal)
	case "recompile":
		return invoke(ctx, input, func(ctx context.Context, req core.RecompileRequest) (any, error) {
			p, err := store.Recompile(ctx, req)
			return struct {
				Historical bool         `json:"historical"`
				Package    core.Package `json:"package"`
			}{true, p}, err
		})
	case "assess-run":
		return invoke(ctx, input, store.AssessRun)
	case "usage":
		return invoke(ctx, input, store.RecordUsage)
	case "index":
		return invoke(ctx, input, func(ctx context.Context, req core.CompileRequest) (core.IndexResult, error) {
			return store.Index(ctx, req, core.Destination{Name: "local", AllowLocal: true})
		})
	case "expand":
		return invoke(ctx, input, func(ctx context.Context, req core.ExpandRequest) (core.Expansion, error) {
			return store.Expand(ctx, req, core.Destination{Name: "local", AllowLocal: true})
		})
	case "expand-evidence":
		return invoke(ctx, input, func(ctx context.Context, req core.ExpandEvidenceRequest) (core.EvidenceExpansion, error) {
			return store.ExpandEvidence(ctx, req, core.Destination{Name: "local", AllowLocal: true})
		})
	case "compile":
		return invoke(ctx, input, func(ctx context.Context, req core.CompileRequest) (core.Package, error) {
			return store.Compile(ctx, req, core.Destination{Name: "local", AllowLocal: true})
		})
	default:
		return nil, invalid("unknown command; use --help")
	}
}

func recoverRun(ctx context.Context, store *core.Store, id string) (core.Observation, error) {
	// Validate the UUID with the store before using it as a filesystem component.
	if _, err := store.Replay(ctx, id); err != nil {
		return core.Observation{}, err
	}
	root, err := dataDirectory()
	if err != nil {
		return core.Observation{}, err
	}
	path := filepath.Join(root, "runs", id, "outcome.pending.json")
	info, err := os.Lstat(path)
	if err != nil {
		return core.Observation{}, err
	}
	if !info.Mode().IsRegular() {
		return core.Observation{}, invalid("pending outcome must be a regular runner-owned file")
	}
	file, err := os.Open(path)
	if err != nil {
		return core.Observation{}, err
	}
	defer file.Close()
	var req core.OutcomeRequest
	if err = decode(file, &req); err != nil {
		return core.Observation{}, err
	}
	if req.ReceiptID != id {
		return core.Observation{}, invalid("pending outcome names another receipt")
	}
	observed, err := store.RecordOutcome(ctx, req)
	if err != nil {
		return observed, err
	}
	return observed, os.Rename(path, filepath.Join(root, "runs", id, "outcome.json"))
}
func invoke[Request, Response any](ctx context.Context, input io.Reader, call func(context.Context, Request) (Response, error)) (Response, error) {
	var req Request
	var zero Response
	if err := decode(input, &req); err != nil {
		return zero, invalid(err.Error())
	}
	return call(ctx, req)
}
