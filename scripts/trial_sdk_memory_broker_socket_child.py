"""Fixed ordinary socket test child; never a memory/native/provider worker."""
import array
import hashlib
import json
import os
import select
import sys
import time

from trial_sdk_memory_broker_socket import RecordSocket, BrokerSocketError
from trial_sdk_peer import Peer

REQUEST=bytes.fromhex('43495043032100000000000000000001000000000000000100000002000000007b7d')
REPLY=bytes.fromhex('434950430343000000000000000000010000000000000001000000040000000000000000')


def main():
    mode,path=sys.argv[1:]
    if mode=='hold':
        sys.stdin.buffer.read(1)
        print('{"hold_released":true}')
        return
    Peer.protect()
    deadline=time.monotonic_ns()+4_000_000_000
    used=peak=0
    def reserve(n):
        nonlocal used,peak
        if used+n>131072: return False
        used+=n;peak=max(peak,used);return True
    def release(n):
        nonlocal used
        used-=n
    endpoint=RecordSocket()
    row={'mode':mode,'api_calls':None,'runtime_qualified':False}
    try:
        endpoint.connect_parent(path,deadline,reserve,release)
        if mode=='fork_sender':
            pid=os.fork()
            if pid==0:
                endpoint._socket.send(REQUEST)
                os._exit(0)
            actual,status=os.waitpid(pid,0)
            row['fork_wait']={'pid':actual,'raw_status':status}
            sys.stdin.buffer.read(1)
        elif mode=='rights':
            fd=os.open('/dev/null',os.O_RDONLY|os.O_CLOEXEC)
            try:
                endpoint._socket.sendmsg([REQUEST],[(1,1,array.array('i',[fd]))])
            finally: os.close(fd)
            sys.stdin.buffer.read(1)
        else:
            source=memoryview(REQUEST)
            try:
                while endpoint.send(source)<0:
                    select.select([], [endpoint.fileno()], [], .01)
            finally: source.release()
            backing=bytearray(544)
            view=memoryview(backing)
            try:
                while True:
                    count=endpoint.receive_into(view)
                    if count>=0: break
                    select.select([endpoint.fileno()],[],[],.01)
                row['reply_matches']=bytes(view[:count])==REPLY
                row['reply_sha256']=hashlib.sha256(view[:count]).hexdigest()
            finally: view.release()
            # Keep the actual peer live through the parent's post-send check.
            sys.stdin.buffer.read(1)
    except BrokerSocketError:
        row['transport_refused']=True
    finally:
        endpoint.close()
        row['endpoint']=endpoint.snapshot()
        row['auxiliary_used']=used
        row['auxiliary_peak']=peak
    print(json.dumps(row,sort_keys=True))


if __name__=='__main__': main()
