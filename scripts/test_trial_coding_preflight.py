"""Real compiler encodes and disposable source custody; no native execution."""
import contextlib
import hashlib
import json
import os
import sys
import unittest

import test_trial_sdk_bootstrap_review as fixtures
import trial_coding_launch as coding
import trial_native_bootstrap as bootstrap
from trial_sdk_attempt_resources import AttemptResources


def launch(prompt, instruction='i'):
    return coding.compile_launch(session_id='90000000-0000-4000-8000-000000000001',
        model='fixture-model', effort='high', prompt=prompt,
        memory_instruction=instruction)


class Backings:
    """Observe real encodes and simultaneous Python frame aliases, retaining sizes.

    This is not a physical-memory quote: interpreter temporaries/capacity and
    external caller-owned inputs are outside these observations.
    """
    def __init__(self):
        self.peak = 0
        self.encodes = []

    def observe(self, frame):
        raw = {}
        while frame is not None:
            if frame.f_code.co_filename == coding.__file__:
                for value in frame.f_locals.values():
                    if type(value) is bytes:
                        raw[id(value)] = len(value)
            frame = frame.f_back
        total = sum(raw.values())
        self.peak = max(self.peak, total)
        return total

    def trace(self, frame, event, arg):
        self.observe(frame)
        return self.trace

    def profile(self, frame, event, arg):
        if (frame.f_code.co_filename == coding.__file__ and event == 'c_call'
                and getattr(arg, '__name__', '') == 'encode'):
            # c_call is BEFORE the next real encode allocates its result. In
            # refusal cases there must be no previous encoded block still held.
            self.encodes.append((len(arg.__self__), self.observe(frame)))

    @contextlib.contextmanager
    def recording(self):
        previous_trace, previous_profile = sys.gettrace(), sys.getprofile()
        try:
            sys.settrace(self.trace)
            sys.setprofile(self.profile)
            yield self
        finally:
            sys.setprofile(previous_profile)
            sys.settrace(previous_trace)


def failure_raw(error):
    """Count actual compiler/helper buffers in the preserved exception chain."""
    seen, raw = set(), {}
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        tb = error.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code.co_filename == coding.__file__:
                for value in tb.tb_frame.f_locals.values():
                    if type(value) is bytes:
                        raw[id(value)] = len(value)
            tb = tb.tb_next
        error = error.__context__
    return sum(raw.values())


class CompilerPreflightTests(unittest.TestCase):
    def test_exact_legacy_bytes_and_context_not_canonical_serialization(self):
        prompts = ['plain', 'é水😀', '"\\\b\f\n\r\t\x00\x1f',
                   '\u2028\u2029 / </script>', 'a' * 2047 + '😀é' + 'b' * 2048]
        for prompt in prompts:
            with self.subTest(prompt=prompt[:20]):
                expected = (json.dumps(dict(type='user', message=dict(role='user',
                    content=prompt)), ensure_ascii=False) + '\n').encode('utf-8')
                result = launch(prompt, 'é😀')
                self.assertEqual(result.input_message, expected)
                self.assertEqual(result.hook_context_bytes, 9500 - 512 - len(expected) - 6)
                self.assertEqual(result.argv[-1], 'é😀')
        self.assertEqual(launch('x').input_message,
                         b'{"type": "user", "message": {"role": "user", "content": "x"}}\n')

    def test_original_context_byte_boundary(self):
        for prompt, instruction in [('a' * 7926, 'i'), ('p', 'i' * 7926),
                                    ('é' * 3963, 'i')]:
            with self.subTest(prompt_length=len(prompt), instruction_length=len(instruction)):
                self.assertEqual(launch(prompt, instruction).hook_context_bytes, 1000)
                with self.assertRaisesRegex(ValueError, '^coding_input_budget$'):
                    launch(prompt + 'a', instruction)

    def test_accepted_maximum_message_starts_after_counting_backing_ends(self):
        observation = Backings()
        with observation.recording():
            result = launch('a' * 7926)
        self.assertEqual(len(result.input_message), 7987)
        self.assertIn((7987, 0), observation.encodes)
        # On return only the admitted user wire and original permission bytes
        # remain in the compiler; input encodings/counting blocks are absent.
        self.assertEqual(observation.peak, len(result.input_message) + len(result.permission_json))

    def check_bounded_refusal(self, prompt, instruction, code):
        observation = Backings()
        with observation.recording():
            try:
                launch(prompt, instruction)
            except ValueError as error:
                self.assertEqual(str(error), code)
                self.assertEqual(failure_raw(error), 0)
            else:
                self.fail('expected refusal')
        self.assertTrue(observation.encodes)
        self.assertLessEqual(observation.peak, 8192)
        self.assertTrue(all(size <= 2048 and old == 0
                            for size, old in observation.encodes), observation.encodes[:4])
        return observation

    def test_direct_oversized_inputs_never_encode_whole_raw(self):
        for prompt, instruction in [('a' * 131073, 'i'), ('p', '😀' * 32769)]:
            with self.subTest(prompt_length=len(prompt)):
                self.check_bounded_refusal(prompt, instruction, 'coding_input_budget')

    def test_strict_unicode_precedes_budget_even_after_large_valid_prefix(self):
        for prompt, instruction in [('a' * 131073, 'i\ud800'),
                                    ('p', 'a' * 131073 + '\udfff'),
                                    ('a' * 131073 + '\ud800', 'i'),
                                    ('\ud800', 'i' * 131073),
                                    ('p', '😀' * 2048 + '\ud800')]:
            with self.subTest(prompt_length=len(prompt), instruction_length=len(instruction)):
                self.check_bounded_refusal(prompt, instruction, 'coding_input')

    def test_json_escape_expansion_refuses_before_whole_raw_encoding(self):
        observed = self.check_bounded_refusal('\0' * 9500, 'i', 'coding_input_budget')
        # More than the input alone was traversed: the real legacy JSON text
        # (57061 characters) was counted using bounded actual encoding calls.
        self.assertGreater(sum(size for size, _ in observed.encodes), 57061)


class PreparationPreflightTests(unittest.TestCase):
    setUp = fixtures.SDKReviewTests.setUp

    def test_real_preparation_refusal_ends_compiler_raw_and_preserves_custody(self):
        for role, text in [('prompt', '\0' * 9500), ('instruction', 'i' * 9500)]:
            path = self.source / (role + '.txt')
            body = text.encode('utf-8')
            path.chmod(0o600)
            path.write_bytes(body)
            path.chmod(0o400)
            self.document['sources'][role]['sha256'] = hashlib.sha256(body).hexdigest()
        self.document['binding']['source_set_sha256'] = bootstrap.canonical_sha(
            {role: ref['sha256'] for role, ref in self.document['sources'].items()})
        bank, observation = AttemptResources(), Backings()
        before = set(os.listdir('/proc/self/fd'))
        with observation.recording():
            try:
                bootstrap.prepare_sdk_review_with_resources(self.document, self.output,
                    bank, raw=65536, working=2**20)
            except bootstrap.PreparationRefusal as error:
                self.assertEqual(str(error), 'coding_input_budget')
                self.assertEqual(failure_raw(error), 0)
                self.assertEqual(error.cleanup_faults, ())
            else:
                self.fail('expected preparation refusal')
        self.assertTrue(observation.encodes)
        self.assertLessEqual(observation.peak, 8192)
        self.assertTrue(all(size <= 2048 and old == 0 for size, old in observation.encodes))
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        self.assertTrue(bank.snapshot()['unknown'])
        self.assertEqual(bank.snapshot()['raw_reserved'], 65536)
        self.assertEqual(bank.snapshot()['working_reserved'], 2**20)

    def test_interrupt_with_actual_counting_block_retains_original_reservation(self):
        # Interrupt immediately after the real UTF-8 encode, before counting or
        # dropping its backing. Cleanup does not claim every traceback alias ended.
        bank, primary = AttemptResources(), KeyboardInterrupt('synthetic count interruption')
        before, held = set(os.listdir('/proc/self/fd')), []
        def interrupt(frame, event, arg):
            if (event == 'line' and frame.f_code.co_filename == coding.__file__
                    and type(frame.f_locals.get('block')) is bytes):
                held.append(len(frame.f_locals['block']))
                raise primary
            return interrupt
        previous = sys.gettrace()
        try:
            sys.settrace(interrupt)
            try:
                bootstrap.prepare_sdk_review_with_resources(self.document, self.output,
                    bank, raw=65536, working=2**20)
            except KeyboardInterrupt as error:
                self.assertIs(error, primary)
                self.assertEqual(failure_raw(error), held[0])
            else:
                self.fail('expected actual counting interruption')
        finally:
            sys.settrace(previous)
        self.assertEqual(len(held), 1)
        self.assertGreater(held[0], 0)
        self.assertLessEqual(held[0], 8192)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        self.assertTrue(bank.snapshot()['unknown'])
        self.assertEqual(bank.snapshot()['raw_reserved'], 65536)


if __name__ == '__main__':
    unittest.main()
