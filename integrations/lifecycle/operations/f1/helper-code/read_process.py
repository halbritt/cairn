"""Cooperative bounded intake of one fixed read child. No retry or signal."""
import os
import selectors
import subprocess
import time
from parsers import Unknown, need, proc_stat

ENV = {'PATH':'/usr/bin:/bin', 'HOME':'/home/halbritt',
       'XDG_DATA_HOME':'/home/halbritt/.local/share',
       'XDG_RUNTIME_DIR':'/run/user/1000',
       'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/1000/bus',
       'LC_ALL':'C', 'PYTHONDONTWRITEBYTECODE':'1'}


def child_start(pid):
    try:
        with open('/proc/'+str(pid)+'/stat','rb') as f:
            return proc_stat(f.read(8193),pid)['start_ticks']
    except (OSError,Unknown):
        return None


class ReadProcess:
    def __init__(self, allowed, clock=time.monotonic, launch=subprocess.Popen,
                 selector=selectors.DefaultSelector, start=child_start):
        self.allowed = frozenset(tuple(x) for x in allowed)
        self.clock, self.launch, self.selector, self.start = clock,launch,selector,start
        self.receipts=[]

    def run(self, kind, argv, body, deadline, cap=65536):
        need(tuple(argv) in self.allowed, 'COMMAND_NOT_SELECTED')
        need(type(body) is bytes and len(body)<=1024, 'INPUT_BOUND')
        begin=self.clock()
        need(begin<deadline, 'READ_BUDGET_EXHAUSTED')
        end=min(deadline,begin+2.0)
        receipt={'class':kind,'pid':None,'start_ticks':None,'begin_mono':begin,
                 'end_mono':None,'outcome':'LAUNCH_UNKNOWN','returncode':None,
                 'stdout_bytes':0,'stderr_bytes':0,'eof':False}
        self.receipts.append(receipt)
        proc=None; sel=None; raw=bytearray(); err_bytes=0
        try:
            proc=self.launch(argv,stdin=subprocess.PIPE if body else subprocess.DEVNULL,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             env=dict(ENV),cwd='/',close_fds=True)
            receipt['pid']=proc.pid;receipt['start_ticks']=self.start(proc.pid)
            sel=self.selector()
            for stream,tag in ((proc.stdout,'out'),(proc.stderr,'err')):
                os.set_blocking(stream.fileno(),False);sel.register(stream,selectors.EVENT_READ,tag)
            remaining=memoryview(body)
            if body:
                os.set_blocking(proc.stdin.fileno(),False)
                sel.register(proc.stdin,selectors.EVENT_WRITE,'in')
            while True:
                now=self.clock()
                if now>end:
                    receipt['outcome']='TIMEOUT_UNKNOWN';raise Unknown('READ_TIMEOUT')
                code=proc.poll()  # Nonblocking observation of this same child only.
                receipt['returncode']=code
                if code is not None and not sel.get_map():
                    receipt['eof']=True
                    receipt['outcome']='COMPLETE_ZERO' if code==0 else 'COMPLETE_NONZERO'
                    need(code==0,'READ_NONZERO')
                    return bytes(raw)
                for key,_ in sel.select(max(0,min(0.02,end-now))):
                    stream,tag=key.fileobj,key.data
                    if tag=='in':
                        try: count=os.write(stream.fileno(),remaining)
                        except BlockingIOError:continue
                        remaining=remaining[count:]
                        if not remaining:sel.unregister(stream);stream.close()
                        continue
                    try:chunk=os.read(stream.fileno(),min(8192,cap+1))
                    except BlockingIOError:continue
                    if not chunk:sel.unregister(stream);stream.close();continue
                    if tag=='out':raw.extend(chunk);receipt['stdout_bytes']=len(raw)
                    else:err_bytes+=len(chunk);receipt['stderr_bytes']=err_bytes
                    if len(raw)>cap or err_bytes>cap:
                        receipt['outcome']='OVERFLOW_UNKNOWN';raise Unknown('READ_OVERFLOW')
        except Unknown:
            raise
        except (OSError,ValueError):
            receipt['outcome']='INTAKE_ERROR_UNKNOWN'
            raise Unknown('READ_INTAKE_ERROR') from None
        finally:
            receipt['end_mono']=self.clock()
            # Closing intake is not cancellation or proof of terminal completion;
            # a remaining producer may see EPIPE. No kill, wait, retry or detach.
            if sel is not None:sel.close()
            if proc is not None:
                for stream in (proc.stdin,proc.stdout,proc.stderr):
                    if stream is not None and not stream.closed:stream.close()
