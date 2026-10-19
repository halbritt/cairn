"""Canonical sink parity, late refusals and retained failure storage."""
import hashlib
import json
import random
import sys
import unittest
from unittest.mock import patch
import trial_sdk_incoming as incoming

class SinkTests(unittest.TestCase):
    def expected(self,value):
        raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        return len(raw),hashlib.sha256(raw).hexdigest()

    def test_nested_escaping_boundaries_match_independent_json_and_transport(self):
        for text in ('x','\x00','é','\U0001f680','"\\\n'):
            for n in (0,1,1364,1365,1366,8190,8192,131072):
                with self.subTest(text=repr(text),n=n):
                    value={'z':[None,True,False,-0.0,1e-300,2**1023], 'a':{text*n:[text*n]}}
                    self.assertEqual(incoming.canonical_size_sha256(value),self.expected(value))
                    h=hashlib.sha256();size=0
                    for block in incoming.canonical_chunks(value):h.update(block);size+=len(block)
                    self.assertEqual((size,h.hexdigest()),self.expected(value))

    def test_generated_nested_values_match_independent_json(self):
        rng=random.Random(419)
        def graph(depth):
            if depth==0:return rng.choice([None,False,True,0,-7,3.125,'\x00é\U0001f680"\\'])
            if rng.randrange(2):return [graph(depth-1) for _ in range(rng.randrange(5))]
            return {str(i)+'é':graph(depth-1) for i in range(rng.randrange(5))}
        for _ in range(100):
            value=graph(5)
            self.assertEqual(incoming.canonical_size_sha256(value),self.expected(value))

    def test_late_refusals_match_transport_and_return_no_digest(self):
        cases=[('\ud800','canonical_surrogate'),({1:0},'canonical_shape'),
               (object(),'canonical_shape'),(2**1024,'canonical_integer'),
               (float('nan'),'canonical_nonfinite'),(float('inf'),'canonical_nonfinite')]
        for invalid,code in cases:
            value={'a':'x'*65536,'z':[invalid]}
            with self.subTest(code=code):
                transport=incoming.canonical_chunks(value)
                self.assertGreater(len(next(transport)),0) # Validation really is late.
                with self.assertRaises(incoming.IncomingError) as old:list(transport)
                with self.assertRaises(incoming.IncomingError) as new:incoming.canonical_size_sha256(value)
                self.assertEqual((old.exception.code,new.exception.code),(code,code))

    def test_no_transport_encoder_and_prior_raw_ended_on_late_refusal(self):
        failure=None
        with patch.object(incoming,'canonical_chunks',side_effect=AssertionError('transport allocation')):
            self.assertEqual(incoming.canonical_size_sha256({'ok':'\x00'*10000}),self.expected({'ok':'\x00'*10000}))
            try:incoming.canonical_size_sha256(['\x00'*10000,'\ud800'])
            except incoming.IncomingError as error:failure=error
        self.assertIsNotNone(failure)
        frames=[];tb=failure.__traceback__
        while tb:
            if tb.tb_frame.f_code is incoming.canonical_size_sha256.__code__:frames.append(tb.tb_frame)
            tb=tb.tb_next
        self.assertEqual(len(frames),1)
        self.assertIsNone(frames[0].f_locals['encoded'])
        self.assertNotIn('buffer',frames[0].f_locals)

    def test_hash_failure_clears_sink_raw_but_keeps_primary_and_inner_alias(self):
        class BrokenHash:
            def update(self,data):raise KeyboardInterrupt('synthetic hash interrupt')
        primary=None
        with patch.object(incoming.hashlib,'sha256',return_value=BrokenHash()):
            try:incoming.canonical_size_sha256({'key':'value'})
            except KeyboardInterrupt as error:primary=error
        self.assertEqual(str(primary),'synthetic hash interrupt')
        tb=primary.__traceback__;sink=None;retained=None
        while tb:
            if tb.tb_frame.f_code is incoming.canonical_size_sha256.__code__:sink=tb.tb_frame
            if tb.tb_frame.f_code is BrokenHash.update.__code__:retained=tb.tb_frame.f_locals['data']
            tb=tb.tb_next
        self.assertIsNone(sink.f_locals['encoded'])
        self.assertIsInstance(retained,bytes) # Callee traceback still charged, not erased by caller finally.
        self.assertLessEqual(len(retained),8190)

    def test_max_depth_nodes_and_integer_boundaries(self):
        value=0
        for _ in range(incoming.MAX_DEPTH):value=[value]
        for valid in (value,[0]*(incoming.MAX_NODES-1),2**1024-1,-(2**1024-1)):
            self.assertEqual(incoming.canonical_size_sha256(valid),self.expected(valid))
        for invalid,code in (([value],'canonical_depth'),([0]*incoming.MAX_NODES,'canonical_graph_bounds')):
            with self.assertRaises(incoming.IncomingError) as error:incoming.canonical_size_sha256(invalid)
            self.assertEqual(error.exception.code,code)

if __name__=='__main__':unittest.main()
