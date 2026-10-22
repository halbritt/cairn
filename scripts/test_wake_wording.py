"""Owner decision 2026-10-21: the wake prompt forbids only registering again, claiming another
session's inbox, or launching a replacement conversation. Every recognizer accepts both the
current and the immediately preceding wording, so wakes already queued, and pinned engines
not yet re-pinned, keep matching across the rollout."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


coordination = load('wording_coordination', 'integrations/lifecycle/coordination.py')
memory = load('wording_memory', 'integrations/lifecycle/memory.py')

AGENT, EXEC, DELIVERY = ('11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222',
                         '33333333-3333-4333-8333-333333333333')
NATIVE = 'caed9473-b01a-41e7-95ce-c3c1f28d66b3'
LEGACY_CLOSING = 'Do not register, manually claim an inbox, or launch a replacement conversation.'


def wake(**extra):
    return dict(dict(session=dict(agent_id=AGENT, execution_id=EXEC), delivery_id=DELIVERY), **extra)


def legacy_text():
    current = coordination.wake_message(wake())
    head = current[:current.rindex('Do not register')]
    return head + LEGACY_CLOSING


def channel(body):
    return (f'<channel source="cairn-events" native_session_id="{NATIVE}" agent_id="{AGENT}" '
            f'execution_id="{EXEC}" delivery_id="{DELIVERY}">\n' + body + '\n</channel>')


class WakeWording(unittest.TestCase):
    def test_current_wording_permits_self_drain(self):
        text = coordination.wake_message(wake())
        self.assertNotIn('manually claim an inbox', text)
        self.assertTrue(text.endswith('Do not register again, claim another session\'s inbox, '
                                      'or launch a replacement conversation.'))
        self.assertTrue(legacy_text().endswith(LEGACY_CLOSING))
        self.assertNotEqual(text, legacy_text())

    def test_memory_engine_classifies_both_wordings(self):
        for body in (coordination.wake_message(wake()), legacy_text()):
            self.assertTrue(memory.taskless_notification(body))
            self.assertTrue(memory.taskless_notification(channel(body)))
            self.assertFalse(memory.taskless_notification(body + ' owner text'))
        mixed = coordination.wake_message(wake()).replace(AGENT, EXEC, 1)
        self.assertFalse(memory.taskless_notification(channel(mixed)))

    def test_coordination_recognizers_accept_both_wordings(self):
        for body in (coordination.wake_message(wake()), legacy_text()):
            with self.subTest(legacy=body == legacy_text()):
                agent = dict(agent_id=AGENT, execution_id=EXEC)
                event = dict(hook_event_name='UserPromptSubmit', prompt=body)
                self.assertTrue(coordination.inbox_recall_wake(event))
                self.assertTrue(coordination.inbox_recall_wake(dict(event, prompt=channel(body))))
                codex = dict(agent=agent, idle_wake=wake(transport='codex-queue', session=agent))
                self.assertEqual(coordination.queued_wake_binding(codex, dict(event, turn_id='t1')),
                                 dict(delivery_id=DELIVERY, native_turn_id='t1'))
                hermes = dict(agent=agent, idle_wake=wake(transport='hermes-queue', session=agent))
                self.assertEqual(coordination.hermes_wake_binding(
                    hermes, dict(event, hook_event_name='TurnStart', turn_id='t2')),
                    dict(delivery_id=DELIVERY, native_turn_id='t2'))
                claude = dict(agent=agent, idle_wake=wake(transport='claude-channel', session=agent))
                self.assertEqual(coordination.channel_wake_binding(
                    claude, dict(event, prompt=channel(body), prompt_id='p1')),
                    dict(delivery_id=DELIVERY, native_turn_id='claude-channel:p1'))
                # A wake joining an active prompt is refused by either wording.
                joined = dict(claude, active_prompt=dict(id='claude-channel:p1'), seen_prompts=['claude-channel:p1'])
                admission = coordination.claude_prompt_admission(
                    joined, dict(event='UserPromptSubmit', native_turn_id='claude-channel:p1'),
                    dict(event, prompt=channel(body)))
                self.assertEqual(admission['refused_delivery'], DELIVERY)

    def test_unknown_wording_is_not_recognized(self):
        other = coordination.wake_message(wake()).replace('Do not register again', 'Please register again')
        event = dict(hook_event_name='UserPromptSubmit', prompt=other)
        self.assertFalse(coordination.inbox_recall_wake(event))
        self.assertFalse(memory.taskless_notification(other))
        agent = dict(agent_id=AGENT, execution_id=EXEC)
        codex = dict(agent=agent, idle_wake=wake(transport='codex-queue', session=agent))
        self.assertEqual(coordination.queued_wake_binding(codex, dict(event, turn_id='t1')), {})


if __name__ == '__main__':
    unittest.main()
