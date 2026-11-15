"""Driver boundaries and owned nonnative audit; no actual Agy or provider."""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import uuid
import unittest
from unittest.mock import patch

import check_r06_agy_native_unit as driver


class HostConnectAuditTests(unittest.TestCase):
    """Synthetic refusal corpus plus one real owned multigeneration client."""
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cairn-connect-',dir=Path.home()/'.cache')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.image=self.root/'unit-client';self.image.write_bytes(b'UNIT ONLY; never executed\n');self.image.chmod(0o500)
        self.capture=dict(Process=dict(PID=200,Start='1',Boot='UNIT'),Client=driver.checked_pin(self.image),ClientArgv=[str(self.image)])
        self.local=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.local.bind(str(self.root/'local.sock'));(self.root/'local.sock').chmod(0o600)
        self.addCleanup(self.local.close)

    def raw(self,body,pid=200,extra=''):
        path=json.dumps(str(self.image))
        return (f'200 execve({path}, [{path}], 0x0 /* UNIT */) = 0\n'+extra+
                f'{pid} {body}\n200 +++ exited with 0 +++\n').encode()

    def audit(self,raw,**kw):
        return driver.audit_host_connections(raw,self.capture,self.root,ports={4321},
            unix_paths=[self.root/'local.sock'],**kw)

    def test_dns_ports_are_not_in_the_explicit_fixture_allowlist(self):
        for host in ('127.0.0.1','127.0.0.53'):
            connect=f'connect(8, {{sa_family=AF_INET, sin_port=htons(53), sin_addr=inet_addr("{host}")}}, 16) = -1 EACCES (Permission denied)'
            with self.assertRaisesRegex(driver.Refused,'external/unselected'):self.audit(self.raw(connect))
    def test_exact_child_version_requires_zero_join_and_no_second_exec(self):
        path=json.dumps(str(self.image))
        for args,status,refuse in [('[%s, "--version"]'%path,'0',False),
                                  ('[%s, "--bg-updater"]'%path,'0',True),
                                  ('[%s, "--new-project"]'%path,'0',True),
                                  ('[%s, "--version"]'%path,'1',True)]:
            raw=self.raw(f'execve({path}, {args}, 0x0 /* UNIT */) = 0',pid=201,
                extra='200 fork() = 201\n').replace(b'200 +++',f'201 +++ exited with {status} +++\n200 +++'.encode())
            if refuse:
                with self.assertRaises(driver.Refused):self.audit(raw)
            else:self.audit(raw)

    def test_actual_kernel_tcp_udp_unix_and_descendant_inheritance(self):
        listener=socket.socket();self.addCleanup(listener.close)
        listener.bind(('127.0.0.1',0));listener.listen(8);port=listener.getsockname()[1]
        self.local.listen(8)
        image=self.root/'python';shutil.copyfile(sys.executable,image);image.chmod(0o500)
        program=r'''
import ctypes,errno,json,os,socket,sys
sys.stdin.buffer.read(1)
port=int(sys.argv[1]);local=sys.argv[2]
def probe():
 for family,address in [(socket.AF_INET,('198.51.100.1',443)),(socket.AF_INET,('127.0.0.1',53)),(socket.AF_INET6,('2001:db8::1',443))]:
  with socket.socket(family,socket.SOCK_STREAM) as s:
   try:s.connect(address)
   except OSError as e:assert e.errno==errno.EACCES,(address,e)
   else:raise AssertionError('nonallowed TCP connect escaped')
 for family in (socket.AF_INET,socket.AF_INET6):
  for kind in (socket.SOCK_DGRAM,socket.SOCK_DGRAM|socket.SOCK_CLOEXEC|socket.SOCK_NONBLOCK,socket.SOCK_RAW):
   try:s=socket.socket(family,kind)
   except OSError as e:assert e.errno==errno.EACCES,e
   else:s.close();raise AssertionError('inet non-stream socket escaped')
 for family in (socket.AF_INET,socket.AF_INET6):
  try:s=socket.socket(family,socket.SOCK_STREAM,132)
  except OSError as e:assert e.errno==errno.EACCES,e
  else:s.close();raise AssertionError('non-TCP stream protocol escaped')
 for family in (socket.AF_PACKET,socket.AF_NETLINK):
  try:s=socket.socket(family,socket.SOCK_RAW)
  except OSError as e:assert e.errno==errno.EACCES,e
  else:s.close();raise AssertionError('packet/netlink socket escaped')
 with socket.create_connection(('127.0.0.1',port),timeout=2):pass
 with socket.socket(socket.AF_UNIX) as s:s.connect(local)
 libc=ctypes.CDLL(None,use_errno=True)
 assert libc.syscall(425,1,0)==-1 and ctypes.get_errno()==errno.EACCES
 assert libc.syscall(0x40000000|39)==-1 and ctypes.get_errno()==errno.EACCES
probe()
child=os.fork()
if child==0:
 probe();grandchild=os.fork()
 if grandchild==0:probe();os._exit(0)
 assert os.waitpid(grandchild,0)[1]==0;os._exit(0)
assert os.waitpid(child,0)[1]==0
print(json.dumps(dict(generations=3,TCP_denied=True,inet_datagrams_denied=True,allowed_TCP=True,AF_UNIX=True,io_uring_denied=True)))
'''
        child=driver.OwnedKernelTrace([str(image),'-I','-S','-B','-c',program,str(port),str(self.root/'local.sock')],self.root,ports=(port,))
        try:stdout,_=child.process.communicate(b'x',timeout=15)
        finally:child.close()
        self.assertEqual(child.process.returncode,0,(self.root/'kernel.stderr').read_text())
        self.assertEqual(json.loads(stdout)['generations'],3)
        self.assertTrue(child.cleanup['all_absent']);self.assertEqual(child.cleanup['forced_signals'],[])
        self.assertEqual(child.policy['handled_access_net'],2);self.assertEqual(child.policy['handled_access_fs'],0)
        self.assertEqual(child.policy['uid'],os.getuid());self.assertIn('PORT',child.policy['scope'])
        # Kernel denial does not turn an external attempt into audit acceptance.
        with self.assertRaisesRegex(driver.Refused,'external/unselected'):
            driver.audit_host_connections(child.trace.read_bytes(),child.generation,self.root,
                ports={port},unix_paths=[self.root/'local.sock'])
        def audit(raw=None,capture=None,root=None,ports=None,enforcement=child):
            return driver.audit_host_connections(child.trace.read_bytes() if raw is None else raw,
                child.generation if capture is None else capture,self.root if root is None else root,
                ports={port} if ports is None else ports,unix_paths=[self.root/'local.sock'],enforcement=enforcement)
        report=audit()
        denied=[row for row in report['connections'] if row['kernel_denied']]
        self.assertTrue(report['kernel_enforced']);self.assertEqual(len(denied),9)
        self.assertTrue(all(row['result']=='-1 EACCES (Permission denied)' for row in denied))
        self.assertEqual(len([row for row in report['connections'] if not row['kernel_denied']]),6)
        export=os.environ.get('CAIRN_R06_KERNEL_EVIDENCE')
        if export:
            directory=driver.private_directory(export)
            for source,name in ((child.trace,'kernel.trace'),(self.root/'kernel-policy.json','kernel-policy.json'),
                                (self.root/'kernel.stderr','kernel.stderr')):
                shutil.copyfile(source,directory/name);(directory/name).chmod(0o400)
            driver.write_json(directory/'gate.json',dict(scope='NONNATIVE kernel fixture only; not native readiness',
                capture=child.generation,cleanup=child.cleanup,audit=report,stdout=json.loads(stdout),policy=child.policy,
                original_policy_pin=child._kernel_policy_pin,original_policy_identity=child._kernel_policy_identity,
                installer=child._kernel_installer,raw=driver.checked_pin(directory/'kernel.trace'),
                native_executions=0,provider_executions=0))
        for raw in (child.trace.read_bytes().replace(b'Permission denied',b'Operation denied'),
                    child.trace.read_bytes().replace(b'htons(443)',b'htons(444)')):
            with self.assertRaisesRegex(driver.Refused,'raw capture prefix'):audit(raw=raw)
        for proof in ({'kernel_enforced':True,'landlock_abi':8},True):
            with self.assertRaisesRegex(driver.Refused,'actual capture'):audit(enforcement=proof)
        wrong=copy.deepcopy(child.generation);wrong['Process']['Start']='stale'
        with self.assertRaisesRegex(driver.Refused,'actual capture'):audit(capture=wrong)
        other=self.root/'other';other.mkdir(mode=0o700)
        with self.assertRaises(driver.Refused):audit(root=other)
        with self.assertRaisesRegex(driver.Refused,'port set'):audit(ports={port+1 if port<65535 else port-1})
        receipt=self.root/'kernel-policy.json'
        receipt.chmod(0o600);policy=json.loads(receipt.read_bytes());policy['seccomp_mode']=0
        receipt.write_text(json.dumps(policy));receipt.chmod(0o400)
        with self.assertRaisesRegex(driver.Refused,'altered kernel policy'):audit()

    def test_ports_and_unverified_denial_results_stay_strict(self):
        body='connect(3, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("198.51.100.1")}, 16) = '
        for result in ('0','-1 EACCES (Permission denied)','-1 EINPROGRESS (Operation now in progress)',
                       '-1 ECONNREFUSED (Connection refused)','-1 ENETUNREACH (Network is unreachable)',
                       '-1 ETIMEDOUT (Connection timed out)','-1 EACCES (Operation denied)'):
            with self.subTest(result=result),self.assertRaises(driver.Refused):self.audit(self.raw(body+result))
        allowed='connect(3, {sa_family=AF_INET, sin_port=htons(4321), sin_addr=inet_addr("127.0.0.1")}, 16) = 0'
        for ports in ({53},{80},{443},{1024},{True},{65536},[4322,4321],[4321,4321]):
            with self.subTest(ports=ports),self.assertRaisesRegex(driver.Refused,'ports required'):
                driver.audit_host_connections(self.raw(allowed),self.capture,self.root,ports=ports,unix_paths=[])
        for malformed in (allowed.replace('16)','28)'),allowed.replace('127.0.0.1','not-an-IP'),
                          allowed.replace('4321','99999')):
            with self.assertRaises(ValueError):self.audit(self.raw(malformed))
        # Classification-only stub; actual installation/capture binding is
        # exercised separately by the real inherited-kernel fixture above.
        with patch.object(driver,'verified_kernel_policy',return_value=True):
            raw=self.raw(body.replace('443','4321')+'-1 EACCES (Permission denied)')
            with self.assertRaisesRegex(driver.Refused,'external/unselected'):self.audit(raw)

    def test_port_only_kernel_policy_does_not_admit_another_loopback_address(self):
        listener=socket.socket();self.addCleanup(listener.close)
        listener.bind(('127.0.0.1',0));listener.listen(1);port=listener.getsockname()[1]
        image=self.root/'python';shutil.copyfile(sys.executable,image);image.chmod(0o500)
        program='''import errno,json,socket,sys
sys.stdin.buffer.read(1)
with socket.socket() as s:
 try:s.connect(('127.0.0.2',int(sys.argv[1])));code=0
 except OSError as e:code=e.errno
print(json.dumps(code))
'''
        child=driver.OwnedKernelTrace([str(image),'-I','-S','-B','-c',program,str(port)],self.root,ports=(port,))
        try:stdout,_=child.process.communicate(b'x',timeout=10)
        finally:child.close()
        self.assertEqual(child.process.returncode,0)
        import errno
        self.assertNotEqual(json.loads(stdout),errno.EACCES)
        with self.assertRaisesRegex(driver.Refused,'external/unselected'):
            driver.audit_host_connections(child.trace.read_bytes(),child.generation,self.root,ports={port},
                unix_paths=[],enforcement=child)

    def test_literal_ipv4_ipv6_failed_local_and_owned_unix(self):
        forms=[
            'connect(3, {sa_family=AF_INET, sin_port=htons(4321), sin_addr=inet_addr("127.0.0.1")}, 16) = 0',
            'connect(3, {sa_family=AF_INET6, sin6_port=htons(4321), sin6_flowinfo=htonl(0), inet_pton(AF_INET6, "::1", &sin6_addr), sin6_scope_id=0}, 28) = -1 ECONNREFUSED (Connection refused)',
            'connect(3, {sa_family=AF_UNIX, sun_path='+json.dumps(str(self.root/'local.sock'))+'}, 110) = 0']
        for body in forms:
            with self.subTest(body=body):
                report=self.audit(self.raw(body));self.assertEqual(len(report['connections']),1)
                self.assertFalse(report['NativePASS']);self.assertIn('No kernel egress enforcement',report['ExternalCoverage'])

    def test_exact_missing_nscd_is_only_outside_root_exception(self):
        body='connect(3, {sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"}, 110) = -1 ENOENT (No such file or directory)'
        self.assertTrue(self.audit(self.raw(body))['connections'][0]['destination']['missing_nscd'])
        for changed in [body.replace('110','109'),body.replace('ENOENT (No such file or directory)','ECONNREFUSED (Connection refused)'),body.replace('= -1 ENOENT (No such file or directory)','= 0'),body.replace('/var/run/nscd/socket','/run/nscd/socket')]:
            with self.subTest(body=changed),self.assertRaises(driver.Refused):self.audit(self.raw(changed))

    def test_external_failed_wrong_unix_unknown_family_and_address_refuse(self):
        good='connect(3, {sa_family=AF_INET, sin_port=htons(4321), sin_addr=inet_addr("127.0.0.1")}, 16) = 0'
        forms=[good.replace('127.0.0.1','198.51.100.23').replace('= 0','= -1 ENETUNREACH (Network is unreachable)'),
            good.replace('127.0.0.1','127.0.0.2'),good.replace('4321','54'),good.replace('16)','4)'),
            'connect(3, {sa_family=AF_UNIX, sun_path="/outside-owned-root.sock"}, 110) = -1 ENOENT (No such file or directory)',
            'connect(3, {sa_family=AF_UNIX, sun_path=@"abstract"}, 10) = 0',
            'connect(3, {sa_family=AF_NETLINK, nl_pid=0, nl_groups=00000000}, 12) = 0',
            good.replace('"127.0.0.1"','"127.0"...'),good.replace('= 0','= ? ERESTARTSYS (To be restarted)'),
            'connect(3, 0x1234, 16) = -1 EFAULT (Bad address)']
        for body in forms:
            with self.subTest(body=body),self.assertRaises(driver.Refused):self.audit(self.raw(body))

    def test_unknown_pid_unfinished_connect_partial_and_malformed_records_refuse(self):
        good='connect(3, {sa_family=AF_INET, sin_port=htons(4321), sin_addr=inet_addr("127.0.0.1")}, 16) = 0'
        cases=[self.raw(good,pid=900),self.raw('connect(3, {sa_family=AF_INET <unfinished ...>'),
            self.raw(good)[:-1],self.raw('<... connect resumed>) = 0'),
            self.raw(good)+b'unattributed connect(0, NULL, 0) = 0\n',
            self.raw(good,extra='200 clone(child_stack=NULL, flags=SIGCHLD) = 201\n201 ???\n')]
        cases.append(self.raw(good,extra='200 read(0 <unfinished ...>\n'))
        for raw in cases:
            with self.subTest(raw=raw[-120:]),self.assertRaises(driver.Refused):self.audit(raw)

    def test_multihop_fork_attribution_and_missing_exit(self):
        body='connect(3, {sa_family=AF_INET, sin_port=htons(4321), sin_addr=inet_addr("127.0.0.1")}, 16) = 0'
        extra='200 fork() = 201\n201 vfork() = 202\n'
        raw=self.raw(body,pid=202,extra=extra).replace(b'200 +++',b'202 +++ exited with 0 +++\n201 +++ exited with 0 +++\n200 +++')
        report=self.audit(raw);self.assertEqual(report['traced_pids'],[200,201,202])
        with self.assertRaises(driver.Refused):self.audit(raw.replace(b'201 +++ exited with 0 +++\n',b''))

    def test_pending_stdin_is_labelled_without_granting_currentness(self):
        report=self.audit(self.raw('read(0 <unfinished ...>'))
        self.assertEqual(report['pending_nonconnect'][0]['name'],'read')
        self.assertIn('does not satisfy',report['currentness'])

    def test_connect_before_selected_exec_or_after_exit_refuses(self):
        connect='200 connect(3, {sa_family=AF_INET, sin_port=htons(4321), sin_addr=inet_addr("127.0.0.1")}, 16) = 0\n'
        baseline=self.raw(connect.removeprefix('200 ').rstrip())
        for raw in [connect.encode()+baseline,baseline+connect.encode(),baseline+b'200 +++ exited with 0 +++\n']:
            with self.subTest(raw=raw[-100:]),self.assertRaises(driver.Refused):self.audit(raw)

    def test_changed_generation_refuses_before_collection(self):
        child=object.__new__(driver.OwnedKernelTrace)
        child.pidfd,child.pid,child.generation=12345,200,self.capture
        child.image=self.capture['Client'];child.client_parent=None
        info=self.image.lstat()
        child.image_identity=(info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode),info.st_nlink,info.st_size)
        changed=copy.deepcopy(self.capture);changed['Process']['Start']='2'
        with patch.object(driver.collector,'capture_owned_generation',return_value=changed):
            with self.assertRaisesRegex(driver.Refused,'generation changed'):child.capture_generation()


    def test_fixture_rejects_uncontrolled_or_native_image_before_process(self):
        self.image.chmod(0o600)
        with patch.object(driver.subprocess,'Popen',side_effect=AssertionError('process before preflight')):
            with self.assertRaisesRegex(driver.Refused,'0500'):driver.OwnedKernelTrace([str(self.image)],self.root)
        self.image.chmod(0o500)
        with patch.object(driver,'AGY_SHA256',self.capture['Client']['SHA256']), \
                patch.object(driver.subprocess,'Popen',side_effect=AssertionError('native process')):
            with self.assertRaisesRegex(driver.Refused,'native image requires'):driver.OwnedKernelTrace([str(self.image)],self.root)

    def test_trace_launch_failure_closes_its_owned_stderr(self):
        with patch.object(driver.subprocess,'Popen',side_effect=OSError('fixture launch failure')):
            with self.assertRaisesRegex(OSError,'fixture launch failure'):
                driver.OwnedKernelTrace([str(self.image)],self.root)
        opened=[]
        for fd in Path('/proc/self/fd').iterdir():
            try:opened.append(os.readlink(fd))
            except FileNotFoundError:continue
        self.assertNotIn(str(self.root/'kernel.stderr'),opened)

    def test_actual_owned_root_child_and_grandchild_loopback_and_unix(self):
        selected=os.environ.get('CAIRN_R06_AGY_HOST_AUDIT_EVIDENCE')
        if selected:
            root=Path(selected);root.mkdir(mode=0o700)
        else:
            temporary=tempfile.TemporaryDirectory(prefix='cairn-net-',dir=Path.home()/'.cache')
            self.addCleanup(temporary.cleanup);root=Path(temporary.name)
        image=root/'owned-python';shutil.copyfile(Path(sys.executable).resolve(),image);image.chmod(0o500)
        inet=socket.socket();inet.bind(('127.0.0.1',0));inet.listen(4);inet.settimeout(10)
        local=socket.socket(socket.AF_UNIX);unix=root/'echo.sock';local.bind(str(unix));unix.chmod(0o600);local.listen(2);local.settimeout(10)
        port=inet.getsockname()[1];errors=[]
        def echo(listener,count):
            try:
                for _ in range(count):
                    with listener.accept()[0] as connection:
                        connection.settimeout(5);data=connection.recv(32)
                        if data!=b'owned-echo':raise AssertionError('wrong fixture payload')
                        connection.sendall(data)
            except BaseException as error:errors.append(repr(error))
        threads=[threading.Thread(target=echo,args=(inet,2)),threading.Thread(target=echo,args=(local,1))]
        for thread in threads:thread.start()
        program=('import os,socket,sys\n'
            'def request(family,address):\n'
            ' s=socket.socket(family);s.settimeout(5);s.connect(address);s.sendall(b"owned-echo");assert s.recv(32)==b"owned-echo";s.close()\n'
            f'request(socket.AF_INET,("127.0.0.1",{port}))\n'
            'child=os.fork()\n'
            'if child==0:\n'
            f' request(socket.AF_UNIX,{str(unix)!r})\n'
            ' grand=os.fork()\n'
            ' if grand==0:\n'
            f'  request(socket.AF_INET,("127.0.0.1",{port}));os._exit(0)\n'
            ' assert os.waitpid(grand,0)[1]==0;os._exit(0)\n'
            'assert os.waitpid(child,0)[1]==0\n'
            'print("AUDIT_READY",flush=True)\n'
            'sys.stdin.buffer.read()\n')
        child=None
        try:
            child=driver.OwnedKernelTrace([str(image),'-I','-S','-B','-c',program],root,ports=(port,))
            with driver.selectors.DefaultSelector() as selector:
                selector.register(child.process.stdout,driver.selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=15));self.assertEqual(child.process.stdout.readline(),b'AUDIT_READY\n')
            capture=driver.collector.capture_owned_generation(child.pid)
            child.close()
            raw=child.trace.read_bytes()
            report=driver.audit_host_connections(raw,capture,root,ports={port},unix_paths=[unix])
            self.assertEqual(len(report['connections']),3);self.assertEqual(len(report['traced_pids']),3)
            self.assertTrue(child.cleanup['all_absent']);self.assertEqual(child.cleanup['forced_signals'],[])
            for policy in [dict(ports={port+1 if port<65535 else port-1},unix_paths=[unix]),dict(ports={port},unix_paths=[])]:
                with self.assertRaises(driver.Refused):driver.audit_host_connections(raw,capture,root,**policy)
            driver.write_json(root/'audit-result.json',dict(audit=report,capture=capture,cleanup=child.cleanup,
                trace=driver.checked_pin(child.trace),scope='owned nonnative sockets; no model/provider/API/PG'))
        finally:
            if child is not None:child.close()
            for listener in (inet,local):listener.close()
            for thread in threads:thread.join(timeout=12)
            self.assertFalse(any(thread.is_alive() for thread in threads));self.assertEqual(errors,[])




class FullSequenceRehearsalTests(unittest.TestCase):
    """Explicit selected packet: genuine Go/API/PG, scripted client, no Agy."""
    def setUp(self):
        selected=os.environ.get('CAIRN_R06_REHEARSAL_SELECTION')
        if not selected:self.skipTest('requires explicitly selected sealed rehearsal packet')
        self.inputs=json.loads(Path(selected).read_text())
        self.evidence_base=Path(os.environ.get('CAIRN_R06_REHEARSAL_EVIDENCE',str(Path.home()/'.cache')))
        self.evidence_base.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.roots=[]

    def fresh(self):
        if self.inputs.get('shape')==driver.SCALE_SHAPE:
            number=len(list(self.evidence_base.glob('c[0-9][0-9]')))+1
            root=self.evidence_base/f'c{number:02}';root.mkdir(mode=0o700)
        else:root=Path(tempfile.mkdtemp(prefix='rh-',dir=self.evidence_base))
        self.roots.append(root)
        with (self.evidence_base/'cases.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(test=self._testMethodName,evidence=str(root),actual_Agy=0))+'\n')
        return root

    def tearDown(self):
        for root in self.roots:
            receipt=root/'case/rehearsal-result.json'
            if not receipt.exists():continue
            pg=json.loads((root/'case/pg-cleanup.json').read_text())
            native=json.loads((root/'case/native-cleanup.json').read_text())
            api=json.loads((root/'case/api-cleanup.json').read_text())
            self.assertTrue(pg['stopped'] and pg['all_absent'] and native['all_absent'] and api['absent'])
            self.assertFalse(native['forced_signals'])
            self.assertEqual(json.loads((root/'case/archive-result.json').read_text())['state'],'completed')
            result=json.loads(receipt.read_text())
            if not result['PG_runtime_removed']:
                runtime=Path(json.loads((root/'case/application-start.json').read_text())['runtime'])
                self.assertEqual(runtime.parent,Path.home()/'.cache');self.assertTrue(runtime.name.startswith('r06r-'))
                self.assertTrue(all(not Path('/proc',str(pid)).exists() for pid in pg['pids']))
                shutil.rmtree(runtime)
                (root/'stopped-runtime-cleanup.json').write_text(json.dumps(dict(removed=True,
                    scope='Expected negative rehearsal; original failed result/terminal remain unchanged'))+'\n')

    def test_complete_shared_sequence_has_real_public_reverse_restored_history_and_final_audit(self):
        root=self.fresh();result=driver.prepare_sequence_rehearsal(root,**self.inputs)
        self.assertTrue(result['RehearsalPASS']);self.assertFalse(result['NativePASS'])
        self.assertEqual(result['actual_Agy'],0);self.assertEqual(result['actual_native_attempts'],0)
        if self.inputs.get('shape')==driver.SCALE_SHAPE:
            self.assertEqual(result['fixture_shape'],driver.SCALE_SHAPE)
            self.assertEqual((result['bound_count'],result['unbound_count']),(8,3))
            self.assertEqual(len(result['scale_inputs']['profiles']),11)
        self.assertEqual(len(result['results']),3)
        self.assertTrue(result['audit']['full']['all_observed_descendants_exited'])
        for key in ('Before','After','RestoredA'):self.assertEqual(len(result[key]['Callbacks']),2)
        generation=json.loads(Path(result['generation']['Path']).read_text())
        self.assertEqual(generation['Before'],generation['After'])
        self.assertNotEqual(result['client']['pin']['SHA256'],driver.AGY_SHA256)
        for label in ('native-A-to-B','native-B-to-retained-Go3-A'):
            operation=json.loads((root/'case'/(label+'.exit.json')).read_text())
            self.assertEqual(operation['exit'],0);self.assertEqual(len(operation['interleaved']),1)
            self.assertEqual(operation['deadline_seconds'],30)
            self.assertGreater(operation['control_wall_seconds'],0)
            self.assertGreater(operation['remaining_seconds'],0)
            if self.inputs.get('shape')==driver.SCALE_SHAPE:
                plan=json.loads((root/'case'/(label+'.plan.json')).read_bytes())
                self.assertEqual(len(plan['current']['pins']),11);self.assertEqual(len(plan['next']),11)
            preservation=json.loads((root/'case'/(label+'.preservation.json')).read_text())
            self.assertEqual(preservation['database_before'],preservation['database_after'])
            self.assertTrue(any(preservation['after']['appends'][p]['bytes']>row['bytes']
                for p,row in preservation['before']['appends'].items()))
        state_path=driver.native_state_pins(root/'catalog/coordination-state',result['native_session'])[0]['Path']
        state=json.loads(Path(state_path).read_text())
        self.assertEqual(state['registration']['native_session_id'],result['native_session'])
        self.assertEqual(state['agent']['native_session_id'],result['native_session'])
        terminal=json.loads((root/'rehearsal-ledger.terminal.json').read_text())
        self.assertEqual(terminal['kind'],'PREPARATION_REHEARSAL');self.assertFalse(terminal['outcome']['NativePASS'])
        self.assertFalse(result['cleanup_errors']);self.assertTrue(result['PG_runtime_removed'])

    def test_actual_scripted_rename_of_different_client_is_refused_at_first_boundary(self):
        root=self.fresh()
        with self.assertRaises(driver.Refused):driver.prepare_sequence_rehearsal(root,fault='client-rename',**self.inputs)
        result=json.loads((root/'case/rehearsal-result.json').read_text())
        self.assertIn('client image identity',result['error']);self.assertFalse(result['NativePASS'])
        self.assertFalse((root/'case/native-A-to-B.plan.json').exists())

    def test_actual_scripted_sibling_is_refused_at_first_boundary(self):
        root=self.fresh()
        with self.assertRaises(driver.Refused):driver.prepare_sequence_rehearsal(root,fault='client-sibling',**self.inputs)
        result=json.loads((root/'case/rehearsal-result.json').read_text())
        self.assertIn('client directory',result['error']);self.assertFalse(result['NativePASS'])
        self.assertFalse((root/'case/native-A-to-B.plan.json').exists())

    def test_protected_changes_during_real_public_switch_are_refused(self):
        for phase in ('forward','reverse'):
            for fault in ('cairn','account','workspace','state'):
                with self.subTest(fault=fault,phase=phase):
                    root=self.fresh()
                    with self.assertRaises(driver.Refused):
                        driver.prepare_sequence_rehearsal(root,fault=fault,fault_phase=phase,**self.inputs)
                    result=json.loads((root/'case/rehearsal-result.json').read_text())
                    self.assertIn('protected',result['error']);self.assertFalse(result['NativePASS'])
                    label='native-A-to-B' if phase=='forward' else 'native-B-to-retained-Go3-A'
                    operation=json.loads((root/'case'/(label+'.exit.json')).read_text())
                    self.assertEqual(len(operation['interleaved']),1)

    def test_deliberate_database_write_during_real_public_control_is_refused(self):
        for phase in ('forward','reverse'):
            with self.subTest(phase=phase):
                root=self.fresh()
                with self.assertRaisesRegex(driver.Refused,'protected API state'):
                    driver.prepare_sequence_rehearsal(root,fault='database',fault_phase=phase,**self.inputs)
                result=json.loads((root/'case/rehearsal-result.json').read_text())
                self.assertFalse(result['NativePASS']);self.assertFalse(result['RehearsalPASS'])
                self.assertEqual(result['actual_Agy'],0)
                label='native-A-to-B' if phase=='forward' else 'native-B-to-retained-Go3-A'
                operation=json.loads((root/'case'/(label+'.exit.json')).read_text())
                self.assertEqual(len(operation['interleaved']),1)
                mutation=operation['interleaved'][0]['database_mutation']
                self.assertEqual(mutation['operation'],'remember')
                self.assertEqual(str(uuid.UUID(mutation['record_id'])),mutation['record_id'])
                self.assertEqual(mutation['version'],1)
                preservation=json.loads((root/'case'/(label+'.preservation.json')).read_text())
                self.assertNotEqual(preservation['database_before'],preservation['database_after'])
                if phase=='forward':self.assertFalse((root/'case/native-B-to-retained-Go3-A.plan.json').exists())
                else:self.assertFalse((root/'catalog/phase-restored-A.trace').exists())


class ScaleSelectionTests(unittest.TestCase):
    """Real local files test input observation; they are not a scale rehearsal."""
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cairn-scale-input-',dir=Path.home()/'.cache')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.directory=self.root/'bindings';self.directory.mkdir(mode=0o700)
        self.token=self.root/'unit.token';self.token.write_bytes(b'UNIT fixture credential; never read')
        self.token.chmod(0o400)
        self.selection=dict(Directory=str(self.directory),Pins={})
        for i in range(8):
            path=self.directory/f'member-{i}.json'
            path.write_text(json.dumps(dict(harness='unit-only',inbox_recall_binding=
                None if i<3 else str(self.root/f'binding-{i}.json'))))
            path.chmod(0o400);self.selection['Pins'][path.name]=driver.checked_pin(path)['SHA256']

    def facts(self):
        return driver.scale_selection_facts(self.root,self.selection,[self.token])

    def test_eight_actual_profiles_three_unbound_with_no_qualification_claim(self):
        facts=self.facts()
        self.assertEqual(facts['binding_count'],8);self.assertEqual(facts['unbound_count'],3)
        self.assertEqual(len(facts['profiles']),8)
        self.assertFalse(facts['NativePASS']);self.assertFalse(facts['complete_unit_qualified'])

    def test_omitted_or_extra_selected_member_refuses(self):
        omitted=copy.deepcopy(self.selection)
        self.selection['Pins'].pop('member-7.json')
        with self.assertRaisesRegex(driver.Refused,'eight'):self.facts()
        self.selection=omitted
        (self.directory/'unselected.json').write_text('{}')
        with self.assertRaisesRegex(driver.Refused,'inventory'):self.facts()

    def test_wrong_unbound_count_and_changed_pin_refuse(self):
        path=self.directory/'member-0.json'
        path.chmod(0o600);path.write_text(json.dumps(dict(harness='unit-only',
            inbox_recall_binding=str(self.root/'binding-extra.json'))));path.chmod(0o400)
        with self.assertRaisesRegex(driver.Refused,'pin'):self.facts()
        self.selection['Pins'][path.name]=driver.checked_pin(path)['SHA256']
        with self.assertRaisesRegex(driver.Refused,'three'):self.facts()

    def test_credential_inode_alias_refuses_before_opening_contents(self):
        path=self.directory/'member-0.json';path.unlink();os.link(self.token,path)
        self.selection['Pins'][path.name]='0'*64
        with patch.object(driver.os,'open',side_effect=AssertionError('credential content open')):
            with self.assertRaisesRegex(driver.Refused,'credential'):self.facts()

    def test_symlink_or_outside_root_selection_refuses(self):
        path=self.directory/'member-0.json';path.unlink();path.symlink_to(self.token)
        with self.assertRaises(driver.Refused):self.facts()
        self.selection['Directory']=str(self.root.parent)
        with self.assertRaises(driver.Refused):self.facts()


class UnitScaleShapeTests(unittest.TestCase):
    """Unit-file negatives protect the 8 bound PLUS 3 unbound fixture contract."""
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cairn-unit-scale-',dir=Path.home()/'.cache')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.directory=self.root/'bindings';self.directory.mkdir(mode=0o700)
        self.token=self.root/'unit.token';self.token.write_bytes(b'UNIT credential; never read');self.token.chmod(0o400)
        self.selection=dict(Directory=str(self.directory),Pins={});self.memories=[]
        for i in range(11):
            home=self.root/f'home-{i}';home.mkdir(mode=0o700)
            path=self.directory/f'coord-{i:02}.json'
            driver.write_json(path,dict(harness='unit-only',config_home=str(home),
                inbox_recall_binding=None if i<3 else str(self.root/f'UNIT-binding-{i}')))
            self.selection['Pins'][path.name]=driver.checked_pin(path)['SHA256']
            if i==0 or i>=3:
                memory=self.root/f'memory-{i}.json';driver.write_json(memory,dict(config_home=str(home)))
                self.memories.append(driver.checked_pin(memory))

    def facts(self):
        return driver.unit_scale_input_facts(self.root,self.selection,self.memories,[self.token])

    def test_eight_bound_plus_three_unbound_are_observed_as_eleven_without_runtime_claim(self):
        facts=self.facts()
        self.assertEqual((facts['bound_count'],facts['unbound_count'],facts['binding_count']),(8,3,11))
        self.assertEqual(facts['kind'],'UNIT_CONTROL_SCALE_FIXTURE_SHAPE')
        self.assertEqual(len(facts['memories']),9);self.assertFalse(facts['NativePASS'])
        self.assertFalse(facts['complete_unit_qualified'])

    def test_eight_total_or_extra_or_omitted_member_is_not_full_scale(self):
        original=copy.deepcopy(self.selection)
        for name in list(self.selection['Pins'])[8:]:self.selection['Pins'].pop(name)
        with self.assertRaisesRegex(driver.Refused,'eleven'):self.facts()
        self.selection=original
        driver.write_json(self.directory/'omitted.json',{})
        with self.assertRaisesRegex(driver.Refused,'inventory'):self.facts()

    def test_missing_external_or_nonprivate_home_refuses_without_repair(self):
        path=self.directory/'coord-00.json';original=json.loads(path.read_bytes())
        for value in (None,str(self.root.parent)):
            path.chmod(0o600);path.write_text(json.dumps(dict(original,config_home=value)));path.chmod(0o400)
            self.selection['Pins'][path.name]=driver.checked_pin(path)['SHA256']
            with self.assertRaises(driver.Refused):self.facts()
        path.chmod(0o600);path.write_text(json.dumps(original));path.chmod(0o400)
        self.selection['Pins'][path.name]=driver.checked_pin(path)['SHA256']
        home=Path(original['config_home']);home.chmod(0o755)
        with self.assertRaises(driver.Refused):self.facts()
        self.assertEqual(stat.S_IMODE(home.stat().st_mode),0o755)

    def test_memory_home_is_also_declared_private_and_selected(self):
        pin=self.memories[0];path=Path(pin['Path']);path.chmod(0o600)
        path.write_text('{}');path.chmod(0o400);self.memories[0]=driver.checked_pin(path)
        with self.assertRaisesRegex(driver.Refused,'config_home'):self.facts()

    def test_pin_change_or_credential_alias_cannot_be_reported_as_scale_input(self):
        path=self.directory/'coord-00.json';path.chmod(0o600);path.write_text('{}');path.chmod(0o400)
        with self.assertRaisesRegex(driver.Refused,'pin'):self.facts()
        path.unlink();os.link(self.token,path)
        with self.assertRaisesRegex(driver.Refused,'credential'):self.facts()


class SourceAdapterTests(unittest.TestCase):
    """Owned unit inputs check adapters only, not genuine producer/runtime proof."""
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cairn-source-adapter-',dir=Path.home()/'.cache')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.images={};self.pins=dict(images={})
        for role in driver.ROLES:
            path=self.root/role;path.write_bytes(('UNIT ONLY '+role).encode());path.chmod(0o500)
            self.images[role]=path
            self.pins['images'][role]=dict(path=str(path),sha256=driver.checked_pin(path)['SHA256'])

    def test_observer_is_the_actual_test_role_without_mutating_five_role_packet(self):
        before=copy.deepcopy(self.pins)
        selected=driver.bootstrap_fixture_images(self.images,self.pins)
        self.assertEqual(selected,dict(api=self.images['api'],control=self.images['control'],
            stager=self.images['stager'],tests=self.images['observer']))
        self.assertEqual(self.pins,before);self.assertEqual(set(self.images),set(driver.ROLES))

    def test_missing_role_alias_or_changed_image_refuses(self):
        images=dict(self.images);images.pop('helper')
        with self.assertRaises(driver.Refused):driver.bootstrap_fixture_images(images,self.pins)
        images=dict(self.images,observer=self.images['control'])
        with self.assertRaises(driver.Refused):driver.bootstrap_fixture_images(images,self.pins)
        self.images['observer'].chmod(0o700);self.images['observer'].write_bytes(b'changed')
        with self.assertRaises(driver.Refused):driver.bootstrap_fixture_images(self.images,self.pins)

    def test_short_new_root_planning_has_no_filesystem_side_effect(self):
        path=self.root/'new'
        plan=driver.production_fixture_plan(path)
        self.assertFalse(path.exists());self.assertLess(len(os.fsencode(plan['api_socket'])),108)
        self.assertEqual(plan['api_owner'],'B contextmanager; f.host.close()')
        self.assertEqual(plan['caller_owned'],['cluster','images','root','client','live_trace'])
        with self.assertRaises(driver.Refused):driver.production_fixture_plan(self.root)
        with self.assertRaises(driver.Refused):driver.production_fixture_plan(self.root/('long'*35))

    def material(self):
        directory=self.root/'bindings';directory.mkdir(mode=0o700)
        token=self.root/'unit.token';token.write_bytes(b'UNIT credential; never read');token.chmod(0o400)
        selection=dict(directory=str(directory),pins={});groups=[]
        for i in range(8):
            name=f'member-{i}.json';path=directory/name
            value=dict(harness='unit-only',inbox_recall_binding=None if i<3 else 'UNIT-bound')
            if i>=3:value['config_home']=str(self.root/f'account-{i}')
            driver.write_json(path,value);selection['pins'][name]=driver.checked_pin(path)['SHA256']
            groups.append(dict(name=f'unit-{i}',bound=i>=3,members=[name],
                target_receipt=dict(Path=str(self.root/f'UNIT-receipt-{i}'),SHA256=str(i)*64)))
        target=self.root/'target.json';driver.write_json(target,dict(schema='cairn.unit-bootstrap/2',
            current=selection,archives=[]))
        original=self.root/'original.json';driver.write_json(original,dict(schema='cairn.unit-bootstrap/2',
            current=selection,transition=dict(target=driver.checked_pin(target))))
        material=dict(schema='cairn.r07-production-shape-material/1',host='proximal',native_admission=False,
            root=str(self.root),original_current=selection,target_current=selection,
            original_plan=driver.checked_pin(original),target_plan=driver.checked_pin(target),groups=groups,
            target_unbound_receipts=[row['target_receipt'] for row in groups if not row['bound']])
        driver.write_json(self.root/'production-material.json',material)
        return SimpleNamespace(root=self.root),original,driver.checked_pin(original)['SHA256'],[token]

    def test_actual_lowercase_material_adapter_preserves_missing_accounts_and_all_members(self):
        args=self.material();before={p:driver.checked_pin(p) for p in (self.root/'bindings').iterdir()}
        observed=driver.production_material_inputs(*args)
        self.assertEqual(observed['selection']['Directory'],str(self.root/'bindings'))
        self.assertEqual(observed['facts']['binding_count'],8);self.assertEqual(observed['facts']['unbound_count'],3)
        self.assertEqual([row['field'] for row in observed['census_boundaries']],['config_home']*3)
        self.assertFalse(observed['NativePASS']);self.assertFalse(observed['complete_unit_qualified'])
        with self.assertRaisesRegex(driver.Refused,'config_home'):driver.require_scale_census_inputs(observed)
        self.assertEqual(before,{p:driver.checked_pin(p) for p in before})

    def test_material_mismatched_plan_or_unbound_members_refuses(self):
        args=self.material()
        with self.assertRaises(driver.Refused):driver.production_material_inputs(args[0],args[1],'f'*64,args[3])
        path=self.root/'production-material.json';value=json.loads(path.read_bytes())
        value['target_unbound_receipts'].pop();path.chmod(0o600);path.write_text(json.dumps(value));path.chmod(0o400)
        with self.assertRaisesRegex(driver.Refused,'unbound'):driver.production_material_inputs(*args)

    def test_material_credential_alias_refuses_before_any_content_read(self):
        args=self.material();path=self.root/'production-material.json';path.unlink();os.link(args[3][0],path)
        with patch.object(driver.os,'open',side_effect=AssertionError('credential content open')):
            with self.assertRaisesRegex(driver.Refused,'credential'):driver.production_material_inputs(*args)

    def test_source_adapter_does_not_start_b_factory_to_plan(self):
        import check_r07_bootstrap_reverse as bootstrap
        self.assertTrue(callable(bootstrap.production_shape_fixture))
        self.assertTrue(callable(bootstrap.fixture_effect_plan))
        with patch.object(bootstrap,'production_shape_fixture',side_effect=AssertionError('B runtime forbidden')):
            driver.production_fixture_plan(self.root/'uncreated')
            driver.bootstrap_fixture_images(self.images,self.pins)

    def proof(self):
        result=dict(result='PASS',workflow_proof='SHARED_CLI_WORKFLOW_WITH_FIXTURE_FENCE',deadline_seconds=30,
            forward=dict(published=True,rechecked=True),reverse=dict(published=True,rechecked=True),
            seconds=dict(switch=1.0,reverse=2.0),workflow_launch_to_join_seconds=dict(switch=2.0,reverse=3.0),
            workflow_margin_seconds=dict(switch=28.0,reverse=27.0),library_margin_seconds=dict(switch=29.0,reverse=28.0),
            default_public_positive='UNESTABLISHED until admitted R07 stopped boundary',
            default_public_refusal=dict(proof='DEFAULT_PUBLIC_REFUSAL_ACTIVE_WORKERS',unit_actions=0,
                outside_owned_root_writes=0,result=dict(published=False,inspected=False,owner_fence='unknown',
                error='unit or activation trigger is not stopped and masked')))
        stdout={op:'=== RUN   TestUnitBootstrapOwnedTransition\n--- PASS: TestUnitBootstrapOwnedTransition (1.00s)\nPASS\n'
            for op in ('switch','reverse')}
        return result,stdout

    def test_scoped_receipt_parser_never_claims_default_public_or_native_positive(self):
        result,stdout=self.proof();observed=driver.bootstrap_workflow_observation(result,stdout)
        self.assertEqual(observed['workflow_proof'],result['workflow_proof'])
        self.assertFalse(observed['NativePASS']);self.assertFalse(observed['complete_unit_qualified'])
        self.assertEqual(observed['default_public_positive'],result['default_public_positive'])
        self.assertEqual(observed['kind'],'NONNATIVE_BOOTSTRAP_RECEIPT_OBSERVATION')

    def test_wrong_proof_default_refusal_or_skipped_execution_refuses(self):
        for field,value in (('workflow_proof','DIRECT_LIBRARY_WITH_FIXTURE_FENCE'),
                            ('default_public_refusal',None),('default_public_positive','PASS')):
            result,stdout=self.proof();result[field]=value
            with self.subTest(field=field),self.assertRaises(driver.Refused):
                driver.bootstrap_workflow_observation(result,stdout)
        for raw in ('PASS\n','no tests to run\n','--- SKIP: TestUnitBootstrapOwnedTransition\n'):
            result,stdout=self.proof();stdout['reverse']=raw
            with self.subTest(raw=raw),self.assertRaises(driver.Refused):
                driver.bootstrap_workflow_observation(result,stdout)

    def test_each_operation_requires_finite_positive_launch_and_library_margin(self):
        for field in ('seconds','workflow_launch_to_join_seconds'):
            for value in (False,0,-1,30,float('inf'),float('nan')):
                result,stdout=self.proof();result[field]['reverse']=value
                with self.subTest(field=field,value=value),self.assertRaises(driver.Refused):
                    driver.bootstrap_workflow_observation(result,stdout)
        result,stdout=self.proof();result['workflow_margin_seconds']['reverse']=28
        with self.assertRaises(driver.Refused):driver.bootstrap_workflow_observation(result,stdout)


class RehearsalTerminalTests(unittest.TestCase):
    """Failure after the sequence cannot leave a passing atomic outcome."""
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cairn-rehearsal-terminal-',dir=Path.home()/'.cache')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.approved=dict(rehearsal=dict(ledger=str(self.root/'rehearsal-ledger.json')),
            rehearsal_pins=dict(kind='PREPARATION_REHEARSAL',NativePASS=False,actual_Agy=0))
        self.reservation=driver.RehearsalReservation(self.approved)
        self.outcome=dict(state='passed',kind='PREPARATION_REHEARSAL',RehearsalPASS=True,
            NativePASS=False,actual_Agy=0,actual_native_attempts=0)

    def terminal(self):
        terminal=self.reservation.path.with_suffix('.terminal.json')
        data=json.loads(terminal.read_bytes())
        self.assertEqual(data['kind'],'PREPARATION_REHEARSAL')
        self.assertEqual(data['state'],'failed')
        self.assertFalse(data['outcome']['NativePASS'])
        self.assertFalse(data['outcome']['RehearsalPASS'])
        return data['outcome']

    def test_cleanup_failure_after_sequence_clears_pass_and_preserves_first_error(self):
        def interrupted(_):
            driver.roundtrip.write(self.root/'rehearsal-result.json',dict(self.outcome,
                state='failed',error_type='ValueError',error='first owned close failure',
                cleanup_errors=['API join failed']))
            raise RuntimeError('secondary final pin failure')
        with self.assertRaisesRegex(RuntimeError,'secondary final pin'):
            driver.run_reserved_case(self.approved,interrupted,self.reservation,
                self.root,'rehearsal-result.json')
        result=self.terminal()
        self.assertEqual(result['error'],'first owned close failure')
        self.assertEqual(result['cleanup_errors'],['API join failed'])
        self.assertEqual(result['finalization_error']['error'],'secondary final pin failure')

    def test_result_write_failure_after_sequence_clears_pass_in_atomic_terminal(self):
        with patch.object(driver.roundtrip,'write',side_effect=OSError('owned evidence write failed')):
            with self.assertRaisesRegex(OSError,'owned evidence write'):
                driver.run_reserved_case(self.approved,lambda _:self.outcome,self.reservation,
                    self.root,'rehearsal-result.json')
        self.assertEqual(self.terminal()['result_write_error'],'OSError')


class LivePreservationTests(unittest.TestCase):
    """Concurrent file actors exercise preservation, not native/currentness proof."""
    ACTOR='''import json,os,sys
from pathlib import Path
request=json.loads(sys.stdin.readline())
for action,name in request:
    path=Path(name)
    if action=='append':
        mode=path.stat().st_mode&0o777;path.chmod(mode|0o200)
        with path.open('ab') as stream:stream.write(b'fixture concurrent append\\n')
        path.chmod(mode)
    elif action=='truncate':path.write_bytes(b'')
    elif action=='rewrite':path.write_bytes(b'rewritten fixture\\n')
    elif action=='replace':
        temporary=path.with_name('actor-replacement');temporary.write_bytes(path.read_bytes())
        os.replace(temporary,path)
print('joined owned stand-in')
'''
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cairn-live-preserve-',dir=Path.home()/'.cache')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        home=self.root/'native-home';account=home/'.gemini/config';internal=home/'.gemini/antigravity-cli'
        account.mkdir(mode=0o700,parents=True);internal.mkdir(mode=0o700)
        work=self.root/'workspace';work.mkdir(mode=0o700)
        api=self.root/'api-home';api.mkdir(mode=0o700)
        config=self.root/'selected-configs';config.mkdir(mode=0o700)
        state=self.root/'coordination-state';state.mkdir(mode=0o700)
        token=self.root/'unit.token';token.write_bytes(b'purpose-written UNIT-only credential');token.chmod(0o400)
        self.protected=[self.root/'coordination.json',account/'hooks.json',work/'fixture-data',
            state/'fixture-state.json',self.root/'sealed-phase.trace',self.root/'native-readback.json']
        for path in self.protected:path.write_bytes(b'purpose-written protected fixture\n')
        for path in [self.root/'kernel.trace',self.root/'kernel.stderr',internal/'data']:path.write_bytes(b'initial bytes\n')
        self.fixture=SimpleNamespace(root=self.root,home=home,account=account,work=work,
            unit=dict(Credential=str(token),ConfigRoot=str(config)),host=SimpleNamespace(home=api))
        self.session=SimpleNamespace(child=SimpleNamespace(trace=self.root/'kernel.trace'))

    def actor(self,actions):
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',self.ACTOR],
            input=json.dumps([(action,str(path)) for action,path in actions]),text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('joined owned stand-in',result.stdout)

    def snapshot(self):return driver.live_root_snapshot(self.fixture,self.session)

    def test_concurrent_append_and_only_explicit_client_internal_churn_are_allowed(self):
        before=self.snapshot()
        self.actor([('append',self.root/'kernel.trace'),('append',self.root/'kernel.stderr'),
            ('append',self.fixture.home/'.gemini/antigravity-cli/data')])
        after=self.snapshot()
        driver.assert_live_preserved(before,after,Path(self.fixture.unit['ConfigRoot']))
        self.assertEqual(before['rows'],after['rows'])
        for path,row in before['appends'].items():
            self.assertGreater(len(after['appends'][path]['bytes']),len(row['bytes']))

    def test_every_cairn_account_workspace_state_and_sealed_file_stays_protected(self):
        for path in self.protected:
            with self.subTest(path=path.name):
                before=self.snapshot();self.actor([('append',path)]);after=self.snapshot()
                with self.assertRaisesRegex(driver.Refused,'protected'):
                    driver.assert_live_preserved(before,after,Path(self.fixture.unit['ConfigRoot']))

    def test_trace_and_stderr_rewrite_truncate_or_inode_replacement_refuse(self):
        for path in [self.root/'kernel.trace',self.root/'kernel.stderr']:
            for action in ('truncate','rewrite','replace'):
                path.write_bytes(b'initial bytes\n')
                with self.subTest(path=path.name,action=action):
                    before=self.snapshot();self.actor([(action,path)]);after=self.snapshot()
                    with self.assertRaisesRegex(driver.Refused,'replaced, truncated or rewritten'):
                        driver.assert_live_preserved(before,after,Path(self.fixture.unit['ConfigRoot']))

    def test_client_internal_symlink_and_hardlink_escape_are_refused(self):
        inside=self.fixture.home/'.gemini/antigravity-cli/escape'
        inside.symlink_to(self.protected[0])
        with self.assertRaisesRegex(driver.Refused,'symlink escapes'):self.snapshot()
        inside.unlink();os.link(self.protected[0],inside)
        with self.assertRaisesRegex(driver.Refused,'hardlink escape'):self.snapshot()


class TraceBoundaryTests(unittest.TestCase):
    """Purpose-written parser fixtures; no native raw file is edited."""
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cairn-boundary-snapshot-',dir=Path.home()/'.cache')
        self.addCleanup(temporary.cleanup);self.path=Path(temporary.name)/'fixture.trace'
        self.session=SimpleNamespace(child=SimpleNamespace(trace=self.path,pid=200))

    def test_partial_line_waits_for_actual_concurrent_complete_bytes(self):
        initial=b'200 read(0';suffix=b' <unfinished ...>\n'
        self.path.write_bytes(initial)
        def append():
            with self.path.open('ab') as stream:stream.write(suffix)
        writer=threading.Timer(.05,append);writer.start();self.addCleanup(writer.join)
        raw=driver.complete_trace_boundary(self.session,timeout=2)
        self.assertEqual(raw,initial+suffix);self.assertEqual(len(raw),len(initial+suffix))

    def test_returned_offset_uses_that_snapshot_even_after_file_grows(self):
        initial=b'200 write(2, "x", 1) = 1\n';extra=b'200 write(2, "y", 1) = 1\n'
        self.path.write_bytes(initial)
        raw=driver.complete_trace_boundary(self.session,timeout=1)
        def append():
            with self.path.open('ab') as stream:stream.write(extra)
        writer=threading.Thread(target=append);writer.start();writer.join()
        self.assertEqual(raw,initial);self.assertEqual(len(raw),len(initial))
        self.assertGreater(self.path.stat().st_size,len(raw))

    def test_timeout_preserves_partial_bytes_and_nonidle_pending_calls_refuse(self):
        for raw in (b'200 read(0',b'200 read(4 <unfinished ...>\n',
                    b'201 read(0 <unfinished ...>\n'):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaisesRegex(AssertionError,'Timed out'):
                    driver.complete_trace_boundary(self.session,timeout=.05)
                self.assertEqual(self.path.read_bytes(),raw)


class NativeStateSchemaTests(unittest.TestCase):
    """Purpose-written redacted schema, not a preserved native case or API mock.

    Go CLI.ensureHookAgentSession (hook_cli.go:484-496) and Python
    coordination._handle (coordination.py:4017-4024)
    write registration and the authenticated API's agent as separate members.
    """
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cairn-state-schema-',dir=Path.home()/'.cache')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.native='NONNATIVE-schema-fixture-conversation'
        self.state=dict(schema='cairn.native-session/1',process=dict(pid=123,start='fixture',boot='fixture'),
            registration=dict(native_session_id=self.native),workspace='/NONNATIVE-fixture',
            agent=dict(native_session_id=self.native))

    def write_state(self,state):
        path=self.root/'fixture-state.json'
        path.write_text(json.dumps(state)+'\n');path.chmod(0o600)
        return path

    def test_product_nested_identity_is_selected_without_a_top_level_alias(self):
        path=self.write_state(self.state)
        self.assertEqual(driver.native_state_pins(self.root,self.native),[driver.checked_pin(path)])

    def test_both_nested_identities_are_required_typed_and_equal_to_the_client(self):
        for field in ('registration','agent'):
            for bad in (None,[],{},dict(native_session_id=None),dict(native_session_id=17),
                        dict(native_session_id=True),dict(native_session_id=''),
                        dict(native_session_id=' '),dict(native_session_id='foreign-conversation')):
                state=copy.deepcopy(self.state);state[field]=bad
                with self.subTest(field=field,bad=bad),self.assertRaises(driver.Refused):
                    self.write_state(state);driver.native_state_pins(self.root,self.native)

    def test_a_top_level_identity_cannot_mask_missing_or_conflicting_nested_state(self):
        for nested,outer in ((False,self.native),(True,'foreign'),(True,17),(True,None)):
            state=copy.deepcopy(self.state) if nested else dict(schema='cairn.native-session/1')
            state['native_session_id']=outer
            with self.subTest(nested=nested,outer=outer),self.assertRaises(driver.Refused):
                self.write_state(state);driver.native_state_pins(self.root,self.native)
        for state in ([],None,dict(self.state,schema='cairn.native-session/2')):
            with self.subTest(state=state),self.assertRaises(driver.Refused):
                self.write_state(state);driver.native_state_pins(self.root,self.native)

    def test_matching_optional_legacy_identity_never_replaces_the_two_nested_ids(self):
        state=dict(self.state,native_session_id=self.native)
        path=self.write_state(state)
        self.assertEqual(driver.native_state_pins(self.root,self.native),[driver.checked_pin(path)])

    def test_missing_or_extra_foreign_state_never_selects_a_conversation(self):
        with self.assertRaisesRegex(driver.Refused,'exactly one'):
            driver.native_state_pins(self.root,self.native)
        self.write_state(self.state)
        (self.root/'foreign.json').write_text(json.dumps(self.state)+'\n')
        with self.assertRaisesRegex(driver.Refused,'exactly one'):
            driver.native_state_pins(self.root,self.native)


class NativeDriverBoundaries(unittest.TestCase):
    """CLI/environment/codec controls; never execute Agy or a provider."""
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cairn-native-boundary-',dir=Path.home()/'.cache')
        self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)

    def test_native_cli_dispatches_through_reservation_and_preflight(self):
        approved={'UNIT':'no native admission or execution'}
        def reserved(selected,preflight,execute):
            self.assertIs(selected,approved);self.assertIs(preflight,driver.native_preflight)
            self.assertIs(execute,driver.execute_native)
            return dict(state='passed',NativePASS=False,scope='UNIT dispatch only')
        with patch.object(driver,'checked_native_admission',return_value=approved) as checked, \
                patch.object(driver,'run_native_attempt',side_effect=reserved), \
                patch.object(driver.r04,'GeminiFixture',side_effect=AssertionError('provider')), \
                patch.object(driver,'open_native_session',side_effect=AssertionError('native')):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code=driver.main(['--native-unit','--admission',str(self.root/'input.json'),
                    '--admission-sha256','a'*64,'--attempt-id',str(uuid.uuid4())])
        self.assertEqual(code,0);self.assertFalse(json.loads(out.getvalue())['NativePASS'])
        self.assertEqual(checked.call_count,1);self.assertEqual(list(self.root.iterdir()),[])

    def test_scripted_mode_or_interpreter_cannot_select_actual_native_execution(self):
        with patch.object(driver,'checked_native_admission',side_effect=AssertionError('native selection')), \
                patch.object(driver,'execute_owned_case',side_effect=AssertionError('application execution')):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(driver.main(['--native-unit','--rehearse-sequence']),2)
                self.assertEqual(driver.main(['--prepare-stubbed','--rehearse-sequence',
                    '--admission',str(self.root/'not-an-admission.json')]),2)
            with self.assertRaisesRegex(driver.Refused,'original held client'):
                driver.execute_native(dict(admission=dict(client=driver.tool_pin(Path(sys.executable).resolve())),
                    rehearsal=True))
        self.assertEqual(list(self.root.iterdir()),[])

    def test_expired_or_missing_selection_cannot_reach_reservation(self):
        with patch.object(driver,'checked_native_admission',side_effect=driver.Refused('expired selector')), \
                patch.object(driver,'run_native_attempt',side_effect=AssertionError('reservation before admission')):
            with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(driver.main(['--native-unit']),75)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_long_unix_path_refuses_before_any_application_or_reservation(self):
        catalog=self.root/('x'*80);catalog.mkdir(mode=0o700)
        evidence=self.root/'evidence';evidence.mkdir(mode=0o700)
        approved=dict(admission=dict(catalog_root=str(catalog),evidence=str(evidence)))
        with patch.object(driver,'pg_bindir',side_effect=AssertionError('PG lookup before path refusal')), \
                patch.object(driver,'NativeReservation',side_effect=AssertionError('reservation')):
            with self.assertRaisesRegex(driver.PrerequisiteUnavailable,'too long'):driver.native_preflight(approved)
        self.assertEqual(list(catalog.iterdir()),[]);self.assertEqual(list(evidence.iterdir()),[])

    def test_child_home_key_and_proxy_scope_preserve_parent(self):
        home=self.root/'native-home';home.mkdir(mode=0o700)
        account=self.root/'settings';account.mkdir(mode=0o700)
        fixture=SimpleNamespace(home=home,f=SimpleNamespace(account=account))
        injected={'GEMINI_API_KEY':'UNIT owner key','HTTP_PROXY':'http://unit.invalid','CAIRN_WAKE_CONTEXT':'UNIT context',
            'CAIRN_DATABASE_URL':'UNIT DSN','OPENROUTER_API_KEY':'UNIT forbidden route'}
        with patch.dict(os.environ,injected):
            before=dict(os.environ);env,settings=driver.native_child_environment(fixture,'http://127.0.0.1:4321')
            self.assertEqual(dict(os.environ),before)
        self.assertEqual(env['HOME'],str(home));self.assertEqual(env['GEMINI_API_KEY'],'r04-synthetic-unused')
        self.assertEqual(env['AGY_CLI_DISABLE_AUTO_UPDATE'],'1')
        self.assertEqual(env['PATH'],os.environ['PATH']);self.assertEqual(env.get('CODEX_HOME'),os.environ.get('CODEX_HOME'))
        self.assertTrue(all(key not in env for key in injected if key!='GEMINI_API_KEY'))
        self.assertEqual(json.loads(settings.read_bytes()),dict(modelProvider='gemini'))

    def test_callback_io_requires_exact_byte_count_and_actual_eof(self):
        self.assertEqual(driver.kernel_io(dict(body='read(0, "\\x61\\x62", 4096) = 2')),(0,b'ab'))
        self.assertEqual(driver.kernel_io(dict(body='read(0, "", 4096) = 0')),(0,b''))
        for raw in ['read(0, "ab", 4096) = 3','write(9, "ab", 2) = 1',
                    'read(0, 0x1234, 4096) = ? ERESTARTSYS (To be restarted)',
                    'write(9, "ab"..., 32) = 32']:
            with self.subTest(raw=raw),self.assertRaises(driver.Refused):driver.kernel_io(dict(body=raw))

    def test_collection_does_not_make_pending_connect_auditable(self):
        raw=b'200 connect(3, {sa_family=AF_INET <unfinished ...>\n'
        calls,pending=driver.kernel_calls(raw,collecting=True)
        self.assertEqual(calls,[]);self.assertEqual(pending[0]['name'],'connect')
        with self.assertRaisesRegex(driver.Refused,'unfinished connect'):driver.kernel_calls(raw)

    def test_unrelated_truncated_asset_is_not_a_callback_frame(self):
        self.assertIsNone(driver.kernel_hook_frame(dict(pid=200,body='write(24, "<svg"..., 9000) = 9000')))
        truncated='write(29, "{\\\"conversationId\\\":\\\"UNIT\\\""..., 9000) = 9000'
        with self.assertRaisesRegex(driver.Refused,'truncated'):driver.kernel_hook_frame(dict(pid=200,body=truncated))

    def test_invalid_packet_shape_returns75_without_reservation(self):
        with patch.object(driver,'checked_native_admission',side_effect=KeyError('missing packet field')), \
                patch.object(driver,'run_native_attempt',side_effect=AssertionError('reservation')):
            with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(driver.main(['--native-unit']),75)

    def test_hook_classification_ignores_builtin_markdown_but_rejects_invalid_hook(self):
        for body in (b'\x7fELF',b'# Lifecycle Hooks: "conversationId" "hookEventName"',b'{"wire":1}'):
            escaped=''.join('\\x%02x'%b for b in body)
            self.assertIsNone(driver.kernel_hook_frame(dict(pid=200,body=f'write(63, "{escaped}"..., 16863523) = 16863523')))
        metadata=json.dumps(dict(conversationId='u',workspacePaths=['/owned'])).encode()
        self.assertIsNone(driver.kernel_hook_frame(dict(pid=200,
            body=f'write(24, {json.dumps(metadata.decode())}, {len(metadata)}) = {len(metadata)}')))
        for body in (b'{"conversationId":',b'{"conversationId":"u","hookEventName":"Stop"}',
                     b'{"conversationId":123,"hookEventName":"Stop","workspacePaths":["/owned"]}'):
            with self.assertRaises(ValueError):
                driver.kernel_hook_frame(dict(pid=200,body=f'write(29, {json.dumps(body.decode())}, {len(body)}) = {len(body)}'))

    def test_budget_keeps_fixed_cap_and_reserve_instead_of_shrinking_control(self):
        report=driver.trace_budget([10*1024*1024,11*1024*1024])
        self.assertEqual(report['cap'],268435456)
        self.assertEqual(report['projected_bytes'],11*1024*1024*6+64*1024*1024)
        self.assertGreater(report['margin_bytes'],0)
        for values in ([],[1],[1,0],[32*1024*1024,32*1024*1024]):
            with self.assertRaises(driver.Refused):driver.trace_budget(values)

    def test_kernel_policy_refuses_wrong_arch_and_forged_policy_before_install(self):
        policy=dict(schema='cairn.r06-kernel-network/1',tcp_ports=[])
        with patch.object(driver.os,'uname',return_value=SimpleNamespace(machine='aarch64')):
            with self.assertRaisesRegex(driver.Refused,'architecture'):driver.install_network_policy(policy)
        for change in ({'disabled':True},{'tcp_ports':[443,443]},{'tcp_ports':[True]},
                       {'tcp_ports':[0]},{'schema':'fake'}):
            with self.assertRaises(driver.Refused):driver.install_network_policy(dict(policy,**change))

    def test_failed_install_or_low_abi_never_execs_unrestricted(self):
        policy=dict(schema='cairn.r06-kernel-network/1',tcp_ports=[])
        from unittest.mock import Mock
        system=SimpleNamespace(syscall=Mock(return_value=3),prctl=Mock(side_effect=AssertionError('after ABI refusal')))
        with patch.object(driver.ctypes,'CDLL',return_value=system):
            with self.assertRaisesRegex(driver.Refused,'ABI4'):driver.install_network_policy(policy)
        with patch.object(driver,'install_network_policy',side_effect=OSError('seccomp refused')), \
                patch.object(driver.os,'execve',side_effect=AssertionError('unrestricted exec')):
            with self.assertRaisesRegex(OSError,'seccomp refused'):
                driver.confined_exec([json.dumps(policy),str(self.root/'receipt.json'),'--','/bin/true'])

    def test_bootstrap_refuses_inherited_network_stdio_and_prewritten_receipt(self):
        policy=json.dumps(dict(schema='cairn.r06-kernel-network/1',tcp_ports=[]))
        marker=self.root/'unrestricted-exec'
        argv=[sys.executable,'-B',str(Path(driver.__file__).resolve()),'--confined-exec',policy,
              str(self.root/'kernel-policy.json'),'--',sys.executable,'-c',
              'from pathlib import Path;Path('+repr(str(marker))+').write_text("escaped")']
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as inherited:
            done=subprocess.run(argv,stdin=inherited,capture_output=True,timeout=10)
        self.assertNotEqual(done.returncode,0);self.assertIn(b'inherited network descriptor',done.stderr)
        self.assertFalse(marker.exists())
        receipt=self.root/'kernel-policy.json';receipt.write_text('{"kernel_enforced":true}')
        done=subprocess.run(argv,input=b'',capture_output=True,timeout=10)
        self.assertNotEqual(done.returncode,0);self.assertIn(b'FileExistsError',done.stderr)
        self.assertEqual(receipt.read_text(),'{"kernel_enforced":true}');self.assertFalse(marker.exists())

    def test_raw_cap_rejects_sparse_oversize_before_content_read(self):
        path=self.root/'oversize.trace'
        with path.open('wb') as out:out.truncate(268435457)
        with self.assertRaisesRegex(ValueError,'256 MiB'):
            driver.collector.trace_interval(path,0,268435457,[])



class NativeAdmissionTests(unittest.TestCase):
    """Owned nonnative files and injected packet/source boundaries; no native run."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cairn-agy-admission-', dir=Path.home()/'.cache')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('evidence', 'catalog', 'ledgers', 'packet'):
            (self.root/name).mkdir(mode=0o700)
        self.client = self.root/'owned-nonnative-client'
        self.client.write_bytes(b'NONNATIVE selector test image; never executed\n')
        self.client.chmod(0o500)
        self.attempt = str(uuid.uuid4())
        self.images = {}
        self.pins = dict(images={})
        for role in driver.ROLES:
            path = self.root/('nonnative-'+role)
            path.write_text('not executable: '+role)
            path.chmod(0o400)
            self.images[role] = path
            self.pins['images'][role] = dict(path=str(path), sha256=driver.roundtrip.sha(path))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        fmt = lambda value: value.strftime('%Y-%m-%dT%H:%M:%SZ')
        tools = {name:driver.tool_pin(Path(sys.executable).resolve() if name == 'python3'
                 else Path(shutil.which(name)).resolve()) for name in ('python3','strace')}
        self.body = dict(schema=driver.NATIVE_SCHEMA, source_head='1'*40, source_tree='2'*40,
            images={role:dict(Path=str(path),SHA256=self.pins['images'][role]['sha256'])
                    for role,path in self.images.items()},
            client=dict(Path=str(self.client),SHA256=driver.roundtrip.sha(self.client)),
            provider=copy.deepcopy(driver.LOCAL_PROVIDER), attempt_id=self.attempt, attempt_limit=1,
            issued_at=fmt(now-timedelta(seconds=1)), expires_at=fmt(now+timedelta(minutes=5)),
            ledger=str(self.root/'ledgers'/(self.attempt+'.json')), evidence=str(self.root/'evidence'),
            catalog_root=str(self.root/'catalog'), tools=tools)
        for field in ('manifest','index','build_complete'):
            path=self.root/'packet'/(field+'.json');path.write_text('{}\n');path.chmod(0o400)
            self.body[field]=dict(Path=str(path),SHA256=driver.roundtrip.sha(path))
        self.path=self.root/'admission.json'
        self.patchers=[patch.object(driver,'AGY_SOURCE',self.client),
            patch.object(driver,'AGY_SHA256',self.body['client']['SHA256']),
            patch.object(driver,'native_source_identity',return_value=dict(source_head='1'*40,source_tree='2'*40)),
            patch.object(driver,'checked_packet',return_value=(self.images,self.pins,{'nonnative_injected_packet':True})),
            patch('subprocess.Popen',side_effect=AssertionError('process in admission unit test')),
            patch.object(driver.r04,'AgySession',side_effect=AssertionError('Agy in admission unit test')),
            patch.object(driver.r04,'GeminiFixture',side_effect=AssertionError('provider in admission unit test')),
            patch.object(driver.OwnedCluster,'start',side_effect=AssertionError('PG in admission unit test')),
            patch.object(driver.DisposableHost,'start',side_effect=AssertionError('API in admission unit test'))]
        for item in self.patchers:
            item.start();self.addCleanup(item.stop)

    def select(self, body=None, raw=None, **kwargs):
        self.path.chmod(0o600) if self.path.exists() else None
        self.path.write_text(json.dumps(body or self.body) if raw is None else raw)
        self.path.chmod(0o400)
        return driver.checked_native_admission(self.path,driver.roundtrip.sha(self.path),
                                               kwargs.pop('attempt_id',self.attempt),**kwargs)

    def test_valid_selector_performs_no_application_start_or_reservation(self):
        approved=self.select()
        self.assertEqual(approved['admission'],self.body)
        self.assertEqual(list((self.root/'ledgers').iterdir()),[])
        self.assertEqual(list((self.root/'catalog').iterdir()),[])

    def test_mutated_tuple_fields_refuse_without_consuming(self):
        for field,value in [('source_head','3'*40),('source_tree',driver.BASE_TREE),('attempt_id',str(uuid.uuid4())),
                ('attempt_limit',True),('attempt_limit',2),('schema','cairn.other/1'),('ledger',3)]:
            with self.subTest(field=field,value=value):
                body=copy.deepcopy(self.body);body[field]=value
                with self.assertRaises((driver.Refused,ValueError)):self.select(body)
                self.assertEqual(list((self.root/'ledgers').iterdir()),[])

    def test_missing_unknown_and_duplicate_fields_refuse(self):
        body=copy.deepcopy(self.body);del body['source_tree']
        with self.assertRaises(driver.Refused):self.select(body)
        body=copy.deepcopy(self.body);body['authorized']=True
        with self.assertRaises(driver.Refused):self.select(body)
        with self.assertRaisesRegex(driver.Refused,'duplicate'):
            self.select(raw=json.dumps(self.body)[:-1]+',"attempt_limit":1}')

    def test_expired_future_boundary_and_unbounded_times_refuse(self):
        expiry=datetime.strptime(self.body['expires_at'],'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        issued=datetime.strptime(self.body['issued_at'],'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        for now in (expiry,expiry+timedelta(seconds=1),issued-timedelta(seconds=1)):
            with self.subTest(now=now),self.assertRaises(driver.Refused):self.select(now=now)
        body=copy.deepcopy(self.body);body['expires_at']=(expiry+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with self.assertRaises(driver.Refused):self.select(body)
        body=copy.deepcopy(self.body);body['expires_at']=body['expires_at'].replace('Z','+00:00')
        with self.assertRaises(driver.Refused):self.select(body)

    def test_provider_host_key_proxy_and_unknown_values_refuse(self):
        for key,value in [('host','localhost'),('host','198.51.100.1'),('api_key','subscription'),
                ('NO_PROXY','*'),('port',False),('port',443),('unknown',True)]:
            with self.subTest(key=key,value=value):
                body=copy.deepcopy(self.body);body['provider'][key]=value
                with self.assertRaises(driver.Refused):self.select(body)
        self.assertEqual(list((self.root/'ledgers').iterdir()),[])

    def test_client_manifest_role_and_tool_alias_refuse(self):
        for field in ('client','manifest'):
            body=copy.deepcopy(self.body);body[field]['SHA256']='0'*64
            if field=='manifest':
                # Real packet preflight owns this hash check; injection returns
                # selected bytes, so verify its exact arguments independently.
                with patch.object(driver,'checked_packet',side_effect=driver.Refused('packet pin mismatch')):
                    with self.assertRaises(driver.Refused):self.select(body)
            else:
                with self.assertRaises(driver.Refused):self.select(body)
        body=copy.deepcopy(self.body);body['images']['observer']=body['images']['helper']
        with self.assertRaises(driver.Refused):self.select(body)
        body=copy.deepcopy(self.body);body['tools']['strace']=body['tools']['python3']
        with self.assertRaises(driver.Refused):self.select(body)

    def test_symlink_and_changed_admission_pin_refuse(self):
        self.select();pin=driver.roundtrip.sha(self.path)
        alias=self.root/'alias.json';alias.symlink_to(self.path)
        with self.assertRaises(driver.Refused):driver.checked_native_admission(alias,pin,self.attempt)
        self.path.chmod(0o600);self.path.write_text('{}\n');self.path.chmod(0o400)
        with self.assertRaisesRegex(driver.Refused,'pin mismatch'):
            driver.checked_native_admission(self.path,pin,self.attempt)

    def test_source_root_is_actual_driver_checkout_not_image_source(self):
        body=copy.deepcopy(self.body);body.update(source_head=driver.BASE,source_tree=driver.BASE_TREE)
        with self.assertRaisesRegex(driver.Refused,'driver source'):self.select(body)

    def test_root_overlap_nonempty_and_existing_consumption_refuse(self):
        body=copy.deepcopy(self.body);body['catalog_root']=body['evidence']
        with self.assertRaisesRegex(driver.Refused,'custody'):self.select(body)
        keep=self.root/'catalog'/'keep';keep.write_bytes(b'preserve')
        with self.assertRaisesRegex(driver.Refused,'fresh empty'):self.select()
        self.assertEqual(keep.read_bytes(),b'preserve');keep.unlink()
        ledger=Path(self.body['ledger']);ledger.write_bytes(b'old consumed marker')
        with self.assertRaisesRegex(driver.Refused,'consumed'):self.select()
        self.assertEqual(ledger.read_bytes(),b'old consumed marker')

    def test_prerequisite75_boundary_does_not_create_or_consume_ledger(self):
        approved=self.select()
        def prereq(_):raise driver.PrerequisiteUnavailable('fixture prerequisite')
        with self.assertRaises(driver.PrerequisiteUnavailable):
            driver.run_native_attempt(approved,prereq,lambda _:self.fail('execute after refusal'))
        self.assertFalse(Path(self.body['ledger']).exists())

    def test_failed_result_and_atomic_terminal_precede_expensive_archive(self):
        approved=self.select();evidence=Path(self.body['evidence'])
        approved['_archive_fixture']=SimpleNamespace(root=Path(self.body['catalog_root']),
            unit={'Credential':'synthetic unconsumed credential path'},host_dsn='synthetic-only')
        def failed(_):raise ValueError('first actual oversized trace')
        def interrupted(*args,**kw):
            terminal=Path(self.body['ledger']).with_suffix('.terminal.json')
            self.assertEqual(json.loads(terminal.read_bytes())['state'],'failed')
            self.assertEqual(json.loads((evidence/'native-result.json').read_bytes())['error'],
                             'first actual oversized trace')
            raise RuntimeError('expensive archive interrupted')
        with patch.object(driver.roundtrip,'snapshot_root',side_effect=interrupted):
            with self.assertRaisesRegex(ValueError,'first actual oversized trace'):
                try:driver.run_native_attempt(approved,lambda _:None,failed)
                finally:driver.archive_native_after_terminal(approved)
        archive=json.loads((evidence/'archive-result.json').read_bytes())
        self.assertEqual(archive['state'],'failed')
        self.assertEqual(archive['error_type'],'RuntimeError')
        self.assertFalse(json.loads((evidence/'native-result.json').read_bytes())['NativePASS'])

    def test_secondary_cleanup_failure_preserves_original_native_error(self):
        approved=self.select();evidence=Path(self.body['evidence'])
        def failed(_):
            driver.roundtrip.write(evidence/'native-result.json',dict(state='failed',NativePASS=False,
                error_type='ValueError',error='first trace limit',cleanup_errors=['fixture cleanup failed']))
            raise RuntimeError('secondary cleanup failure')
        with self.assertRaisesRegex(RuntimeError,'secondary cleanup'):
            driver.run_native_attempt(approved,lambda _:None,failed)
        selected=json.loads(Path(self.body['ledger']).with_suffix('.terminal.json').read_bytes())['outcome']
        self.assertEqual(selected['error'],'first trace limit');self.assertEqual(selected['error_type'],'ValueError')
        self.assertEqual(selected['finalization_error']['error'],'secondary cleanup failure')
        self.assertEqual(selected['cleanup_errors'],['fixture cleanup failed'])

    def test_expiry_is_rechecked_after_preflight_before_reservation(self):
        approved=self.select()
        def expire(value):value['admission']['expires_at']=value['admission']['issued_at']
        with self.assertRaisesRegex(driver.Refused,'changed after preflight'):
            driver.run_native_attempt(approved,expire,lambda _:self.fail('expired execute'))
        self.assertFalse(Path(self.body['ledger']).exists())

    def test_failure_remains_consumed_with_atomic_bound_terminal_and_no_retry(self):
        approved=self.select();calls=[]
        def fail(_):calls.append('entered');raise RuntimeError('owned nonnative failure')
        with self.assertRaises(RuntimeError):driver.run_native_attempt(approved,lambda _:None,fail)
        ledger=Path(self.body['ledger']);saved=ledger.read_bytes()
        terminal=ledger.with_suffix('.terminal.json');raw=terminal.read_bytes();result=json.loads(raw)
        self.assertEqual(result['state'],'failed');self.assertEqual(result['admission'],approved['pin'])
        self.assertEqual(result['tuple'],self.body);self.assertEqual(calls,['entered'])
        with self.assertRaises(FileExistsError):driver.run_native_attempt(approved,lambda _:None,fail)
        self.assertEqual(calls,['entered']);self.assertEqual(ledger.read_bytes(),saved)
        self.assertEqual(terminal.read_bytes(),raw)
        self.assertEqual(sorted(p.name for p in ledger.parent.iterdir()),sorted([ledger.name,terminal.name]))

    def test_two_simultaneous_reservations_have_exactly_one_winner(self):
        approved=self.select()
        def reserve(_):
            try:return driver.NativeReservation(approved)
            except FileExistsError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,range(2)))
        winners=[result for result in results if result is not None]
        self.assertEqual(len(winners),1);winners[0].finish(dict(state='failed',nonnative_control=True))

    def test_terminal_cannot_overwrite_binding_or_prior_result(self):
        approved=self.select();reservation=driver.NativeReservation(approved)
        terminal=reservation.finish(dict(state='failed',admission={'forged':True},tuple={'forged':True}))
        original=terminal.read_bytes();parsed=json.loads(original)
        self.assertEqual(parsed['admission'],approved['pin']);self.assertEqual(parsed['tuple'],self.body)
        with self.assertRaises(FileExistsError):reservation.finish(dict(state='passed'))
        self.assertEqual(terminal.read_bytes(),original)
        self.assertFalse(any(path.name.startswith('.') for path in terminal.parent.iterdir()))

    def test_reservation_rechecks_selected_bytes_and_copies_immutable_binding(self):
        approved=self.select();reservation=driver.NativeReservation(approved)
        original=copy.deepcopy(approved['admission']);approved['admission']['images']={}
        terminal=reservation.finish(dict(state='failed'))
        self.assertEqual(json.loads(terminal.read_bytes())['tuple'],original)
        other=copy.deepcopy(approved);other['admission']=original
        other['admission']['ledger']=str(self.root/'ledgers'/(str(uuid.uuid4())+'.json'))
        with self.assertRaisesRegex(driver.Refused,'changed after preflight'):driver.NativeReservation(other)

    def test_client_is_byte_identical_owned0500_inside_catalog_without_execution(self):
        approved=self.select();source=self.client.stat();result=driver.stage_native_client(approved)
        path=Path(result['pin']['Path']);info=path.stat()
        self.assertTrue(path.is_relative_to(Path(self.body['catalog_root'])))
        self.assertEqual(path.read_bytes(),self.client.read_bytes());self.assertEqual(info.st_mode&0o777,0o500)
        self.assertNotEqual((info.st_dev,info.st_ino),(source.st_dev,source.st_ino))
        self.assertEqual(path.parent.stat().st_mode&0o777,0o700)
        self.assertEqual(path.parent.name,'client')
        self.assertTrue(os.access(path.parent,os.W_OK))  # 0700 is write-capable; boundary guards detect mutation.
        with self.assertRaises(FileExistsError):driver.stage_native_client(approved)
        self.assertEqual(path.read_bytes(),self.client.read_bytes())

    def test_changed_client_pin_refuses_before_creating_catalog_copy(self):
        approved=self.select();self.client.chmod(0o700);self.client.write_bytes(b'changed');self.client.chmod(0o500)
        with self.assertRaises(ValueError):driver.stage_native_client(approved)
        self.assertEqual(list((self.root/'catalog').iterdir()),[])


class AgyPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cairn-agy-prep-test-', dir=Path.home() / '.cache')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def pins(self):
        return dict(python_sha256=driver.tool_pin(Path(sys.executable).resolve())['SHA256'],
                    strace_sha256=driver.tool_pin(Path(shutil.which('strace')).resolve())['SHA256'])

    def test_import_never_launches_a_process(self):
        with patch('subprocess.Popen', side_effect=AssertionError('process on import')), \
                patch.object(driver.r04, 'AgySession', side_effect=AssertionError('Agy on import')):
            importlib.reload(driver)

    def test_native_entry_refuses_before_any_process_or_file(self):
        with patch.object(driver.r04, 'TracedProcess', side_effect=AssertionError('trace launch')), \
                patch.object(driver.r04, 'AgySession', side_effect=AssertionError('native launch')), \
                patch.object(driver.r04, 'GeminiFixture', side_effect=AssertionError('provider launch')), \
                patch.object(driver.OwnedCluster, 'start', side_effect=AssertionError('PG launch')), \
                patch.object(driver.DisposableHost, 'start', side_effect=AssertionError('API launch')), \
                patch.object(driver, 'NativeReservation', side_effect=AssertionError('native reservation')):
            with contextlib.redirect_stderr(io.StringIO()) as diagnostic:
                self.assertEqual(driver.main(['--native-unit']), 75)
        self.assertIn('INPUT_NOT_READY', diagnostic.getvalue())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_entry_requires_explicit_tool_pins(self):
        with patch.object(driver.r04, 'TracedProcess', side_effect=AssertionError('trace launch')):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(driver.main(['--prepare-stubbed', '--evidence', str(self.root)]), 2)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_target_data_entry_requires_inputs_before_producer_or_native(self):
        with patch.object(driver.roundtrip,'produced_fixture',side_effect=AssertionError('producer before inputs')), \
                patch.object(driver,'checked_packet',side_effect=driver.Refused('INPUT_NOT_READY')), \
                patch.object(driver.r04,'AgySession',side_effect=AssertionError('native entry')):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(driver.main(['--prepare-target-observation','--evidence',str(self.root)]),2)
        self.assertEqual(list(self.root.iterdir()),[])

    def test_modes_cannot_be_combined(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                driver.main(['--native-unit', '--prepare-stubbed'])

    def test_pin_mismatch_cannot_consume_ledger_or_launch(self):
        pins = self.pins()
        pins['python_sha256'] = '0' * 64
        with patch.object(driver.r04, 'TracedProcess', side_effect=AssertionError('trace launch')):
            with self.assertRaisesRegex(driver.Refused, 'pin mismatch'):
                driver.prepare_stubbed(self.root, **pins)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_symlink_and_writable_input_refused(self):
        original = self.root / 'original'
        original.write_bytes(b'pin')
        link = self.root / 'link'
        link.symlink_to(original)
        with self.assertRaises(ValueError):
            driver.checked_pin(link)
        original.chmod(0o666)
        with self.assertRaises(ValueError):
            driver.checked_pin(original)

    def test_evidence_requires_owned_0700_directory(self):
        self.root.chmod(0o755)
        with self.assertRaisesRegex(driver.Refused, '0700'):
            driver.prepare_stubbed(self.root, **self.pins())

    def test_output_custody_refuses_preexisting_bytes(self):
        selected = self.root / 'keep'
        selected.write_bytes(b'do not overwrite')
        with self.assertRaisesRegex(driver.Refused, 'fresh empty'):
            driver.prepare_stubbed(self.root, **self.pins())
        self.assertEqual(selected.read_bytes(), b'do not overwrite')

    def test_ledger_is_one_shot_and_keeps_first_result(self):
        path = self.root / 'stub-ledger.json'
        ledger = driver.reserve_ledger(path, {})
        driver.finish_ledger(ledger, dict(state='failed', kind='PREPARATION_STUBBED'))
        saved = path.read_bytes()
        with self.assertRaises(FileExistsError):
            driver.reserve_ledger(path, {})
        self.assertEqual(path.read_bytes(), saved)

    def test_launch_failure_remains_consumed_without_native_fallback(self):
        with patch.object(driver.r04, 'TracedProcess', side_effect=RuntimeError('retained fixture failure')), \
                patch.object(driver.r04, 'AgySession', side_effect=AssertionError('native fallback')):
            with self.assertRaisesRegex(RuntimeError, 'retained fixture failure'):
                driver.prepare_stubbed(self.root, **self.pins())
        ledger = json.loads((self.root / 'stub-ledger.json').read_bytes())
        self.assertEqual(ledger['state'], 'failed')
        self.assertEqual(ledger['native_runs'], 0)
        with self.assertRaisesRegex(driver.Refused, 'fresh empty'):
            driver.prepare_stubbed(self.root, **self.pins())

    def test_real_stub_trace_has_live_generation_eof_zero_join_and_no_native_claim(self):
        with patch.object(driver.r04, 'AgySession', side_effect=AssertionError('actual native launch')):
            result = driver.prepare_stubbed(self.root, **self.pins())
        self.assertEqual(result['state'], 'passed')
        self.assertEqual(result['parsed_execs'], 1)
        self.assertEqual(result['stdin_bytes'], len(driver.INPUT))
        self.assertEqual(result['interval']['Start'], 0)
        self.assertEqual(result['interval']['End'], Path(result['interval']['Trace']['Path']).stat().st_size)
        self.assertEqual(result['callbacks'], 0)
        self.assertFalse(result['NativePASS'])
        self.assertFalse(result['native_client_executed'])
        self.assertFalse(result['complete_unit_qualified'])
        self.assertEqual(result['refresh'], 'unobserved')
        self.assertEqual(result['native_runs'], 0)
        self.assertTrue(all(pid > 1 and not Path(f'/proc/{pid}').exists() for pid in result['owned_pids']))
        self.assertTrue(all(path.stat().st_mode & 0o077 == 0 for path in self.root.iterdir()))
        generation = json.loads(Path(result['generation']['Path']).read_bytes())
        self.assertEqual(generation['Before'], generation['After'])
        with self.assertRaisesRegex(driver.Refused, 'fresh empty'):
            driver.prepare_stubbed(self.root, **self.pins())


class AuthenticatedPreparationBoundaries(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cairn-agy-auth-test-', dir=Path.home()/'.cache')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_authenticated_entry_requires_sealed_packet_before_pg_or_native(self):
        with patch.object(driver, 'pg_bindir', side_effect=AssertionError('PG before packet')), \
                patch.object(driver.r04, 'AgySession', side_effect=AssertionError('native entry')):
            with contextlib.redirect_stderr(io.StringIO()) as error:
                code = driver.main(['--prepare-authenticated', '--evidence', str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn('INPUT_NOT_READY', error.getvalue())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unknown_input_preserves_consumed_ledger(self):
        ledger = self.root/'preparation-ledger.json'
        handle = driver.reserve_ledger(ledger, {}, kind='AUTHENTICATED_PREPARATION')
        driver.finish_ledger(handle, dict(state='passed', native_runs=0))
        saved = ledger.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                driver.main(['--prepare-authenticated', '--unknown-admission'])
        self.assertEqual(ledger.read_bytes(), saved)

    def test_native_refusal_preserves_existing_auth_ledger(self):
        ledger = self.root/'preparation-ledger.json'
        ledger.write_bytes(b'consumed preparation; native_runs=0\n')
        saved = ledger.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(driver.main(['--native-unit', '--evidence', str(self.root)]), 75)
        self.assertEqual(ledger.read_bytes(), saved)

    def manifest(self):
        return dict(schema='cairn.r06-unit-images/1', source_head=driver.BASE, source_tree=driver.BASE_TREE,
                    images={role:dict(path='/unit/'+role, sha256=str(i)*64)
                            for i, role in enumerate(driver.ROLES, start=1)})

    def test_images_must_name_exact1508_tree(self):
        pins = self.manifest()
        pins['source_tree'] = '71302f4fe158e7d59fa3d5b077df2447af03ad54'
        with self.assertRaisesRegex(driver.Refused, 'exact selected image source tree'):
            driver.validate_image_manifest(pins)

    def test_five_roles_cannot_alias_helper_observer(self):
        pins = self.manifest()
        pins['images']['observer'] = dict(pins['images']['helper'])
        with self.assertRaisesRegex(driver.Refused, 'alias'):
            driver.validate_image_manifest(pins)

    def packet_paths(self, manifest_name):
        paths = [self.root/manifest_name, self.root/'index.json', self.root/'build-complete.json']
        for path in paths:
            path.write_text('{}\n')
            path.chmod(0o600)
        return [item for path in paths for item in (path, driver.roundtrip.sha(path))]

    def test_pending_manifest_is_not_consumable(self):
        with self.assertRaisesRegex(driver.Refused, 'final packet filenames'):
            driver.checked_packet(*self.packet_paths('images-host.pending.json'))

    def test_unsealed_index_is_not_consumable(self):
        with self.assertRaisesRegex(driver.Refused, 'selected sealed image index'):
            driver.checked_packet(*self.packet_paths('images-host.json'))

    def test_privacy_snapshot_never_hashes_credentials_aliases_or_api_home(self):
        credential = self.root/'worker.token'
        credential.write_bytes(b'unit-only credential')
        alias = self.root/'alias'
        os.link(credential, alias)
        api_home = self.root/'api-home'
        api_home.mkdir(mode=0o700)
        (api_home/'identities.json').write_bytes(b'private identity input')
        normal = self.root/'profile.json'
        normal.write_bytes(b'{"inbox_recall_binding":null}\n')
        original = driver.roundtrip.sha
        def privacy_guard(path):
            path = Path(path)
            self.assertFalse(path.is_relative_to(api_home))
            self.assertFalse(os.path.samefile(path, credential))
            return original(path)
        with patch.object(driver.roundtrip, 'sha', side_effect=privacy_guard):
            rows = driver.roundtrip.snapshot_root(self.root, credential, api_home=api_home)
        for path in (credential, alias, api_home/'identities.json'):
            self.assertFalse(rows[str(path)]['content_read'])
            self.assertNotIn('sha256', rows[str(path)])
        self.assertIn('sha256', rows[str(normal)])

    def test_null_profile_pin_must_match_actual_bytes(self):
        path = self.root/'profile.json'
        path.write_text('{"inbox_recall_binding":null}\n')
        pin = dict(Path=str(path), SHA256=driver.roundtrip.sha(path))
        fixture = {role+label:pin for role in ('Memory', 'Config') for label in ('A', 'B')}
        self.assertEqual(len(driver.null_profiles(fixture)), 4)
        path.write_text('{"inbox_recall_binding":"invented"}\n')
        with self.assertRaisesRegex(driver.Refused, 'pin changed'):
            driver.null_profiles(fixture)


class SealedRecipeCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = Path(os.environ['CAIRN_R06_AGY_CONTROLLED_PACKET'])
        cls.pins = json.loads((cls.packet/'images-host.json').read_bytes())
        cls.candidate = json.loads((cls.packet/'candidate.json').read_bytes())
        driver.checked_pin(cls.packet/'images-host.json',
            'e132531f982044e89546e67242f0ae28d7b0d3db05e8fee4f21c8f428feca068')
        driver.checked_pin(cls.packet/'candidate.json',
            '4e14e15f0f0aaa8a43cbdaf36bd396355f42dfdfd748584443b5570b0bb60397')

    def recipe(self, role='helper'):
        return copy.deepcopy(self.candidate['roles'][role]), self.pins['images'][role]

    def test_actual_sealed_recipes_all_five_roles(self):
        for role in driver.ROLES:
            with self.subTest(role=role):
                record, image = self.recipe(role)
                driver.validate_build_recipe(role, record, image, self.pins)

    def test_real_flags_permit_option_order(self):
        for role in ('helper', 'observer'):
            record, image = self.recipe(role)
            argv = record['build']['inner_argv']
            record['build']['inner_argv'] = [argv[0], 'test', '-c', '-o', image['path'],
                                            '-buildvcs=false', '-race', argv[-1]]
            driver.validate_build_recipe(role, record, image, self.pins)

    def test_missing_compile_refuses_execution_only_test(self):
        record, image = self.recipe()
        record['build']['inner_argv'].remove('-c')
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_missing_race_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'].remove('-race')
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_missing_vcs_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'].remove('-buildvcs=false')
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_wrong_output_refused(self):
        record, image = self.recipe()
        argv = record['build']['inner_argv']
        argv[argv.index('-o')+1] = '/wrong/output'
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_wrong_package_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'][-1] = './internal/lifecycle/coordination'
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_duplicate_output_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'][2:2] = ['-o', image['path']]
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_duplicate_required_flag_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'].insert(2, '-race')
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_unapproved_go_identity_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'][0] = '/bin/sh'
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_unknown_build_flag_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'].insert(2, '-run=all')
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_actual_role_alias_refused(self):
        pins = copy.deepcopy(self.pins)
        pins['images']['observer'] = dict(pins['images']['helper'])
        with self.assertRaises(driver.Refused):
            driver.validate_image_manifest(pins)

    def test_controlled_regular_file_still_refuses_0664(self):
        with tempfile.TemporaryDirectory(dir=Path.home()/'.cache') as directory:
            path = Path(directory)/'artifact'
            path.write_bytes(b'unit-only data')
            path.chmod(0o664)
            with self.assertRaises(ValueError):
                driver.checked_pin(path)

    def test_complete_controlled_packet_preflight(self):
        driver.checked_packet(self.packet/'images-host.json',
            'e132531f982044e89546e67242f0ae28d7b0d3db05e8fee4f21c8f428feca068',
            self.packet/'index.json', 'f169590e02f41ba1b9e6578713a7f030a24d0a1f21432bf2a2c118bc200bbeaa',
            self.packet/'build-complete.json', 'ddfcf61f4e63f44378e5009f16b510fa2436914d270dc807dcd865201a55745f')

    def test_historical_packet_cannot_supply_current_native_behavior(self):
        with self.assertRaisesRegex(driver.Refused,'native images lack current Go/embedded source behavior'):
            driver.checked_packet(self.packet/'images-host.json',
                'e132531f982044e89546e67242f0ae28d7b0d3db05e8fee4f21c8f428feca068',
                self.packet/'index.json','f169590e02f41ba1b9e6578713a7f030a24d0a1f21432bf2a2c118bc200bbeaa',
                self.packet/'build-complete.json','ddfcf61f4e63f44378e5009f16b510fa2436914d270dc807dcd865201a55745f',native=True)


class RetainedCollectorCodecTests(unittest.TestCase):
    """Historical bytes only: no /proc, process, source-path replay or currentness."""
    def test_actual_callback_eof_frames_and_pending_native_thread(self):
        source=Path(os.environ['CAIRN_R06_AGY_RETAINED_PREFIX'])
        driver.checked_pin(source,'76a6337a698e36ff0e3fc2de06c82ea14e9a9ebd1fc517fa2819f755caef3d9e')
        observations=Path(os.environ['CAIRN_R06_AGY_RETAINED_OBSERVATIONS'])
        driver.checked_pin(observations,'fc6a499fc2abd608543cf9a0cc7abe6cdccc76c24ed7e6c95d8a460cd417ecd1')
        original=json.loads(observations.read_bytes())[0]
        calls,pending=driver.kernel_calls(source.read_bytes())
        self.assertEqual(len(pending),1);self.assertEqual(pending[0]['pid'],3049)
        self.assertEqual(pending[0]['body'].strip(),'read(0')
        threads=driver.kernel_threads(calls,original['native_pid'])
        self.assertIn(pending[0]['pid'],threads)
        frames=[]
        for row in calls:
            if row['name']=='write' and row['pid'] in threads:
                selected=driver.kernel_hook_frame(row)
                if selected is not None:frames.append(selected[0])
        self.assertEqual(len(frames),2)
        for index,reached in enumerate(original['reached']):
            pid=int(reached['pid'])
            executions=[row for row in calls if row['pid']==pid and row['name']=='execve']
            self.assertEqual(len(executions),2)
            self.assertEqual(driver.kernel_exec(executions[0]),(reached['launcher']['path'],reached['argv']))
            self.assertEqual(driver.kernel_exec(executions[1]),(reached['program']['path'],reached['exec']['argv']))
            callback_threads=driver.kernel_threads([row for row in calls if row['start']>=executions[1]['start']],pid)
            reads=[driver.kernel_io(row)[1] for row in calls if row['pid'] in callback_threads
                and row['name']=='read' and row['start']>executions[1]['start'] and row['body'].startswith('read(0,')]
            self.assertTrue(reads and reads[-1]==b'');self.assertTrue(all(reads[:-1]))
            self.assertEqual(b''.join(reads),frames[index]);self.assertEqual(json.loads(frames[index]),reached['payload'])
            exits=[row for row in calls if row['pid']==pid and row['name']=='exit']
            self.assertEqual([row['body'] for row in exits],['+++ exited with 0 +++'])
        self.assertIs(json.loads(frames[1])['fullyIdle'],True)


def load_tests(loader, standard, pattern):
    # Normal discovery keeps the standing portable controls. This explicit
    # fixture input adds real sealed-recipe controls, with no skipped tests.
    cases=[AgyPreparationTests,AuthenticatedPreparationBoundaries,NativeAdmissionTests,HostConnectAuditTests,
        NativeDriverBoundaries,NativeStateSchemaTests,LivePreservationTests,TraceBoundaryTests,
        RehearsalTerminalTests,ScaleSelectionTests,SourceAdapterTests,UnitScaleShapeTests]
    if os.environ.get('CAIRN_R06_REHEARSAL_SELECTION'):
        cases.append(FullSequenceRehearsalTests)
    if os.environ.get('CAIRN_R06_AGY_CONTROLLED_PACKET'):
        cases.append(SealedRecipeCompatibilityTests)
    if os.environ.get('CAIRN_R06_AGY_RETAINED_PREFIX'):
        cases.append(RetainedCollectorCodecTests)
    return unittest.TestSuite(loader.loadTestsFromTestCase(case) for case in cases)


if __name__ == '__main__':
    unittest.main()
