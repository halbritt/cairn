"""Signed observations and organic handoffs through native lifecycle interfaces."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("organic_native_session", HERE / "native_session.py")
ns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ns)
AGENT = dict(agent_id=str(uuid.UUID(int=1)), execution_id=str(uuid.UUID(int=2)))
SESSION = dict(AGENT)
CONFIG = dict(binding="test", harness="codex")
ORIGIN = dict(version=1, source="owner_composer", admission="fresh_start", input_id=str(uuid.UUID(int=3)))
ENVELOPE = dict(schema="cairn.owner-input-attestation/1", payload="e30", signature="A" * 86)


def attempt(ident="first", index=1):
    return dict(attempt_id=ident, session=SESSION, native_turn_id="turn-one",
                admission_kind="organic", organic_root="root-one", batch_root="first", batch_index=index,
                batch_limit=3, turn_exclusive=False, delivery=dict(delivery_id="delivery-"+ident,
                lease_id="lease-"+ident, event=dict(kind="notice", event_id="event-"+ident)))


class OrganicTransitions(unittest.TestCase):
    def session(self, record, host):
        return ns.NativeSession(CONFIG, Path("/unused"), record, host)

    def test_signed_observation_lost_reply_recovers_without_overwriting_existing_hold(self):
        old = dict(request_id="first", session=SESSION, native_turn_id="turn-one")
        record = dict(agent=AGENT, inbox_intent=dict(old), inbox_attempt=attempt(),
                      inbox_completion="keep-completion", inbox_result_policy=dict(claim_request_id="first", version=1))
        host = ns.MemoryHost({"session-organic-notice-claim": ns.MemoryHost.Refusal("API_UNAVAILABLE")})
        with self.assertRaises(ns.MemoryHost.Refusal):
            self.session(record, host).admit_organic_notice("turn-one", ORIGIN, ENVELOPE)
        sent = host.calls[0]
        self.assertEqual(record["inbox_intent"], old)
        self.assertEqual(record["inbox_completion"], "keep-completion")
        host.responses["session-organic-notice-claim"] = dict(root_id="root-one", decision="none", attempt=None)
        self.session(record, host).recover()
        self.assertEqual(host.calls[-1], sent)
        self.assertEqual(record["inbox_intent"], old)
        self.assertEqual(record["inbox_attempt"]["attempt_id"], "first")
        self.assertNotIn("organic_claim_intent", record)

    def test_advance_lost_reply_reuses_identity_and_replaces_item_journal_state(self):
        record = dict(agent=AGENT, inbox_intent=dict(request_id="first", session=SESSION,
            native_turn_id="turn-one"), inbox_attempt=attempt(), organic_root="root-one",
            inbox_completion="old-completion", inbox_response="old-response", inbox_close={"old": True},
            inbox_ack_reminded=True)
        host = ns.MemoryHost({"session-organic-notice-advance": ns.MemoryHost.Refusal("API_UNAVAILABLE")})
        with self.assertRaises(ns.MemoryHost.Refusal):
            self.session(record, host).advance_organic_notice()
        operation, sent = host.calls[0]
        host.responses[operation] = dict(attempt=attempt(sent["request_id"], 2), remainder="")
        self.session(record, host).recover()
        self.assertEqual(host.calls[-1], (operation, sent))
        self.assertEqual(record["inbox_intent"], dict(request_id=sent["request_id"], session=SESSION,
                                                   native_turn_id="turn-one"))
        for key in ("organic_advance_intent", "inbox_completion", "inbox_response", "inbox_close", "inbox_ack_reminded"):
            self.assertNotIn(key, record)

    def test_committed_claim_lost_reply_recovers_exact_attempt_and_refusal_drops_only_observation(self):
        record = dict(agent=AGENT)
        host = ns.MemoryHost({"session-organic-notice-claim": ns.MemoryHost.Refusal("API_UNAVAILABLE")})
        with self.assertRaises(ns.MemoryHost.Refusal):
            self.session(record, host).admit_organic_notice("turn-one", ORIGIN, ENVELOPE)
        sent = host.calls[0]
        selected = attempt(sent[1]['request_id'])
        host.responses['session-organic-notice-claim'] = dict(root_id='root-one', decision='admitted', attempt=selected)
        self.assertEqual(self.session(record, host).recover(), selected)
        self.assertEqual(host.calls[-1], sent)
        self.assertEqual(record['inbox_intent']['request_id'], selected['attempt_id'])
        host.responses['session-organic-notice-claim'] = ns.MemoryHost.Refusal('AUTHORITY_DENIED')
        with self.assertRaises(ns.MemoryHost.Refusal):
            self.session(record, host).admit_organic_notice('turn-one', ORIGIN, ENVELOPE)
        self.assertNotIn('organic_claim_intent', record)
        self.assertEqual(record['inbox_attempt'], selected)

    def test_organic_advance_rejects_wrong_root_and_keeps_unknown_results_pending(self):
        record = dict(agent=AGENT, inbox_attempt=attempt(), organic_root='root-one')
        host = ns.MemoryHost({'session-organic-notice-advance': lambda req:
            dict(attempt=dict(attempt(req['request_id'], 2), organic_root='other-root'), remainder='')})
        with self.assertRaises(ns.MemoryHost.Refusal) as caught:
            self.session(record, host).advance_organic_notice()
        self.assertEqual(caught.exception.code, 'INVALID_HOST')
        self.assertEqual(record['inbox_attempt']['attempt_id'], 'first')
        self.assertIn('organic_advance_intent', record)
        host.responses['session-organic-notice-advance'] = ns.MemoryHost.Refusal('STALE_SESSION')
        with self.assertRaises(ns.MemoryHost.Refusal):
            self.session(record, host).recover()
        self.assertIn('organic_advance_intent', record)
        record.pop('organic_advance_intent')
        with self.assertRaises(ns.MemoryHost.Refusal):
            self.session(record, host).advance_organic_notice()
        self.assertNotIn('organic_advance_intent',record)

    def test_retirement_resolves_unknown_claim_before_leaving_and_closes_exact_hold(self):
        record=dict(agent=AGENT)
        host=ns.MemoryHost({'session-organic-notice-claim':ns.MemoryHost.Refusal('API_UNAVAILABLE')})
        with self.assertRaises(ns.MemoryHost.Refusal):
            self.session(record,host).admit_organic_notice('turn-one',ORIGIN,ENVELOPE)
        sent=host.calls[0][1]
        with self.assertRaises(ns.MemoryHost.Refusal):
            self.session(record,host).retire()
        self.assertNotIn('retired',record)
        host.responses.update({'session-organic-notice-claim':dict(root_id='root-one',decision='admitted',
            attempt=attempt(sent['request_id'])),'agent-leave':{},'session-inbox-reconcile':{}})
        self.session(record,host).retire()
        self.assertEqual([call[0] for call in host.calls[-3:]],
                         ['session-organic-notice-claim','agent-leave','session-inbox-reconcile'])
        self.assertEqual(host.calls[-1][1]['attempt_id'],sent['request_id'])
        self.assertTrue(record['retired'])

    def test_restricted_cached_claim_uses_actual_control_hold_instead_of_request_identity(self):
        record=dict(agent=AGENT)
        host=ns.MemoryHost({'session-organic-notice-claim':ns.MemoryHost.Refusal('API_UNAVAILABLE')})
        with self.assertRaises(ns.MemoryHost.Refusal):
            self.session(record,host).admit_organic_notice('turn-one',ORIGIN,ENVELOPE)
        pending=record['organic_claim_intent']
        host.responses['session-organic-notice-claim']=ns.MemoryHost.Refusal('NOT_FOUND')
        with self.assertRaises(ns.MemoryHost.Refusal): self.session(record,host).recover()
        self.assertEqual(record['organic_claim_intent'],pending)
        host.responses.update({'session-inbox-control':dict(attempt=attempt('older-store-owned-attempt')),
                               'agent-leave':{},'session-inbox-reconcile':{}})
        self.session(record,host).retire()
        self.assertEqual(host.calls[-1][1]['attempt_id'],'older-store-owned-attempt')
        self.assertTrue(record['retired'])

    def test_retirement_control_absence_does_not_prove_an_uncertain_claim_closed(self):
        record=dict(agent=AGENT,organic_claim_intent=dict(request_id='unknown',session=SESSION,
            native_owner_turn_id='turn-one',owner_input_origin=ORIGIN,owner_input_attestation=ENVELOPE))
        host=ns.MemoryHost({'session-organic-notice-claim':ns.MemoryHost.Refusal('NOT_FOUND'),
                           'session-inbox-control':dict(attempt=None)})
        with self.assertRaises(ns.MemoryHost.Refusal): self.session(record,host).retire()
        self.assertIn('organic_claim_intent',record)
        self.assertNotIn('retired',record)
        self.assertNotIn('agent-leave',[call[0] for call in host.calls])

    def test_retirement_control_closes_exact_advance_successor_and_refuses_wrong_root_or_position(self):
        for defect in ('none','root','position'):
            with self.subTest(defect=defect):
                record=dict(agent=AGENT,inbox_attempt=attempt(),organic_root='root-one',
                    inbox_intent=dict(request_id='first',session=SESSION,native_turn_id='turn-one'),
                    inbox_completion='old-completion',inbox_close={'old':True},
                    organic_advance_intent=dict(request_id='uncertain-next',session=SESSION,
                        attempt_id='first',root_id='root-one',version=1))
                successor=attempt('actual-next',2)
                if defect=='root': successor['organic_root']='wrong'
                if defect=='position': successor['batch_index']=3
                host=ns.MemoryHost({'session-organic-notice-advance':ns.MemoryHost.Refusal('NOT_FOUND'),
                    'session-inbox-control':dict(attempt=successor),'agent-leave':{},'session-inbox-reconcile':{}})
                if defect!='none':
                    with self.assertRaises(ns.MemoryHost.Refusal): self.session(record,host).retire()
                    self.assertIn('organic_advance_intent',record)
                    self.assertEqual(record['inbox_completion'],'old-completion')
                    self.assertNotIn('retired',record)
                else:
                    self.session(record,host).retire()
                    self.assertEqual(host.calls[-1][1]['attempt_id'],'actual-next')
                    self.assertEqual(host.calls[-1][1]['reason'],'process_exited')
                    self.assertNotIn('inbox_completion',record)
                    self.assertNotIn('organic_advance_intent',record)
                    self.assertTrue(record['retired'])


class HookTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("organic_coordination", HERE / "coordination.py")
        cls.coordination = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.coordination)

    def test_signed_observation_reaches_store_before_private_queue_gate(self):
        c = self.coordination
        config = dict(CONFIG, native_delivery=True, idle_wakeup=True)
        state = dict(agent=dict(AGENT, native_session_id="native-one"), process={})
        event = dict(event="UserPromptSubmit", phase="busy", native_turn_id="turn-one",
                     owner_input_origin=ORIGIN, owner_input_attestation=ENVELOPE)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(c, "codex_queue_endpoint", return_value="/private/native.sock"), \
                patch.object(c, "call", return_value=dict(root_id="root-one", decision="none", attempt=None)) as call:
            self.assertEqual(c.inbox_context(config, state, Path(tmp)/"state.json", event), "")
        self.assertEqual(len(call.call_args_list), 1)
        self.assertEqual(call.call_args.args[1], "session-organic-notice-claim")
        self.assertEqual(call.call_args.args[2]["owner_input_attestation"], ENVELOPE)

    def test_unsigned_origin_keeps_ordinary_inbox_and_queue_behavior(self):
        c = self.coordination
        for queued in (True, False):
            with self.subTest(queued=queued), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(c, 'codex_queue_endpoint', return_value='/native.sock' if queued else None), \
                    patch.object(c, 'call', return_value=dict(attempt=None)) as call:
                config = dict(CONFIG, native_delivery=True, idle_wakeup=queued)
                state = dict(agent=dict(AGENT, native_session_id='native-one'), process={})
                observation = dict(event='UserPromptSubmit', phase='busy', native_turn_id='turn-one',
                                   owner_input_origin=ORIGIN)
                self.assertEqual(c.inbox_context(config, state, Path(tmp)/'state.json', observation), '')
                self.assertEqual([item.args[1] for item in call.call_args_list],
                                 [] if queued else ['session-inbox-claim'])

    def test_new_signed_turn_observes_store_and_preserves_ordinary_request_hold(self):
        c = self.coordination
        held=attempt()
        held.update(admission_kind='ordinary',turn_exclusive=True)
        held['delivery']['event']['kind']='request'
        held.pop('organic_root')
        intent=dict(request_id='first',session=SESSION,native_turn_id='turn-one')
        state=dict(agent=dict(AGENT,native_session_id='native-one'),process={},
                   inbox_intent=intent,inbox_attempt=held,inbox_completion='keep')
        observation=dict(event='UserPromptSubmit',phase='busy',native_turn_id='new-turn',
                         owner_input_origin=ORIGIN,owner_input_attestation=ENVELOPE)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(c,'call',return_value=dict(root_id='new-root',decision='none',attempt=None)) as call:
            with self.assertRaises(c.CoordinationError) as caught:
                c.inbox_context(dict(CONFIG,native_delivery=True),state,Path(tmp)/'state.json',observation)
        self.assertEqual(caught.exception.code,'NATIVE_TURN_MISMATCH')
        self.assertEqual(call.call_args.args[1],'session-organic-notice-claim')
        self.assertEqual(call.call_args.args[2]['native_owner_turn_id'],'new-turn')
        self.assertEqual(state['inbox_attempt'],held)
        self.assertEqual(state['inbox_intent'],intent)
        self.assertEqual(state['inbox_completion'],'keep')

    def test_watcher_and_stop_recover_unknown_observation_without_local_hold(self):
        c=self.coordination
        pending=dict(request_id='pending',session=SESSION,native_owner_turn_id='turn-one',
                     owner_input_origin=ORIGIN,owner_input_attestation=ENVELOPE)
        for mode in ('watch','stop'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(c,'call',return_value=dict(root_id='root-one',decision='none',attempt=None)) as call, \
                    patch.object(c,'flush_inbox_intents',return_value=True), \
                    patch.object(c,'codex_queue_endpoint',return_value='/native.sock'):
                state=dict(agent=dict(AGENT,native_session_id='native-one'),process={},organic_claim_intent=dict(pending))
                config=dict(CONFIG,native_delivery=True,idle_wakeup=True)
                if mode=='watch':
                    c.watch_inbox(config,state,Path(tmp)/'state.json')
                else:
                    c.inbox_context(config,state,Path(tmp)/'state.json',
                                    dict(event='Stop',phase='idle',native_turn_id='turn-one'))
                self.assertEqual(call.call_args.args[1],'session-organic-notice-claim')
                self.assertEqual(call.call_args.args[2],pending)
                self.assertNotIn('organic_claim_intent',state)

    def test_compact_advance_routes_organic_root_through_saved_handoff(self):
        c = self.coordination
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            (state_dir / "compact").mkdir(parents=True)
            (state_dir / "inbox").mkdir()
            (root / "bindings").mkdir()
            config = dict(CONFIG, state_dir=str(state_dir), notice_batch_version=1)
            held = attempt()
            held['delivery']['event'].update(ref=dict(record_id="record", version=1), **{'from': 'sender'})
            record = dict(agent=dict(AGENT, native_session_id="native-one"), process=dict(pid=1,start=1,boot="boot"),
                          inbox_attempt=held, inbox_intent=dict(request_id="first", session=SESSION,
                          native_turn_id="turn-one"), organic_root="root-one")
            state = state_dir / "native.json"
            context_path = state_dir / "inbox" / "first.json"
            handle, issued = '0123456789abcdef', time.time()
            context = dict(AGENT, attempt_id="first", compact_handle=handle, compact_issued_at=issued,
                delivery_id="delivery-first", lease_id="lease-first", event_id="event-first", kind="notice",
                sender="sender", source=held['delivery']['event']['ref'], native_turn_id="turn-one",
                batch_advance_request_id="next-request")
            entry = dict(schema="cairn.compact-inbox-handle/1", issued_at=issued, binding="test",
                         state=str(state), context=str(context_path), native_session_id="native-one")
            for path, body in ((state, record), (context_path, context), (root/'bindings/test.json', config),
                               (state_dir/'compact'/f'{handle}.json', entry)):
                c.write_state(path, body)
            with patch.dict(os.environ, CAIRN_INBOX_ROOT=str(root), CODEX_THREAD_ID="native-one"), patch.object(c, "load_config", return_value=config), \
                    patch.object(c, "process_alive", return_value=True), patch.object(c, "caller_descends_from", return_value=True), \
                    patch.object(c, "call", return_value=dict(attempt=None, remainder="batch_limit")) as call, \
                    patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(c.compact_inbox_command(['advance', handle]), 0)
                self.assertEqual(json.loads(output.getvalue())['remainder'], 'batch_limit')
            self.assertEqual(call.call_args.args[1], 'session-organic-notice-advance')
            self.assertEqual(call.call_args.args[2]['root_id'], 'root-one')




if __name__ == "__main__":
    unittest.main()
