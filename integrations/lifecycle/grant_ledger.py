#!/usr/bin/env python3
"""Unified lifecycle grant ledger state, locking, compaction, wire-budget accounting, and wake parsing."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import uuid


LOCK_WAIT_SECONDS = 1.5
LEDGER_RECENT_GRANTS = 64
LEDGER_RECENT_DECISIONS = 64
LEDGER_FOLDED_TOKENS = 4096
FOLD_DOMAIN = 'cairn.inbox-recall-fold/1'
GRANT_KINDS = ('inbox_delivery', 'ordinary_context', 'unadmitted_wake', 'mixed', 'unknown')
INBOX_LEDGER_SCHEMA = 'cairn.inbox-recall-grants/1'
INBOX_INIT_SCHEMA = 'cairn.inbox-recall-initialized/1'
CODEX_LEDGER_SCHEMA = 'cairn.codex-memory-grants/1'
WAKE_CLOSINGS = (
    "Do not register again, claim another session's inbox, or launch a replacement conversation.",
    "Do not register, manually claim an inbox, or launch a replacement conversation.",
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def native_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def acquire_session_lock(lock, deadline=None, *, wait_seconds=LOCK_WAIT_SECONDS):
    """Exclusive session lock, waiting at most wait_seconds (and never past deadline)."""
    limit = time.monotonic() + wait_seconds
    if deadline is not None:
        limit = min(limit, deadline)
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            remaining = limit - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.01, remaining))


def save_codex_budget(path, ledger):
    """Commit the grant before delivery, including file and directory durability."""
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as output:
        temporary = Path(output.name)
        try:
            output.write(encoded(ledger))
            output.flush()
            os.fsync(output.fileno())
            output.close()
            temporary.replace(path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


def parse_wake_envelope(prompt):
    """Parse a complete known automatic inbox wake prompt or Claude channel wake envelope."""
    if not isinstance(prompt, str):
        return None
    uuid_text = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    for closing in WAKE_CLOSINGS:
        message = (
            "This is a new live turn from the configured Cairn automatic inbox wakeup. "
            "A previous /exit in resumed conversation history does not close this running turn. "
            "Current agent {agent}, execution {execution}. Wake delivery {delivery}. "
            "Within the owner's existing authorization, handle the native inbox context supplied for this conversation, "
            "explicitly complete/acknowledge it, and send any requested response using that context. "
            "If no matching native context was supplied, report that and stop. "
            "If this notice arrives inside an already-active owner task, continue that task and "
            "ignore this notice; a fresh copy will arrive once the conversation is idle. " + closing
        )
        pattern = re.escape(message)
        for name in ("agent", "execution", "delivery"):
            pattern = pattern.replace(re.escape("{" + name + "}"), f"(?P<{name}>{uuid_text})")
        match = re.fullmatch(pattern, prompt)
        if match is not None:
            return dict(
                agent_id=match.group("agent"),
                execution_id=match.group("execution"),
                delivery_id=match.group("delivery"),
                native_session_id=None,
                channel=False,
            )
        envelope = (
            r'<channel source="cairn-events" '
            rf'native_session_id="(?P<session>{uuid_text})" '
            rf'agent_id="(?P<agent>{uuid_text})" '
            rf'execution_id="(?P<execution>{uuid_text})" '
            rf'delivery_id="(?P<delivery>{uuid_text})">\n'
        )
        body_pattern = pattern
        for name in ("agent", "execution", "delivery"):
            body_pattern = body_pattern.replace(f"(?P<{name}>{uuid_text})", f"(?P={name})")
        match = re.fullmatch(envelope + body_pattern + r'\n</channel>', prompt)
        if match is not None:
            return dict(
                agent_id=match.group("agent"),
                execution_id=match.group("execution"),
                delivery_id=match.group("delivery"),
                native_session_id=match.group("session"),
                channel=True,
            )
    return None


def taskless_notification(prompt):
    """Classify complete known wake text; this establishes no identity or authority."""
    return parse_wake_envelope(prompt) is not None


def claude_prompt_owner(event):
    prompt = event.get("prompt_id")
    if (not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 256
            or any(ord(c) < 32 for c in prompt)):
        return None
    return "claude-channel:" + prompt


def claude_owner(event):
    owner = claude_prompt_owner(event)
    if owner is None:
        raise ValueError('inbox recall requires actual Claude prompt ownership')
    return owner


def reserved(engine, event):
    return event.get('hook_event_name') == 'UserPromptSubmit' and engine.taskless_notification(event.get('prompt', ''))


def folded_token(kind, value):
    return hashlib.sha256('\0'.join((FOLD_DOMAIN, kind, value)).encode()).hexdigest()[:16]


def folded_conflict(ledger, *, key, wake_sha256, owner):
    """True when a folded grant matched this key, wake hash or (Claude) native owner."""
    tokens = set((ledger.get('folded') or {}).get('tokens', ()))
    if not tokens:
        return False
    probes = [folded_token('key', key)]
    if isinstance(wake_sha256, str):
        probes.append(folded_token('wake', wake_sha256))
    if isinstance(owner, str):
        probes.append(folded_token('owner', owner))
    return any(p in tokens for p in probes)


def compact_grants(
    ledger,
    *,
    owners=(),
    deliveries=(),
    attempts=(),
    turns=(),
    recent_grants=LEDGER_RECENT_GRANTS,
    folded_tokens=LEDGER_FOLDED_TOKENS,
    recent_decisions=LEDGER_RECENT_DECISIONS,
):
    """Fold grants outside the retained set into digests; returns how many were folded."""
    grants = ledger['grants']
    recent = set(list(grants)[-recent_grants:])
    retained, fresh = {}, []
    for key, grant in grants.items():
        identity = grant['identity']
        if (key in recent or key == ledger.get('active') or identity.get('recall_task_key')
                or (grant.get('required') or {}).get('pending')
                or identity.get('native_owner') in owners or identity.get('delivery_id') in deliveries
                or identity.get('attempt_id') in attempts):
            retained[key] = grant
            continue
        fresh.append(folded_token('key', key))
        if isinstance(grant.get('wake_sha256'), str):
            fresh.append(folded_token('wake', grant['wake_sha256']))
        if isinstance(identity.get('native_owner'), str):
            fresh.append(folded_token('owner', identity['native_owner']))
    folded = len(grants) - len(retained)
    if folded:
        prior = ledger.get('folded') or dict(count=0, tokens=[])
        seen = set(prior['tokens'])
        tokens = list(prior['tokens'])
        for token in fresh:
            if token not in seen:
                seen.add(token)
                tokens.append(token)
        ledger['grants'] = retained
        ledger['folded'] = dict(count=prior['count'] + folded, tokens=tokens[-folded_tokens:])
    decisions = ledger.get('inbox_activation')
    if isinstance(decisions, dict) and len(decisions) > recent_decisions:
        names = list(decisions)
        keep = set(names[-recent_decisions:]) | (set(turns) & set(names))
        ledger['inbox_activation'] = {name: decisions[name] for name in names if name in keep}
    return folded


def load_grants(path, session):
    path = Path(path)
    marker = path.with_suffix('.initialized')
    if marker.exists() and json.loads(marker.read_text()) != dict(schema=INBOX_INIT_SCHEMA, session=session):
        raise ValueError('inbox recall initialization marker corrupt')
    if marker.exists() and not path.exists():
        raise ValueError('inbox recall grant ledger missing')
    ledger = json.loads(path.read_text()) if path.exists() else dict(schema=INBOX_LEDGER_SCHEMA, session=session, grants={})
    if ledger.get('schema') != INBOX_LEDGER_SCHEMA or ledger.get('session') != session or not isinstance(ledger.get('grants'), dict):
        raise ValueError('inbox recall grant ledger corrupt')
    for key, grant in ledger['grants'].items():
        if (not re.fullmatch('[0-9a-f]{64}', key) or not isinstance(grant, dict)
                or not isinstance(grant.get('identity'), dict)
                or any(type(grant.get(k)) is not int or grant[k] < 0 for k in ('limit_bytes', 'output_bytes', 'native_allowance_bytes'))
                or grant['output_bytes'] + grant['native_allowance_bytes'] > grant['limit_bytes']):
            raise ValueError('inbox recall grant ledger corrupt')
    for grant in [ledger, *ledger['grants'].values()]:
        required = grant.get('required')
        if required is not None and (not isinstance(required, dict)
                or type(required.get('count')) is not int or required['count'] < 0
                or type(required.get('pending')) is not bool
                or not isinstance(required.get('selection_sha256'), str)
                or (required['selection_sha256'] != '' and not re.fullmatch('[0-9a-f]{64}', required['selection_sha256']))):
            raise ValueError('inbox required-context status corrupt')
        if type(grant.get('required_refresh_bytes', 0)) is not int or grant.get('required_refresh_bytes', 0) < 0:
            raise ValueError('inbox required refresh counter corrupt')
    if type(ledger.get('pending_bytes', 0)) is not int or not 0 <= ledger.get('pending_bytes', 0) <= 9500:
        raise ValueError('inbox recall pending charge corrupt')
    if ledger.get('active') is not None and ledger['active'] not in ledger['grants']:
        raise ValueError('inbox recall active grant missing')
    folded = ledger.get('folded')
    if folded is not None and (not isinstance(folded, dict) or set(folded) != {'count', 'tokens'}
            or type(folded['count']) is not int or folded['count'] < 0 or not isinstance(folded['tokens'], list)
            or any(not isinstance(t, str) or not re.fullmatch('[0-9a-f]{16}', t) for t in folded['tokens'])):
        raise ValueError('inbox recall folded grants corrupt')
    return ledger


def save_claude_ledger(path, ledger, *, save_fn=save_codex_budget):
    path = Path(path)
    marker = path.with_suffix(".initialized")
    if not marker.exists():
        save_fn(marker, dict(schema=INBOX_INIT_SCHEMA, session=ledger["session"]))
    save_fn(path, ledger)


def claude_grant_matches(grant, owner, task_key):
    identity = grant["identity"]
    return (
        identity.get("recall_task_key") == task_key if task_key else
        identity.get("native_owner") == owner and not identity.get("recall_task_key")
    )


def claude_required_grant(ledger, event, task_key=None):
    if event["hook_event_name"] == "UserPromptSubmit" and claude_prompt_owner(event):
        owner = claude_prompt_owner(event)
        for grant in ledger["grants"].values():
            if claude_grant_matches(grant, owner, task_key):
                return grant
        if ledger.get("active") is None:
            return ledger
        active = ledger["grants"][ledger["active"]]
        return active if active.get("required", {}).get("pending") else None
    return ledger["grants"][ledger["active"]] if ledger.get("active") else ledger


def codex_budget_ledger(path, session, budget, *, error_cls=ValueError):
    """Load retained turn grants; never infer a refund from missing/corrupt state."""
    path = Path(path)
    marker = path.with_suffix(".initialized")
    if marker.exists():
        try:
            if json.loads(marker.read_text()) != dict(schema=CODEX_LEDGER_SCHEMA, session_id=session):
                raise ValueError("invalid initialization marker")
        except (ValueError, TypeError) as exc:
            raise error_cls("native-turn memory initialization marker is corrupt") from exc
    if not path.exists():
        if marker.exists():
            raise error_cls("native-turn memory ledger is missing")
        return dict(
            schema=CODEX_LEDGER_SCHEMA,
            session_id=session,
            active_turn_id=None,
            pending=dict(limit_bytes=budget, emitted_hook_bytes=0, reserved_native_bytes=0, granted=False),
            turns={},
        )
    try:
        ledger = json.loads(path.read_text())
        if (ledger["schema"] != CODEX_LEDGER_SCHEMA or ledger["session_id"] != session
                or not isinstance(ledger["turns"], dict)
                or any(not native_uuid(turn) for turn in ledger["turns"])
                or (ledger["active_turn_id"] is None and ledger["turns"])
                or (ledger["active_turn_id"] is not None and ledger["active_turn_id"] not in ledger["turns"])):
            raise ValueError("invalid ledger identity")
        for grant in [ledger["pending"], *ledger["turns"].values()]:
            if (not isinstance(grant, dict) or type(grant["granted"]) is not bool
                    or any(type(grant[key]) is not int for key in
                           ("limit_bytes", "emitted_hook_bytes", "reserved_native_bytes"))
                    or not 1000 <= grant["limit_bytes"] <= 65536
                    or min(grant["emitted_hook_bytes"], grant["reserved_native_bytes"]) < 0
                    or grant["emitted_hook_bytes"] + grant["reserved_native_bytes"] > grant["limit_bytes"]
                    or (not grant["granted"] and grant["reserved_native_bytes"] != 0)):
                raise ValueError("invalid grant accounting")
            required = grant.get("required", dict(count=0, selection_sha256="", pending=False))
            if (not isinstance(required, dict) or type(required.get("count")) is not int or required["count"] < 0
                    or type(required.get("pending")) is not bool
                    or not isinstance(required.get("selection_sha256"), str)
                    or (required["count"] > 0 and not re.fullmatch(r"[0-9a-f]{64}", required["selection_sha256"]))
                    or (required["count"] == 0 and required["selection_sha256"] != "")
                    or type(grant.get("required_refresh_bytes", 0)) is not int or grant.get("required_refresh_bytes", 0) < 0
                    or type(grant.get("checkpoint_refresh_bytes", 0)) is not int or grant.get("checkpoint_refresh_bytes", 0) < 0
                    or ("serialized_hook_bytes" in grant and (type(grant["serialized_hook_bytes"]) is not int
                        or grant["serialized_hook_bytes"] < 0))):
                raise ValueError("invalid required-context accounting")
        if (ledger["pending"]["granted"] or (ledger["active_turn_id"] is not None
                                            and ledger["pending"]["emitted_hook_bytes"] != 0)):
            raise ValueError("invalid startup accounting")
        return ledger
    except (ValueError, KeyError, TypeError) as exc:
        raise error_cls("native-turn memory ledger is corrupt; no allowance renewed") from exc


def save_codex_ledger(path, ledger, *, save_fn=save_codex_budget):
    path = Path(path)
    marker = path.with_suffix(".initialized")
    if not marker.exists():
        save_fn(marker, dict(schema=CODEX_LEDGER_SCHEMA, session_id=ledger["session_id"]))
    save_fn(path, ledger)


def codex_hook_cost(event, text):
    return (1 + len(encoded(dict(hookSpecificOutput=dict(
        hookEventName=event["hook_event_name"], additionalContext=text))).encode())) if text else 0


def codex_emitted_wire_bytes(grant):
    emitted = grant["emitted_hook_bytes"]
    if grant.get("serialized_hook_bytes") == emitted:
        return emitted
    framing = 1 + len(encoded(dict(hookSpecificOutput=dict(
        hookEventName="SessionStart", additionalContext=""))).encode())
    return 2 * emitted + framing if emitted else 0


def codex_required_selection(grant, selected):
    grant["required"] = dict(
        count=len(selected),
        selection_sha256=hashlib.sha256(encoded(selected).encode()).hexdigest() if selected else "",
        pending=False,
    )


def codex_required_grant(ledger, event):
    if event["hook_event_name"] == "UserPromptSubmit" and native_uuid(event.get("turn_id")):
        turn = event["turn_id"]
        if turn in ledger["turns"]:
            return ledger["turns"][turn]
        return ledger["pending"] if ledger["active_turn_id"] is None else None
    active = ledger["active_turn_id"]
    return ledger["turns"][active] if active is not None else ledger["pending"]


def recall_grant_kind(identity):
    """Fixed-vocabulary evidence class of one bridge/memory ledger grant identity."""
    boundary = identity.get('boundary')
    if identity.get('attempt_id') or identity.get('delivery_id') or boundary in ('claude_prompt_delivery', 'native_turn'):
        return 'inbox_delivery'
    if boundary in ('ordinary_prompt', 'launcher_task'):
        return 'ordinary_context'
    if boundary == 'unadmitted_wake':
        return 'unadmitted_wake'
    return 'unknown'


def summarize_grant_kinds(kinds):
    kinds = set(kinds)
    if not kinds:
        return None
    return kinds.pop() if len(kinds) == 1 else 'mixed'


def recall_grant_evidence(config, state, attempt=None, native_owner=None, *, require_files=True, coord=None):
    """Read the bound bridge's own ledger for grants naming an attempt, its delivery or a native prompt."""
    owner = native_owner if native_owner is not None else (attempt or {}).get('native_turn_id')
    try:
        load_bridge = getattr(coord, 'load_inbox_recall', None)
        if load_bridge is not None:
            bridge = load_bridge(config, validate=False)
            _, memory = bridge.binding(config)
            load_grants_fn = bridge.load_grants
        else:
            load_grants_fn = load_grants
            memory = config
        directory = Path(memory['state_dir'])
        native = state['agent']['native_session_id']
        if not require_files and directory.is_absolute() and not os.path.lexists(directory):
            return 'absent', None
        info = directory.stat()
        if not directory.is_absolute() or info.st_uid != os.getuid() or info.st_mode & 0o022:
            return 'unknown', 'unknown'
        with (directory / (native + '.lock')).open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 'busy', 'unknown'
            ledger_path = directory / (native + '.inbox-recall.json')
            for required in (ledger_path, ledger_path.with_suffix('.initialized')):
                try:
                    info = os.lstat(required)
                except FileNotFoundError:
                    if require_files:
                        return 'unknown', 'unknown'
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    return 'unknown', 'unknown'
            ledger = load_grants_fn(ledger_path, native)
            kinds = set()
            for grant in ledger['grants'].values():
                identity = grant['identity']
                if attempt and (identity.get('attempt_id') == attempt['attempt_id']
                                or identity.get('delivery_id') == attempt['delivery']['delivery_id']):
                    kinds.add('inbox_delivery')
                elif owner and identity.get('native_owner') == owner:
                    kinds.add(recall_grant_kind(identity))
            return ('present', summarize_grant_kinds(kinds)) if kinds else ('absent', None)
    except Exception:
        return 'unknown', 'unknown'


def codex_pre_grant_ledger_absent(config, state, attempt, recorded, *, coord=None):
    """Explicit-recovery proof that a recorded required-lookup refusal left no grant."""
    turn = attempt.get('native_turn_id')
    if (config.get('harness') != 'codex' or recorded.get('recall_phase') != 'required_search'
            or recorded.get('grant') != 'not_checked' or not isinstance(turn, str)):
        return False
    try:
        load_bridge = getattr(coord, 'load_inbox_recall', None)
        if load_bridge is not None:
            bridge = load_bridge(config, validate=False)
            _, memory = bridge.binding(config)
        else:
            memory = config
        directory = Path(memory['state_dir'])
        native = state['agent']['native_session_id']
        info = directory.stat()
        if not directory.is_absolute() or info.st_uid != os.getuid() or info.st_mode & 0o022:
            return False
        with (directory / (native + '.lock')).open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            ledger_path = directory / (native + '.inbox-recall.json')
            if any(os.path.lexists(p) for p in (ledger_path, ledger_path.with_suffix('.initialized'))):
                return False
            budget_path = directory / (native + '.memory-budget.json')
            info = os.lstat(budget_path)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                return False
            budget = json.loads(budget_path.read_text())
            return bool(
                isinstance(budget, dict) and budget.get('schema') == CODEX_LEDGER_SCHEMA
                and budget.get('session_id') == native
                and isinstance(budget.get('inbox_activation'), dict)
                and budget['inbox_activation'].get(turn) is True
                and isinstance(budget.get('turns'), dict) and turn not in budget['turns']
            )
    except Exception:
        return False


def recall_grant_state(config, state, attempt, *, coord=None):
    """What the bound bridge's own ledger says about this claimed attempt."""
    evidence_fn = getattr(coord, 'recall_grant_evidence', None)
    if evidence_fn is not None:
        status, _ = evidence_fn(config, state, attempt)
    else:
        status, _ = recall_grant_evidence(config, state, attempt, coord=coord)
    return 'unknown' if status == 'busy' else status


class GrantLedger:
    """Unified session grant ledger seam for inbox-recall and memory-budget ledgers."""

    def __init__(self, state_dir, session):
        self.directory = Path(state_dir)
        self.session = session
        self.lock_path = self.directory / (session + ".lock")
        self.inbox_path = self.directory / (session + ".inbox-recall.json")
        self.inbox_marker_path = self.inbox_path.with_suffix(".initialized")
        self.codex_path = self.directory / (session + ".memory-budget.json")
        self.codex_marker_path = self.codex_path.with_suffix(".initialized")

    @contextmanager
    def locked(self, deadline=None, *, wait_seconds=LOCK_WAIT_SECONDS):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock_path.open("a") as lock:
            acquire_session_lock(lock, deadline, wait_seconds=wait_seconds)
            yield lock

    def load_inbox(self):
        return load_grants(self.inbox_path, self.session)

    def save_inbox(self, ledger, *, save_fn=save_codex_budget):
        save_claude_ledger(self.inbox_path, ledger, save_fn=save_fn)

    def compact_inbox(self, ledger, *, owners=(), deliveries=(), attempts=(), turns=()):
        return compact_grants(ledger, owners=owners, deliveries=deliveries, attempts=attempts, turns=turns)

    def load_codex(self, budget, *, error_cls=ValueError):
        return codex_budget_ledger(self.codex_path, self.session, budget, error_cls=error_cls)

    def save_codex(self, ledger, *, save_fn=save_codex_budget):
        save_codex_ledger(self.codex_path, ledger, save_fn=save_fn)
