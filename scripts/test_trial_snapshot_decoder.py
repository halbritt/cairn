"""Real owned snapshots, synthetic bytes and read-time mutation/interruption."""
import codecs
import dataclasses
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import trial_bootstrap_snapshot as snapshots

class SnapshotDecoderTests(unittest.TestCase):
    def snapshot(self,data):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        owner=snapshots.Snapshot(str(Path(tmp.name)/'snapshot'))
        self.addCleanup(owner.close)
        owner.generated('text',data)
        return owner,owner.files['text'][0].fd

    def decode_width(self,owner,fd,width,action=None):
        read=os.read;counts=[]
        def bounded(target,n):
            value=read(target,min(n,width) if target==fd else n)
            if target==fd:
                counts.append(len(value))
                if action:action(target,len(counts),value)
            return value
        with patch.object(snapshots.os,'read',bounded):result=owner.read_text('text')
        return result,counts

    def test_every_multibyte_split_and_boundary(self):
        text='aé中\U0001f680z'*100
        owner,fd=self.snapshot(text.encode())
        for width in range(1,17):
            actual,counts=self.decode_width(owner,fd,width)
            self.assertEqual(actual,text);self.assertEqual(counts[-1],0)
        for char in ('é','中','\U0001f680'):
            raw=char.encode()
            for split in range(1,len(raw)):
                text='a'*(8192-split)+char+'tail'
                other,ofd=self.snapshot(text.encode())
                self.assertEqual(other.read_text('text'),text)

    def test_malformed_authenticated_input_reads_to_eof_before_generic_unicode_error(self):
        cases=[b'\xff',b'\xc0\xaf',b'\xed\xa0\x80',b'\xf4\x90\x80\x80',b'\xe2x',b'\x80']
        for malformed in cases:
            data=b'prefix'+malformed+b'x'*100
            owner,fd=self.snapshot(data);read=os.read;seen=[]
            def bounded(target,n):
                value=read(target,min(n,2));seen.append((target,len(value)));return value
            with patch.object(snapshots.os,'read',bounded):
                with self.assertRaisesRegex(UnicodeError,'snapshot_utf8'):owner.read_text('text')
            self.assertEqual(seen[-1],(fd,0))
            self.assertEqual(sum(n for target,n in seen if target==fd),len(data))

    def test_truncated_sequences_keep_original_final_decode_error(self):
        for raw in (b'\xc2',b'\xe2',b'\xe2\x82',b'\xf0',b'\xf0\x9f',b'\xf0\x9f\x9a'):
            owner,fd=self.snapshot(b'valid'+raw)
            with self.assertRaises(UnicodeDecodeError) as expected:
                decoder=codecs.getincrementaldecoder('utf-8')();decoder.decode(b'valid'+raw);decoder.decode(b'',True)
            for width in (1,2,3,8192):
                with self.assertRaises(UnicodeDecodeError) as actual:self.decode_width(owner,fd,width)
                self.assertEqual((actual.exception.object,actual.exception.start,actual.exception.end,actual.exception.reason),
                    (expected.exception.object,expected.exception.start,expected.exception.end,expected.exception.reason))

    def test_invalid_utf8_does_not_mask_digest_or_identity_mismatch(self):
        owner,fd=self.snapshot(b'\xff'+b'x'*20)
        row=owner.files['text'];owner.files['text']=(row[0],row[1],'0'*64,row[3])
        with self.assertRaisesRegex(Exception,'snapshot_changed'):owner.read_text('text')
        owner.files['text']=row
        changed=[]
        def mutate(target,n,value):
            if n==1:os.fchmod(target,0o600);changed.append(True)
        with self.assertRaisesRegex(Exception,'snapshot_changed'):self.decode_width(owner,fd,2,mutate)
        self.assertTrue(changed)

    def test_invalid_utf8_does_not_mask_concurrent_byte_change(self):
        owner,fd=self.snapshot(b'\xff'+b'x'*30)
        path=Path(owner.directory and '/proc/self/fd')/str(fd)
        def mutate(target,n,value):
            if n==1:
                os.fchmod(target,0o600)
                with open(path,'r+b',buffering=0) as output:output.seek(10);output.write(b'y')
        with self.assertRaisesRegex(Exception,'snapshot_changed'):self.decode_width(owner,fd,2,mutate)

    def test_interrupt_retains_callee_borrow_and_real_cleanup_reachable(self):
        owner,fd=self.snapshot(b'hello')
        original=snapshots._utf8_parts;primary=None
        def interrupted(block,carry):raise KeyboardInterrupt('decode interrupted')
        with patch.object(snapshots,'_utf8_parts',interrupted):
            try:owner.read_text('text')
            except KeyboardInterrupt as error:primary=error
        self.assertIsNotNone(primary)
        tb=primary.__traceback__;retained=None
        while tb:
            if tb.tb_frame.f_code is interrupted.__code__:retained=tb.tb_frame.f_locals['block']
            tb=tb.tb_next
        self.assertEqual(retained,b'hello') # Clearing owner fields cannot erase an external traceback.
        self.assertEqual(owner.close(),())
        self.assertTrue(all(lease.fd is None for lease in owner.tree.leases))

    def test_decode_borrows_large_tail_and_only_copies_small_carry(self):
        original=codecs.utf_8_decode;observed=[]
        def decode(data,*args):observed.append((type(data),len(data)));return original(data,*args)
        with patch.object(snapshots.codecs,'utf_8_decode',decode):
            parts,carry=snapshots._utf8_parts(b'\x82\xac'+b'x'*8190,b'\xe2')
        self.assertEqual(''.join(parts),'€'+'x'*8190);self.assertEqual(carry,b'')
        self.assertTrue(any(kind is memoryview and n>8000 for kind,n in observed))
        self.assertLessEqual(max(n for kind,n in observed if kind is bytes),6)

if __name__=='__main__':unittest.main()
