"""Pure selected stream reader. No process, socket, persistence or authentication."""
import copy
import hashlib
import json
import math

from .evidence import (ACCEPTED_POLICIES, CURRENT_READ_POLICIES, EvidenceError, KNOWN_POLICIES, Ledger, POLICY_2,
                      SEARCH, PULL, bounded_tree, utf8)


class FrameError(ValueError):
    pass


BENIGN_LABELS = frozenset({('system', 'thinking_tokens'), ('system', 'ui_invalidate'), ('rate_limit_event', None)})
HOOK_SUBTYPES = frozenset({'hook_started', 'hook_response'})
HOOK_EVENTS = frozenset({'SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure'})  # names scripts/trial_native_observation.py retains
# Original numeric budgets, copied here as constants (never constructor parameters). Sources:
# memory 9500 and native_searches<=2, pulls<=4 in scripts/trial_native_observation.measure; final 8192 in
# native-frames-v3; tool room 32000 in the pilot MCP argv. Stream caps below are reader memory bounds, not budgets.
PINNED_BUDGETS = {'memory_bytes': 9500, 'final_bytes': 8192, 'tool_room_bytes': 32000, 'native_searches': 2, 'pulls': 4}


class Profile:
    """Explicit, reviewable interface profile. The default (no profile) is the accepted
    memory-only reader. A profile only names what a reviewer pinned: the exact init tool
    set, which of those tools are non-memory actions, the accepted permission modes, the
    hook channel policy ('refuse' or 'project'), the final-answer contract and the stream
    caps. Original numeric budgets are module constants and cannot be widened here."""

    def __init__(self, name, init_tools, action_tools, permission_modes, hooks='refuse', inspection_policies=ACCEPTED_POLICIES,
                 final_kind='json_object', final_max_bytes=PINNED_BUDGETS['final_bytes'], final_required_keys=(),
                 max_total_bytes=64 * 1024 * 1024, max_line_bytes=8 * 1024 * 1024, max_frames=20000):
        need(isinstance(name, str) and 0 < len(name) <= 64, 'profile_name')
        need(hooks in ('refuse', 'project'), 'profile_hooks')
        need(isinstance(inspection_policies, (tuple, frozenset, set)) and inspection_policies
             and set(inspection_policies) <= KNOWN_POLICIES and '' in inspection_policies, 'profile_inspection_policies')
        # Both b5ed072 and main 554037b ship a lifecycle cue that directs task-requested full reads to /2
        # (integrations/lifecycle/memory.py:65). A memory-on (hook) profile that cannot read /2 would only fail
        # mid-run, so it is refused here, before launch.
        need(hooks != 'project' or POLICY_2 in inspection_policies, 'profile_current_read_contract')
        need(final_kind in ('json_object', 'text') and type(final_max_bytes) is int
             and 0 < final_max_bytes <= PINNED_BUDGETS['final_bytes'], 'profile_final')
        need(isinstance(final_required_keys, (tuple, list, set, frozenset)) and len(final_required_keys) <= 32
             and all(isinstance(k, str) and 0 < len(k) <= 64 for k in final_required_keys)
             and (final_kind == 'json_object' or not final_required_keys), 'profile_final_keys')
        for group in (init_tools, action_tools, permission_modes):
            need(isinstance(group, (set, frozenset)) and group and all(isinstance(v, str) and v for v in group),
                 'profile_sets')
        need({SEARCH, PULL} <= set(init_tools) and set(action_tools) <= set(init_tools)
             and not ({SEARCH, PULL} & set(action_tools)), 'profile_tools')
        need(all(type(v) is int and v > 0 for v in (max_total_bytes, max_line_bytes, max_frames))
             and max_line_bytes <= max_total_bytes <= 256 * 1024 * 1024 and max_frames <= 100000, 'profile_caps')
        self.name, self.init_tools, self.action_tools = name, frozenset(init_tools), frozenset(action_tools)
        self.permission_modes = frozenset(permission_modes)
        self.hooks, self.final_kind, self.final_max_bytes = hooks, final_kind, final_max_bytes
        self.final_required_keys = tuple(sorted(set(final_required_keys)))
        self.inspection_policies = tuple(sorted(inspection_policies))
        self.limits = (max_total_bytes, max_line_bytes, max_frames)
        self.document = dict(name=name, init_tools=sorted(init_tools), action_tools=sorted(action_tools),
                             permission_modes=sorted(permission_modes), hooks=hooks,
                             inspection_policies=sorted(inspection_policies),
                             final=dict(kind=final_kind, max_bytes=final_max_bytes, required_keys=sorted(set(final_required_keys))),
                             budgets=dict(PINNED_BUDGETS), stream_caps=dict(
                                 total_bytes=max_total_bytes, line_bytes=max_line_bytes, frames=max_frames))
        self.sha256 = hashlib.sha256(json.dumps(self.document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def need(ok, label):
    if not ok:
        raise FrameError(label)


def parse(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'duplicate_frame_key')
            out[key] = value
        return out

    def invalid_constant(_):
        raise FrameError('nonfinite_frame_number')

    error, value = None, None
    try:
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=invalid_constant)
        bounded_tree(value)
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        need(isinstance(value, dict), 'frame_not_object')
    except FrameError as exc:
        error = str(exc)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        error = 'invalid_frame_json'
    if error is not None:
        # Raise outside the handler: no old exception chain or raw parse locals.
        raw, value = b'', None
        raise FrameError(error)
    return value


class Frames:
    """One expected session; malformed input poisons the reader, never retries it.

    The configuration is caller testimony. It does not attest actual argv,
    endpoint, authentication, custody, loaded SDK or process identity. finish()
    receives an exit fact from its caller; it does not observe an actual child.
    """

    def __init__(self, session_id, model, cli_version, profile=None, actions=None, clock=None, hook_projector=None):
        need(all(isinstance(v, str) and 0 < len(v) <= 256
                 for v in [session_id, model, cli_version]), 'config_shape')
        need(profile is None or isinstance(profile, Profile), 'profile_type')
        # Non-memory actions need an explicit metadata sink (e.g. the main-line PublicActions);
        # without a profile no sink is accepted, so the memory-only reader cannot be widened by accident.
        need((profile is None) == (actions is None) and (clock is None or callable(clock)), 'profile_actions')
        need((profile is not None and profile.hooks == 'project') == (hook_projector is not None)
             and (hook_projector is None or callable(hook_projector)), 'profile_hook_projector')
        need(actions is None or callable(getattr(actions, 'observe_denials', None)), 'profile_actions')
        self.profile, self.actions, self.clock, self.hook_projector = profile, actions, clock, hook_projector
        self.stream_hash = hashlib.sha256()
        # A poisoned reader restores the sink to this construction-time state: a rejected stream never
        # leaves partial public action state behind. The sink must be deep-copyable and keep its state in __dict__.
        self.sink0 = copy.deepcopy(actions) if actions is not None else None
        self.outbox = []
        self.hooks = dict(started={}, finished={}, rows=[], failures=[], unknown=[], stdout_bytes=0) \
            if profile is not None and profile.hooks == 'project' else None
        self.limits = profile.limits if profile else (4 * 1024 * 1024, 1024 * 1024, 2048)
        self.kinds, self.pending, self.names = {}, set(), {}
        self.expected = {'session_id': session_id, 'model': model, 'cli_version': cli_version}
        self.buffer = bytearray()
        self.scanned = 0
        self.total_bytes = 0
        self.frames = 0
        self.ledger = Ledger(profile.inspection_policies if profile else ACCEPTED_POLICIES)
        self.init = None
        self.final = None
        self.labels = {}
        self.failure = None
        self.closed = False

    def feed(self, chunk):
        need(self.failure is None and not self.closed, 'reader_unavailable')
        error, interruption, raw, event, staged = None, None, None, None, None
        try:
            need(isinstance(chunk, bytes) and len(chunk) <= 65536, 'chunk_bounds')
            self.stream_hash.update(chunk)
            self.total_bytes += len(chunk)
            need(self.total_bytes <= self.limits[0], 'stream_bounds')
            self.buffer.extend(chunk)
            while True:
                pos = self.buffer.find(b'\n', self.scanned)
                if pos < 0:
                    self.scanned = len(self.buffer)
                    break
                raw = bytes(self.buffer[:pos])
                del self.buffer[:pos+1]
                self.scanned = 0
                need(0 < len(raw) <= self.limits[1], 'line_bounds')
                self.frames += 1
                need(self.frames <= self.limits[2], 'frame_count_bounds')
                event = parse(raw)
                # Only maps are copied. All installed values are immutable here;
                # Ledger constructs new values on every request/response/retry.
                staged = copy.copy(self)
                staged.ledger = Ledger(self.ledger.policies)
                for key in ['calls', 'responses', 'offers', 'receipt_limits']:
                    setattr(staged.ledger, key, getattr(self.ledger, key).copy())
                staged.labels = dict(self.labels)
                staged.kinds, staged.pending, staged.names = dict(self.kinds), set(self.pending), dict(self.names)
                staged.outbox = []
                if self.hooks is not None:
                    staged.hooks = dict(started=dict(self.hooks['started']), finished=dict(self.hooks['finished']),
                                        rows=list(self.hooks['rows']), failures=list(self.hooks['failures']),
                                        unknown=list(self.hooks['unknown']), stdout_bytes=self.hooks['stdout_bytes'])
                staged._event(event)
                self._commit_sink(staged.outbox)   # before any other state moves; all-or-nothing per frame
                self.ledger, self.init, self.final = staged.ledger, staged.init, staged.final
                self.labels, self.kinds, self.pending, self.names = staged.labels, staged.kinds, staged.pending, staged.names
                self.hooks = staged.hooks
            need(len(self.buffer) <= self.limits[1], 'line_bounds')
        except (FrameError, EvidenceError) as exc:
            error = str(exc)
        except Exception:
            error = 'internal_refusal'
        except BaseException as exc:
            error, interruption = 'observer_interrupted', exc
        if error is not None:
            self.failure = error
            self.buffer.clear()
            self.scanned = 0
            self._reset_sink()
            chunk, raw, event, staged = b'', None, None, None
            if interruption is not None:
                # Honor the original interrupt after poisoning; never log it.
                raise interruption
            raise FrameError(error)

    def _commit_sink(self, outbox):
        # Dry-run on a copy so a sink fault cannot leave a half-applied frame, then one dict update.
        if not outbox or self.actions is None:
            return
        trial = copy.deepcopy(self.actions)
        for item in outbox:
            if item[0] == 'denials':
                trial.observe_denials(item[1])      # existing reviewed reconciliation: a matched action becomes tool_error
            else:
                trial.consume(*item)
        self.actions.__dict__.update(trial.__dict__)

    def _reset_sink(self):
        if self.actions is not None and self.sink0 is not None:
            self.actions.__dict__.update(copy.deepcopy(self.sink0).__dict__)

    def _emit(self, block, event):
        if self.actions is not None:
            self.outbox.append((block, event, self._now()))

    def _hook(self, event):
        # Reviewed projection of the hook channel: metadata plus the existing source-delivery
        # extraction (scripts/trial_source_delivery.hook_delivery) of a successful hook's stdout.
        hooks, sub = self.hooks, event.get('subtype')
        ident, name = event.get('hook_id'), event.get('hook_event')
        if not (isinstance(ident, str) and 0 < len(ident) <= 256 and isinstance(name, str) and 0 < len(name) <= 64):
            hooks['unknown'].append('native_hook_identity_missing')
            return
        key = hashlib.sha256(ident.encode()).hexdigest()
        name = name if name in HOOK_EVENTS else 'unknown'
        if sub == 'hook_started':
            if key in hooks['started']:
                hooks['unknown'].append('duplicate_native_hook')
            hooks['started'][key] = name
            return
        if key in hooks['finished']:
            hooks['unknown'].append('duplicate_native_hook')
        text, outcome = event.get('stdout'), event.get('outcome')
        raw = utf8(text) if isinstance(text, str) else None
        code = event.get('exit_code')
        code = code if type(code) is int and -128 <= code <= 255 else None      # missing or invalid stays unobserved
        outcome = 'success' if outcome == 'success' else 'non_success' if isinstance(outcome, str) else 'missing'   # closed
        row = dict(event=name, hook_sha256=key, outcome=outcome, exit_code=code,
                   stdout_bytes=len(raw) if raw is not None else None,
                   stdout_sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None, received_seconds=self._now(),
                   delivery=None)
        hooks['finished'][key] = name
        if raw is not None:
            hooks['stdout_bytes'] += len(raw)
        if code is None:
            hooks['unknown'].append('native_hook_exit_unobserved')
        if outcome != 'success' or (code is not None and code != 0):
            hooks['failures'].append('native_hook_failed')
        elif outcome == 'success' and code == 0 and raw is not None:
            row['delivery'] = self.hook_projector(text)      # origin and bytes are claims about stdout, distinct from the exit
        if len(hooks['rows']) < 128:
            hooks['rows'].append(row)
        else:
            hooks['unknown'].append('hook_row_limit')

    def _denial_fact(self, denial):
        # Closed facts only: tool name from the pinned init set, whether it matches a known call, a digest of the id.
        if not isinstance(denial, dict):
            return {'tool': 'unknown', 'matched_call': False, 'tool_use_id_sha256': None}
        name, ident = denial.get('tool_name'), denial.get('tool_use_id')
        tool = name if isinstance(name, str) and name in self.profile.init_tools else 'unknown'
        return {'tool': tool, 'matched_call': isinstance(ident, str) and tool != 'unknown' and self.names.get(ident) == tool,
                'tool_use_id_sha256': hashlib.sha256(ident.encode()).hexdigest() if isinstance(ident, str) and ident else None}

    def stream_digest(self):
        """Digest and byte count of every chunk accepted by feed(), available in any state (also after a refusal)."""
        return {'stream_sha256': self.stream_hash.hexdigest(), 'stream_bytes': self.total_bytes,
                'session_id': self.expected['session_id']}

    def _now(self):
        value = self.clock() if self.clock else None
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None

    def _label(self, kind, subtype):
        # Labels only: never an event payload. Bounded distinct labels; total frames
        # are already bounded by feed().
        label = json.dumps([str(kind)[:64], str(subtype)[:64]])
        need(label in self.labels or len(self.labels) < 32, 'event_label_bounds')
        self.labels[label] = self.labels.get(label, 0) + 1

    def _event(self, event):
        kind = event.get('type')
        if kind not in ('system', 'assistant', 'user', 'result') or (
                kind == 'system' and event.get('subtype') != 'init'):
            # claude 2.1.295 emits housekeeping events in every observed stream
            # (system/thinking_tokens, system/ui_invalidate, rate_limit_event).
            # They are counted by label only. A session id, when present, must match.
            need(event.get('session_id', self.expected['session_id']) == self.expected['session_id'],
                 'session_mismatch')
            if kind == 'system' and event.get('subtype') in HOOK_SUBTYPES:
                # Hook output is a second memory channel: projected under an explicit profile policy,
                # refused otherwise; never swallowed as housekeeping.
                need(self.hooks is not None, 'hook_event_unexpected')
                self._hook(event)
                return
            self._label(kind, event.get('subtype'))
            return
        need(self.final is None, 'event_after_final')
        need(event.get('session_id') == self.expected['session_id'], 'session_mismatch')
        if kind == 'system':
            need(event.get('subtype') == 'init' and self.init is None, 'init_shape')
            modes = self.profile.permission_modes if self.profile else {'dontAsk'}
            need(event.get('model') == self.expected['model']
                 and event.get('claude_code_version') == self.expected['cli_version']
                 and event.get('permissionMode') in modes, 'init_identity')
            tools = event.get('tools')
            allowed = self.profile.init_tools if self.profile else {SEARCH, PULL}
            need(isinstance(tools, list) and all(isinstance(t, str) for t in tools)
                 and len(tools) == len(set(tools)) and set(tools) == set(allowed), 'tool_allowlist')
            servers = event.get('mcp_servers')
            need(isinstance(servers, list) and len(servers) == 1 and isinstance(servers[0], dict)
                 and set(servers[0]) <= {'name', 'status', 'source'}
                 and servers[0].get('name') == 'cairn' and servers[0].get('status') == 'connected'
                 and ('source' not in servers[0] or servers[0]['source'] == 'dynamic'), 'server_metadata')
            self.init = {'session_id': event['session_id'], 'model': event['model'],
                         'cli_version': event['claude_code_version'], 'tools': sorted(tools),
                         'servers': copy.deepcopy(servers), 'permission_mode': event['permissionMode'],
                         'profile': {'name': self.profile.name, 'sha256': self.profile.sha256} if self.profile else None,
                         'basis': 'reported native metadata; not endpoint authentication'}
            return
        need(self.init is not None, 'event_before_init')
        if kind in ['assistant', 'user']:
            message = event.get('message')
            need(isinstance(message, dict), 'message_shape')
            blocks = message.get('content')
            if isinstance(blocks, str):
                return  # Plain transcript text never enters selected output.
            need(isinstance(blocks, list) and len(blocks) <= 128, 'blocks_shape')
            for block in blocks:
                need(isinstance(block, dict), 'block_shape')
                tag = block.get('type')
                if tag in ['text', 'thinking', 'redacted_thinking']:
                    continue
                if tag == 'tool_use':
                    need(kind == 'assistant' and len(self.ledger.calls) < 128 and len(self.kinds) < 4096,
                         'tool_call_role_or_cap')
                    name, ident = block.get('name'), block.get('id')
                    if isinstance(name, str) and name in {SEARCH, PULL}:
                        self.ledger.request(ident, name, block.get('input'))
                        self.kinds[ident] = 'memory'
                        self.names[ident] = name
                        self._emit(block, event)   # same public timeline as the main-line harness: memory calls included
                    else:
                        need(self.profile is not None and isinstance(name, str) and name in self.profile.action_tools,
                             'tool_not_allowed')
                        need(isinstance(ident, str) and 0 < len(ident) <= 256 and ident not in self.kinds,
                             'call_identity')
                        self._emit(block, event)
                        self.kinds[ident] = 'action'
                        self.names[ident] = name
                        self.pending.add(ident)
                elif tag == 'tool_result':
                    need(kind == 'user', 'tool_result_role')
                    key = block.get('tool_use_id')
                    need(isinstance(key, str), 'result_call_id')
                    if self.kinds.get(key) == 'action':
                        need(key in self.pending, 'duplicate_action_result')
                        self._emit(block, event)
                        self.pending.discard(key)
                    else:
                        self.ledger.response(key, block.get('content'), block.get('is_error', False))
                        self._emit(block, event)
                else:
                    raise FrameError('unknown_content_block')
            return
        need(kind == 'result', 'unknown_event_type')  # unreachable for labelled unknown types
        need(event.get('subtype') == 'success' and event.get('is_error') is False, 'native_result_failed')
        need(set(self.ledger.calls) == set(self.ledger.responses) and not self.pending, 'unanswered_tool_call')
        text = event.get('result')
        limit = self.profile.final_max_bytes if self.profile else PINNED_BUDGETS['final_bytes']
        need(isinstance(text, str) and len(utf8(text)) <= limit, 'work_product_bounds')
        if self.profile is not None and self.profile.final_kind == 'text':
            data = utf8(text)
            need(len(data) > 0, 'work_product_empty')
            work = {'kind': 'text', 'utf8_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'text': text}
        else:
            work = parse(utf8(text))
            need(self.profile is None or set(self.profile.final_required_keys) <= set(work), 'work_product_keys')
        usage = event.get('modelUsage')
        model = self.expected['model']
        need(isinstance(usage, dict) and set(usage) == {model} and isinstance(usage[model], dict)
             and usage[model].get('provider') == 'firstParty', 'model_usage_identity')
        costs = {}
        for key in ['inputTokens', 'outputTokens', 'cacheReadInputTokens', 'cacheCreationInputTokens']:
            value = usage[model].get(key)
            need(type(value) is int and 0 <= value <= 1000000000, 'usage_counter')
            costs[key] = value
        cost = event.get('total_cost_usd')
        need(type(cost) in [int, float] and 0 <= cost <= 1000 and math.isfinite(cost), 'usage_cost')
        turns = event.get('num_turns')
        need(type(turns) is int and 0 <= turns <= 1000, 'usage_turns')
        denials = event.get('permission_denials')
        facts = None
        if self.profile is not None and isinstance(denials, list):
            facts = [self._denial_fact(d) for d in denials[:256]]
            if denials and self.actions is not None:
                self.outbox.append(('denials', copy.deepcopy(denials)))
        self.final = {'work_product': work, 'reported_model_usage': costs,
                      'reported_cost_usd': cost, 'reported_turns': turns,
                      'reported_permission_denials': len(denials) if isinstance(denials, list) else None,
                      'permission_denial_facts': facts,
                      'basis': 'native-reported counters; not free context room or task correctness'}

    def finish(self, exit_code):
        need(self.failure is None and not self.closed, 'reader_unavailable')
        error, result = None, None
        self.closed = True  # Even a copy failure cannot reopen this reader.
        try:
            need(not self.buffer and self.final is not None and type(exit_code) is int
                 and exit_code == 0, 'incomplete_stream_or_exit')
            result = copy.deepcopy({'schema': 'cairn.selected-native-evidence/1', 'init': self.init,
                          'evidence': self.ledger.selected(), 'final': self.final,
                          'observed_frame_count': self.frames, 'stream_bytes_not_retained': self.total_bytes,
                          'stream_sha256': self.stream_hash.hexdigest(),
                          'event_labels': dict(self.labels),
                          'unreviewed_event_labels': sorted(l for l in self.labels
                                                            if tuple(json.loads(l)) not in {(a, str(b)) for a, b in BENIGN_LABELS}),
                          'event_labels_basis': 'type/subtype label counts only; no payload; benign set = labels seen in the two consumed pilot arms',
                          'actions': self.actions.finish() if self.actions else None,
                          'hooks': self._hooks_report(),
                          'budget_check': self._budget_check(),
                          'exit_basis': 'caller supplied; no child identity or exit observation',
                          'limits': 'pure framing only; no durable retention, SDK charges, task oracle, custody or endpoint proof'})
        except (FrameError, EvidenceError) as exc:
            error = str(exc)
        except Exception:
            error = 'internal_refusal'
        if error is not None:
            self.failure = error
            self.buffer.clear()
            self._reset_sink()
            exit_code, result = None, None
            raise FrameError(error)
        return result

    def _hooks_report(self):
        if self.hooks is None:
            return None
        hooks = self.hooks
        unknown = list(hooks['unknown'])
        if set(hooks['started']) != set(hooks['finished']):
            unknown.append('unfinished_native_hooks')
        if any(hooks['started'][k] != hooks['finished'][k] for k in set(hooks['started']) & set(hooks['finished'])):
            unknown.append('native_hook_event_mismatch')
        return dict(policy='project', status='unknown' if unknown else 'observed', unknown=sorted(set(unknown)),
                    failures=len(hooks['failures']), rows=list(hooks['rows']), stdout_bytes_total=hooks['stdout_bytes'],
                    basis='hook events as reported; hook_delivery projection of successful stdout only; '
                          'no offered-handle linkage and no server charge for hook bytes')

    def _budget_check(self):
        if self.profile is None:
            return None
        calls = list(self.ledger.calls.values())
        searches = [c for c in calls if c['name'] == SEARCH]
        pulls = [c for c in calls if c['name'] == PULL]
        violations = []
        if len(searches) > PINNED_BUDGETS['native_searches'] or len(pulls) > PINNED_BUDGETS['pulls']:
            violations.append('memory_call_limit')
        for call in searches:
            budget = call['arguments'].get('memory_budget_bytes')
            if type(budget) is not int:
                violations.append('search_without_memory_budget')
            elif budget > PINNED_BUDGETS['memory_bytes']:
                violations.append('search_budget_above_original')
            if call['arguments'].get('inspection_policy') and (type(budget) is not int or 'min_pull_bytes' in call['arguments']):
                violations.append('inspection_policy_request_invalid')   # the server refuses these; the call is still evidence
        return dict(pinned=dict(PINNED_BUDGETS), observed=dict(searches=len(searches), pulls=len(pulls)),
                    violations=sorted(set(violations)),
                    basis='request-argument and count check only; server balances remain the charge authority')
