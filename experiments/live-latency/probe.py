"""Read-only version API latency with five warmups and thirty samples."""
import argparse
import http.client
import json
from pathlib import Path
import socket
import time
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--socket',type=Path,default=Path.home()/'.local/share/cairn/api.sock')
parser.add_argument('--token-file',type=Path,default=Path.home()/'.local/share/cairn/hosted-agent.token')
args=parser.parse_args()

class UnixHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(15)
        self.sock.connect(str(args.socket))

token=args.token_file.read_text().strip()
samples=[]
build=None
for i in range(35):
    c=UnixHTTP('localhost',timeout=15)
    start=time.monotonic_ns()
    c.request('POST','/v1/version',body=b'{}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    r=c.getresponse()
    data=json.loads(r.read())
    elapsed=time.monotonic_ns()-start
    c.close()
    assert r.status==200 and data['ok'],(r.status,data)
    build=data['data']
    if i>=5:samples.append(elapsed/1e6)
    time.sleep(.05)
print(json.dumps(dict(host=socket.gethostname(),operation='version',warmup=5,samples_ms=samples,build=build)))
