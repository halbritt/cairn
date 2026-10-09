"""Regression for direct success retention after a refused composer prefix.

Process exits are constructed declarations; effects are disposable local files.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from selected_native.retention import Intent,Retention,RetentionError,ExitSnapshot
from trial_coding_stream import TOTAL,LINE,FRAMES
from test_trial_coding_stream import composer
from test_trial_attempt_actions import SESSION,init,event,wire


def successful():
    source=composer()
    final=dict.fromkeys(source.profile.final_required_keys,None)
    source.feed(wire(init())+wire(event('result',subtype='success',is_error=False,
        result=json.dumps(final),permission_denials=[],num_turns=1,total_cost_usd=.1,
        modelUsage={'fixture-model':dict(provider='firstParty',inputTokens=1,outputTokens=2,
            cacheReadInputTokens=0,cacheCreationInputTokens=0)})),0)
    return source


class RetentionGuardTests(unittest.TestCase):
    def retained(self,source):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);self.addCleanup(os.close,fd)
        writer=Retention.create(fd,Intent(SESSION,'a'*64,'b'*64,'c'*64,'d'*64))
        self.addCleanup(writer.close)
        # Deliberately call the success writer before observations, as the
        # real successful worker must. No observed actor exit is claimed here.
        with self.assertRaisesRegex(RetentionError,'^reader_refused$'):
            writer.prepare(source.reader,ExitSnapshot(1,1,SESSION,0,False))
        self.assertFalse((root/SESSION/'SELECTED.json').exists())
        self.assertFalse((root/SESSION/'PREPARED.json').exists())
        self.assertEqual(writer.state,'failed')
        self.assertTrue(source.reader.closed)
        report=source.observations([])
        return report

    def test_oversize_or_nonbytes_after_final_cannot_prepare_successful_prefix(self):
        for chunk in (b'x'*65537,'not bytes'):
            with self.subTest(kind=type(chunk).__name__):
                source=successful();source.feed(chunk,1)
                self.assertIn('transport_chunk_unknown',source.unknown)
                report=self.retained(source)
                self.assertFalse(report['full_stream']['digest_complete'])
                self.assertFalse(report['success_reader']['whole_stream_consumed'])

    def test_total_frame_and_line_bounds_before_finish_all_poison_success(self):
        housekeeping=wire(event('rate_limit_event'))
        examples={
            'total':[housekeeping*(65536//len(housekeeping))]*(TOTAL//65536+2),
            'frames':[housekeeping]*(FRAMES+1),
            'line':[b' '*65536]*(LINE//65536+1),
        }
        for kind,chunks in examples.items():
            with self.subTest(kind=kind):
                source=successful()
                for chunk in chunks:source.feed(chunk,1)
                self.assertTrue(source.stopped)
                self.assertIn('transport_bounds',source.unknown)
                if kind=='total':self.assertGreater(source.total,TOTAL)
                report=self.retained(source)
                self.assertFalse(report['success_reader']['whole_stream_consumed'])

    def test_action_interrupt_after_final_poisons_before_reraising(self):
        source=successful()
        with patch.object(source.failure_actions,'feed',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):source.feed(b' ',1)
        self.assertIn('action_reader_interrupted',source.unknown)
        self.retained(source)

    def test_success_reader_interrupt_after_final_poisons_before_reraising(self):
        source=successful()
        with patch.object(source.reader,'feed',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):source.feed(wire(event('rate_limit_event')),1)
        self.assertIn('success_reader_interrupted',source.unknown)
        self.retained(source)

    def test_required_capture_timing_unknown_cannot_prepare_success(self):
        source=successful();source.feed(b' ',None)
        self.assertFalse(source.stopped)  # Other observation channels continue.
        self.assertIn('capture_timing_unknown',source.unknown)
        self.assertEqual(source.frame_failure,'capture_timing_unknown')
        self.retained(source)
