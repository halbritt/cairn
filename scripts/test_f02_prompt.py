"""Bound Codex prompt recall through the real checked launcher and private API."""
import json

import test_f02_start as start_fixture


class F02PromptTests(start_fixture.F02StartTests):
    """Retain the landed start corpus while adding actual prompt consumers."""

    def source(self,prefix=(),*,controlled_ids=False):
        return super().source(prefix,controlled_ids=controlled_ids,bridge=True)

    def prompt(self, **changes):
        self.f.event.update(hook_event_name='UserPromptSubmit',
                            turn_id='33333333-3333-4333-8333-333333333333',
                            prompt='Inspect the current bounded author task.')
        self.f.event.update(changes)
        self.f.raw = start_fixture.encoded(self.f.event)

    def test_owner_prompt_receives_one_native_grant(self):
        self.prompt()
        result = self.pair()
        context = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(context['hookEventName'],'UserPromptSubmit')
        self.assertIn('remaining_memory_bytes',context['additionalContext'])
        budget = self.state()[start_fixture.SESSION+'.memory-budget.json']
        grant = budget['turns'][self.f.event['turn_id']]
        self.assertTrue(grant['granted'])
        self.assertEqual(grant['emitted_hook_bytes'],len(result.stdout))
        self.assertGreater(grant['reserved_native_bytes'],0)

    def test_degraded_prompt_retains_the_native_grant(self):
        self.status = 'DEGRADED_NO_EMBEDDINGS'
        self.prompt()
        self.assertEqual(self.pair().returncode,0)

    def test_prompt_required_context_refuses_without_partial_delivery(self):
        self.prompt()
        self.refusal='OPEN_CONFLICT'
        result=self.pair()
        self.assertEqual(result.returncode,2)
        self.assertEqual(result.stdout,b'')
        self.assertEqual(result.stderr,b'Cairn lifecycle diagnostic: phase=handle error_class=RequiredContextRefused\nCairn: current required context could not be restored.\n')

    def test_prompt_optional_api_failure_is_nonblocking(self):
        self.prompt()
        self.refusal='API_UNAVAILABLE'
        self.assertEqual(self.pair().returncode,1)

    def test_prompt_restart_cannot_renew_native_allowance(self):
        self.prompt()
        self.pair()
        previous=self.state()[start_fixture.SESSION+'.memory-budget.json']
        self.calls.clear()
        self.assertEqual(self.pair().stdout,b'{}\n')
        current=self.state()[start_fixture.SESSION+'.memory-budget.json']
        self.assertEqual(current,previous)

    def test_next_prompt_receives_a_distinct_grant(self):
        self.prompt()
        self.pair()
        self.calls.clear()
        self.prompt(turn_id='44444444-4444-4444-8444-444444444444')
        self.pair()
        current=self.state()[start_fixture.SESSION+'.memory-budget.json']
        self.assertEqual(len(current['turns']),2)
        self.assertTrue(all(g['granted'] for g in current['turns'].values()))

    def test_reserved_wake_leaves_context_to_qualified_coordination(self):
        self.prompt(prompt=self.f.wake())
        self.assertEqual(self.pair().stdout,b'{}\n')
        self.assertFalse(self.calls)
        self.assertNotIn(start_fixture.SESSION+'.json',self.state())

    def test_coordination_opt_out_keeps_independent_required_check(self):
        self.prompt(prompt=self.f.wake())
        (self.f.root/'.cairn-no-coordination').touch()
        self.assertEqual(self.pair().stdout,b'{}\n')
        self.assertIn('index',[call[0] for call in self.calls])
        status=self.state()[start_fixture.SESSION+'.json']['last_recall']
        self.assertEqual(status['native_turn_input'],'taskless_prompt')
        self.assertEqual(status['optional_deferred'],'native_turn_unbound')
