"""Fixed ordinary-child fixture. No Cairn/SDK/provider calls or credentials."""
import os
import sys
import time


def send(fd,block,count):
    with memoryview(block) as view:
        for _ in range(count):
            offset=0
            while offset<len(view):offset+=os.write(fd,view[offset:])


def main():
    mode=sys.argv[1]
    if mode=='reject':
        os.close(0)
        os.write(1,b'{"status":"BUDGET_REFUSED"}')
        os.write(2,b'err\xff\xf0')
        # The parent waits for stdout readiness BEFORE the first stdin offer.
        # This finite hold makes a known EPIPE observable without a hidden RPC.
        time.sleep(2)
        return 17
    if mode not in ('exact','overflow'):return 2
    slab=bytearray(512)
    count=0
    with memoryview(slab) as view:
        while True:
            size=os.readv(0,[view])
            if not size:break
            if any(byte!=66 for byte in view[:size]):return 3
            count+=size
            if count>2*1024*1024:return 4
    if count!=2*1024*1024:return 5
    send(1,b'a'*512,2048)
    send(2,b'\xff'*512,2048)
    if mode=='overflow':os.write(2,b'!')
    return 17


if __name__=='__main__':raise SystemExit(main())
