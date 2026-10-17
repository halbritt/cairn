"""Fixed ordinary stdout pressure producer; no SDK/provider or arbitrary input."""
import errno
import json
import os
import signal

expected='{"type":"fixture","value":"雪"}\n'.encode('utf8')
message=b''
while len(message)<=33:
    part=os.read(0,34-len(message))
    if not part:break
    message+=part
if message!=expected or len(message)!=33:raise SystemExit(24)
if os.write(1,message)!=33:raise SystemExit(25)
os.set_blocking(1,False)
body=b'A'*512
view=memoryview(body)
accepted=0;blocked=False;number=None
while accepted<1024*1024:
    loan=view[:min(512,1024*1024-accepted)]
    try:count=os.write(1,loan)
    except BlockingIOError as error:
        if error.errno not in (errno.EAGAIN,errno.EWOULDBLOCK):raise
        blocked=True;number=error.errno;break
    finally:loan.release()
    if not 0<count<=min(512,1024*1024-accepted):raise SystemExit(21)
    accepted+=count
view.release()
metadata=(json.dumps({'accepted':accepted,'eagain':blocked,'errno':number},separators=(',',':'))+'\n').encode('ascii')
if len(metadata)>128 or os.write(2,metadata)!=len(metadata):raise SystemExit(22)
if not blocked:raise SystemExit(17)
signal.pause()
raise SystemExit(23)
