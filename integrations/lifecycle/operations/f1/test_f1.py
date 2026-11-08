import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
FIXTURE_ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(FIXTURE_ROOT/'collector-code'))
import collector as C
import parsers as P
import read_process as R
sys.path.insert(0,str(FIXTURE_ROOT/'helper-code'))
import metadata_helper as M

# Synthetic image bindings exist only in this isolated fixture module.
M.EXPECTED_IMAGES={'api':'a'*64,'daemon':'b'*64,'postgres':'c'*64}
NS={k:k+':[1]' for k in ('pid','net','mnt','time')}
BIND=dict(C.BINDING,api_image_sha256='a'*64,daemon_image_sha256='b'*64,postgres_image_sha256='c'*64)
ROUTE={'TCP':{'8789':{'HTTPS':True}},'Web':{P.ORIGIN:{'Handlers':{'/':{'Proxy':P.BACKEND}}}}}
DIRECTORY={'schema':'cairn.response/1','ok':True,'status':'OK','data':{'agents':[{'agent_id':'11111111-1111-4111-8111-111111111111','execution_id':'22222222-2222-4222-8222-222222222222','machine_id':'archon','database_generation':1,'metadata':{'task':'SYNTHETIC_PRIVATE'}}],'more':True,'next_after':1}}
CLIENTS={'schema':'cairn.response/1','ok':True,'status':'OK','data':{'schema':'cairn.clients/1','storage':'volatile','observation_epoch':'33333333-3333-4333-8333-333333333333','started_at':'2026-11-08T00:00:00Z','observed_at':'2026-11-08T01:00:00Z','retention_seconds':86400,'server':{'schema':'cairn.build/1'},'exhaustive':False,'partial':True,'truncated':True,'wall_clock_regressed':False,'returned':1,'eligible_rows':2,'counters':{'evicted_cohorts':0,'invalid_declarations':0},'rows':[{'principal':'hosted-agent','machine_id':'root-central','reported':{'origin':'SYNTHETIC_PRIVATE'}}]}}
CLIENTS['data']['server']={'schema':'cairn.build/1','go_version':'go1.27.3','vcs_modified':None}
CLIENTS['data']['rows'][0].update(cohort_id='44444444-4444-4444-8444-444444444444',first_observed_at='2026-11-08T00:00:00Z',last_observed_at='2026-11-08T01:00:00Z',metadata_state='present',origin_state='reported',reported={'schema':'cairn.client-diagnostics/1','surface':'cli','harness':'codex','transport_build':dict(CLIENTS['data']['server']),'origin':{'component':'lifecycle-memory','basis':'reported','implementation_id':'SYNTHETIC_PRIVATE'}})
def raw(v):return json.dumps(v).encode()
def proc(pid,uid,sha,parent=1):return {'pid':pid,'ppid':parent,'uid':uid,'start_ticks':pid*10,'exe':'/synthetic/image','image_sha256':sha*64,'namespaces':dict(NS)}
UNIT_API=b'Id=cairn-api.service\nMainPID=200\nNRestarts=0\nActiveState=active\nSubState=running\n'
UNIT_TS=UNIT_API.replace(b'cairn-api',b'tailscaled').replace(b'200',b'300')
UNIX=(b'u_str ESTAB 0 0 * 11 /var/run/postgresql/.s.PGSQL.5432 21 users:(("a",pid=200,fd=6))\n'
      b'u_str ESTAB 0 0 /var/run/postgresql/.s.PGSQL.5432 21 * 11 users:(("p",pid=401,fd=6))\n')
LISTEN=(f'u_str LISTEN 0 128 {P.API_SOCKET} 10 * 0 users:(("a",pid=200,fd=5))\n'
        f'u_str LISTEN 0 128 {P.TS_SOCKET} 30 * 0 users:(("t",pid=300,fd=5))\n'
        'u_str LISTEN 0 128 /var/run/postgresql/.s.PGSQL.5432 20 * 0 users:(("p",pid=400,fd=5))\n').encode()
TCP=b'LISTEN 0 128 127.0.0.1:18889 0.0.0.0:* users:(("a",pid=200,fd=9))\n'
CONN=b'ESTAB 0 0 127.0.0.1:18889 127.0.0.1:55555 users:(("a",pid=200,fd=10))\n'

def receipt(kind):return {'class':kind,'pid':500,'start_ticks':5000,'begin_mono':0.0,'end_mono':0.1,'outcome':'COMPLETE_ZERO','returncode':0,'stdout_bytes':10,'stderr_bytes':0,'eof':True}
class View:
 def observer_namespaces(self):return dict(NS)
 def __init__(self):
  self.procs={200:proc(200,1000,'a'),300:proc(300,0,'b'),400:proc(400,112,'c'),401:proc(401,112,'c',400)}
  self.fd={200:{10:[5],11:[6]},300:{30:[5]},400:{20:[5]},401:{21:[6]}}
 def process(self,pid):return copy.deepcopy(self.procs[pid])
 def fds(self,pid):return copy.deepcopy(self.fd[pid])
 def socket(self,path):return {'dev':1,'inode':2 if path==P.API_SOCKET else 3,'uid':1000 if path==P.API_SOCKET else 0,'mode':0o600 if path==P.API_SOCKET else 0o666}
 def journal(self):
  return {'socket':{'dev':1,'inode':40,'uid':0,'mode':0o666},'parents':[{'path':path,'dev':1,'inode':index+100,'uid':0,'mode':0o755} for index,path in enumerate(M.JOURNAL_PARENTS)]}
class HelperReader:
 def __init__(self):self.receipts=[];self.data=dict(zip(M.SS,[UNIX,LISTEN,TCP,CONN]))
 def run(self,kind,argv,body,deadline,cap):self.receipts.append(receipt(kind));return self.data[tuple(argv)]
def meta():return M.observe(200,300,HelperReader(),View(),100)
class Adapter:
 def __init__(self):self.receipts=[];self.calls=[];self.t=0.;self.change={};self.pin_calls=0
 def now(self):return self.t
 def pins(self):self.pin_calls+=1
 def run(self,kind,argv,body,deadline):
  self.calls.append((kind,tuple(argv),body));self.t+=.1;self.receipts.append(receipt(kind))
  data={'api-unit-before':UNIT_API,'api-unit-after':UNIT_API,'daemon-unit-before':UNIT_TS,'daemon-unit-after':UNIT_TS,
   'metadata-before':raw(meta()),'metadata-after':raw(meta()),'route-before':raw(ROUTE),'route-after':raw(ROUTE),'directory':raw(DIRECTORY),'clients':raw(CLIENTS)}
  value=self.change.get(kind,data[kind])
  if isinstance(value,Exception):raise value
  return value

class Parsing(unittest.TestCase):
 def reject(self,fn,value):
  with self.assertRaises(P.Unknown):fn(value)
 def test_route(self):self.assertEqual(P.tailscale(raw(ROUTE))['backend'],P.BACKEND)
 def test_duplicate(self):self.reject(P.tailscale,b'{"TCP":{},"TCP":{}}')
 def test_null(self):self.reject(P.tailscale,b'null')
 def test_utf8(self):self.reject(P.tailscale,b'\xff')
 def test_surrogate(self):self.reject(P.decode,b'{"x":"\\ud800"}')
 def test_nan(self):self.reject(P.decode,b'{"x":NaN}')
 def test_overflow(self):self.reject(P.decode,b' ' * 65537)
 def test_deep(self):self.reject(P.decode,b'['*100+b'0'+b']'*100)
 def test_service(self):
  v=copy.deepcopy(ROUTE);v['Services']={'svc:x':{}};self.reject(P.tailscale,raw(v))
 def test_foreground(self):
  v=copy.deepcopy(ROUTE);v['Foreground']={'x':{}};self.reject(P.tailscale,raw(v))
 def test_other_host_same_port(self):
  v=copy.deepcopy(ROUTE);v['Web']['other:8789']={};self.reject(P.tailscale,raw(v))
 def test_mount(self):
  v=copy.deepcopy(ROUTE);v['Web'][P.ORIGIN]['Handlers']['/x']={};self.reject(P.tailscale,raw(v))
 def test_proxy_credentials(self):
  v=copy.deepcopy(ROUTE);v['Web'][P.ORIGIN]['Handlers']['/']['Proxy']='http://secret:secret@127.0.0.1:18889';self.reject(P.tailscale,raw(v))
 def test_text_not_proxy(self):
  v=copy.deepcopy(ROUTE);v['Web'][P.ORIGIN]['Handlers']['/']={'Text':'PRIVATE'};self.reject(P.tailscale,raw(v))
 def test_changed_backend(self):
  v=copy.deepcopy(ROUTE);v['Web'][P.ORIGIN]['Handlers']['/']['Proxy']='http://127.0.0.1:9';self.reject(P.tailscale,raw(v))
 def test_wrong_https_type(self):
  v=copy.deepcopy(ROUTE);v['TCP']['8789']['HTTPS']=1;self.reject(P.tailscale,raw(v))
 def test_funnel(self):
  v=copy.deepcopy(ROUTE);v['AllowFunnel']={P.ORIGIN:True};self.reject(P.tailscale,raw(v))
 def test_unselected_private_omitted(self):
  v=copy.deepcopy(ROUTE);v['Web']['other:443']={'Handlers':{'/':{'Text':'PRIVATE_SENTINEL'}}};self.assertNotIn('PRIVATE',json.dumps(P.tailscale(raw(v))))
 def test_directory_projection(self):self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(P.directory(raw(DIRECTORY))))
 def test_forged_reported_machine(self):
  v=copy.deepcopy(DIRECTORY);v['data']['agents'][0].update(machine_id='other',metadata={'machine_id':'archon','principal':'machine:archon/agent'});self.reject(P.directory,raw(v))
 def test_forged_reported_authority(self):
  v=copy.deepcopy(DIRECTORY);v['data']['agents'][0]['metadata']={'principal':'machine:archon/agent','authenticated':True};self.assertEqual(P.directory(raw(v)),P.directory(raw(DIRECTORY)))
 def test_empty_directory(self):
  v=copy.deepcopy(DIRECTORY);v['data']['agents']=[];self.reject(P.directory,raw(v))
 def test_directory_wrong_envelope(self):self.reject(P.directory,raw({'ok':True,'result':DIRECTORY['data']}))
 def test_directory_refusal(self):
  v=copy.deepcopy(DIRECTORY);v['ok']=False;v['message']='PRIVATE';self.reject(P.directory,raw(v))
 def test_directory_generation_not_store_id(self):self.assertIn('not exhaustive',P.directory(raw(DIRECTORY))['scope'])
 def test_unit(self):self.assertEqual(P.unit(UNIT_API,'cairn-api.service')['pid'],200)
 def test_unit_duplicate(self):self.reject(lambda r:P.unit(r,'cairn-api.service'),UNIT_API+b'MainPID=400\n')
 def test_unit_inactive(self):self.reject(lambda r:P.unit(r,'cairn-api.service'),UNIT_API.replace(b'=active',b'=inactive'))
 def test_unit_missing(self):self.reject(lambda r:P.unit(r,'cairn-api.service'),b'MainPID=200\n')
 def test_proc_zombie(self):self.reject(lambda r:P.proc_stat(r,200),b'200 (x) Z '+b'0 '*30)
 def test_tcp_unknown(self):self.reject(lambda r:P.tcp_connections(r,200),CONN.replace(b'127.0.0.1:55555',b'8.8.8.8:5432'))

class DirectoryGeneration(unittest.TestCase):
 def payload(self,value):
  v=copy.deepcopy(DIRECTORY);v['data']['agents'][0]['database_generation']=value;return raw(v)
 def accepted(self,value):
  projected=P.directory(self.payload(value));self.assertEqual(projected['database_generation'],value)
  self.assertIs(type(projected['database_generation']),int);self.assertIn('not exhaustive',projected['scope'])
 def rejected(self,value):
  with self.assertRaises(P.Unknown) as raised:P.directory(self.payload(value))
  self.assertEqual(str(raised.exception),'DIRECTORY_GENERATION')
 def test_zero(self):self.accepted(0)
 def test_positive(self):self.accepted(1)
 def test_int64_max(self):self.accepted((1 << 63)-1)
 def test_negative(self):
  for value in (-1,-(1 << 63),-(1 << 63)-1):
   with self.subTest(value=value):self.rejected(value)
 def test_above_int64_max(self):
  for value in (1 << 63,1 << 64):
   with self.subTest(value=value):self.rejected(value)
 def test_bool(self):
  for value in (False,True):
   with self.subTest(value=value):self.rejected(value)
 def test_missing(self):
  v=copy.deepcopy(DIRECTORY);del v['data']['agents'][0]['database_generation']
  with self.assertRaises(P.Unknown) as raised:P.directory(raw(v))
  self.assertEqual(str(raised.exception),'DIRECTORY_GENERATION')
 def test_null(self):self.rejected(None)
 def test_string(self):
  for value in ('0','1','9223372036854775807','PRIVATE_SENTINEL'):
   with self.subTest(value=value):self.rejected(value)
 def test_float(self):
  for value in (0.0,1.0,0.5):
   with self.subTest(value=value):self.rejected(value)
 def test_compound(self):
  for value in ([],{},[0],{'value':0}):
   with self.subTest(value=value):self.rejected(value)
 def test_zero_full_fake_collection(self):
  a=Adapter();a.change['directory']=self.payload(0);result=C.collect(a,BIND)
  self.assertEqual(result['status'],'OBSERVED_COOPERATIVE_RELATIONSHIP_CANDIDATE')
  self.assertEqual(result['claims']['directory_sample']['database_generation'],0)
  self.assertEqual(result['f1_acceptance'],'NOT_ASSESSED');self.assertEqual(result['readiness'],'NOT_ASSESSED')
  self.assertEqual(len(a.calls),10)

class Metadata(unittest.TestCase):
 def test_complete(self):self.assertEqual(len(meta()['postgres_pairs']),1)
 def test_inaccessible(self):
  view=View();view.fds=lambda pid:(_ for _ in ()).throw(PermissionError('PRIVATE'))
  with self.assertRaises(PermissionError):M.observe(200,300,HelperReader(),view,100)
 def test_helper_rejects_wrong_selected_image(self):
  view=View();view.procs[300]['image_sha256']='d'*64
  with self.assertRaises(P.Unknown):M.observe(200,300,HelperReader(),view,100)
 def test_control_not_owned(self):
  view=View();view.fd[300]={}
  with self.assertRaises(P.Unknown):M.observe(200,300,HelperReader(),view,100)
 def test_wrong_pg_parent(self):
  view=View();view.procs[401]['ppid']=999
  with self.assertRaises(P.Unknown):M.observe(200,300,HelperReader(),view,100)
 def test_time_namespace(self):
  view=View();view.procs[401]['namespaces']['time']='time:[2]'
  with self.assertRaises(P.Unknown):M.observe(200,300,HelperReader(),view,100)
 def test_mount_namespace_not_new_gate(self):
  view=View();view.procs[401]['namespaces']['mnt']='mnt:[2]';self.assertTrue(M.observe(200,300,HelperReader(),view,100)['postgres_pairs'])
 def test_bad_peer(self):
  reader=HelperReader();reader.data[M.SS[0]]=UNIX.replace(b'* 11 users:(("p"',b'* 999 users:(("p"')
  with self.assertRaises(P.Unknown):M.observe(200,300,reader,View(),100)
 def test_process_race(self):
  view=View();old=view.process;count=[0]
  def raced(pid):
   count[0]+=1;r=old(pid)
   if count[0]>4 and pid==200:r['start_ticks']+=1
   return r
  view.process=raced
  with self.assertRaises(P.Unknown):M.observe(200,300,HelperReader(),view,100)
 def test_pg_image_mismatch(self):
  v=meta();v['pg_backends'][0]['image_sha256']='d'*64
  with self.assertRaises(P.Unknown):C.metadata(raw(v),{'pid':200},{'pid':300},BIND)
 def test_helper_unknown_children(self):
  v=meta();v['children'][0]['outcome']='TIMEOUT_UNKNOWN'
  with self.assertRaises(P.Unknown):C.metadata(raw(v),{'pid':200},{'pid':300},BIND)

class Orchestration(unittest.TestCase):
 def test_selected_literal_commands(self):
  self.assertEqual(C.TS,('/usr/bin/tailscale','--socket=/var/run/tailscale/tailscaled.sock','serve','status','--json'))
  self.assertEqual(C.CLIENTS,('/home/halbritt/.local/bin/cairn','agent','--socket','/home/halbritt/.local/share/cairn/api.sock','--token-file','/home/halbritt/.local/share/cairn/hosted-agent.token','clients','--limit','1'))
  self.assertEqual(M.SS,(('/usr/bin/ss','-xnpH'),('/usr/bin/ss','-xlnpH'),('/usr/bin/ss','-ntlpH'),('/usr/bin/ss','-ntpH')))
 def test_complete_exact_argv(self):
  a=Adapter();v=C.collect(a,BIND)
  self.assertEqual(v['status'],'OBSERVED_COOPERATIVE_RELATIONSHIP_CANDIDATE');self.assertEqual(v['f1_acceptance'],'NOT_ASSESSED')
  self.assertEqual(len(a.calls),10);self.assertEqual(a.calls[3][1],C.TS);self.assertIn('--socket=/var/run/tailscale/tailscaled.sock',C.TS)
  self.assertEqual(a.calls[4],('directory',C.DIRECTORY,C.BODY));self.assertEqual(a.calls[5],('clients',C.CLIENTS,b''));self.assertEqual(len(v['helper_children']),8)
 def test_disabled_constructor_no_reads(self):
  with patch.object(C,'checked_image',side_effect=AssertionError('READ')):
   with self.assertRaises(P.Unknown):C.HostReads()
 def test_disabled_entries(self):self.assertFalse(C.ENACTED);self.assertFalse(M.ENACTED);self.assertIsNone(C.OUTPUT_ROOT)
 def test_helper_exact_argv(self):
  obj=C.HostReads.__new__(C.HostReads);a=Adapter();C.collect(a,BIND)
  self.assertTrue(obj._helper_argv(a.calls[2][1]));self.assertFalse(obj._helper_argv(['/bin/sh','-c','x']))
 def test_changed_route(self):
  a=Adapter();v=copy.deepcopy(ROUTE);v['Foreground']={'x':{}};a.change['route-after']=raw(v)
  self.assertEqual(C.collect(a,BIND)['status'],'UNKNOWN')
 def test_api_restart(self):
  a=Adapter();a.change['api-unit-after']=UNIT_API.replace(b'NRestarts=0',b'NRestarts=1');self.assertEqual(C.collect(a,BIND)['reason'],'IDENTITY_CHANGED')
 def test_endpoint_race(self):
  a=Adapter();v=meta();v['control_socket']['inode']+=1;a.change['metadata-after']=raw(v);self.assertEqual(C.collect(a,BIND)['reason'],'ENDPOINT_CHANGED')
 def test_timeout_no_retry(self):
  a=Adapter();a.change['route-before']=P.Unknown('READ_TIMEOUT');r=C.collect(a,BIND);self.assertEqual(r['status'],'UNKNOWN');self.assertEqual(len(a.calls),4)
 def test_secret_failure_sanitized(self):
  a=Adapter();a.change['directory']=OSError('SECRET_BODY');r=C.collect(a,BIND);self.assertNotIn('SECRET_BODY',json.dumps(r));self.assertEqual(r['status'],'UNKNOWN')
 def test_local_map_unavailable_stops(self):
  a=Adapter();a.change['clients']=b'{}';r=C.collect(a,BIND);self.assertEqual(r['status'],'UNKNOWN');self.assertEqual(len(a.calls),6)
 def test_missing_binding(self):
  a=Adapter();r=C.collect(a,dict(BIND,api_image_sha256=None));self.assertEqual(r['status'],'UNKNOWN')
 def test_whole_read_overrun(self):
  a=Adapter();old=a.run
  def slow(*args):r=old(*args);a.t+=2.01;return r
  a.run=slow;r=C.collect(a,BIND);self.assertEqual(r['reason'],'COMPOSITE_READ_OVERRUN');self.assertEqual(len(a.calls),1)
 def test_helper_failure_retains_child(self):
  a=Adapter();v={'schema':'cairn.F1-metadata/2','status':'UNKNOWN','children':[dict(receipt('unix-connected'),outcome='TIMEOUT_UNKNOWN',returncode=None,eof=False)]};a.change['metadata-before']=raw(v)
  r=C.collect(a,BIND);self.assertEqual(r['status'],'UNKNOWN');self.assertEqual(r['helper_children'][0]['outcome'],'TIMEOUT_UNKNOWN')

class ProcFixtures(unittest.TestCase):
 def test_fd_bound(self):
  class Entries:
   def __enter__(self):return iter(type('Entry',(),{'name':str(i)})() for i in range(M.MAX_FDS+1))
   def __exit__(self,*args):pass
  with patch.object(M.os,'scandir',lambda path:Entries()),patch.object(M.os,'readlink',side_effect=AssertionError('UNREACHED')):
   with self.assertRaises(P.Unknown):M.ProcView().fds(200)
 def test_fd_missing_visibility(self):
  with patch.object(M.os,'scandir',side_effect=PermissionError('PRIVATE')):
   with self.assertRaises(PermissionError):M.ProcView().fds(200)
 def test_deleted_image(self):
  stat=(b'200 (x) S 1 '+b'0 '*17+b'2000 0')
  with patch.object(M,'read_regular',lambda *a:stat),patch.object(M.os,'readlink',return_value='/image (deleted)'):
   with self.assertRaises(P.Unknown):M.ProcView().process(200)
 def test_socket_link_refused(self):
  info=type('Info',(),{'st_mode':0o120777})()
  with patch.object(M.os,'lstat',return_value=info):
   with self.assertRaises(P.Unknown):M.ProcView().socket(P.TS_SOCKET)
 def test_regular_read_bound(self):
  p=Path('/tmp/read-bound');p.write_bytes(b'x'*20)
  with self.assertRaises(P.Unknown):M.read_regular(p,10)
  p.unlink()
 def test_regular_read_exact(self):
  p=Path('/tmp/read-exact');p.write_bytes(b'x'*10);self.assertEqual(M.read_regular(p,10),b'x'*10);p.unlink()

class OpusCorrections(unittest.TestCase):
 def journal_case(self,holders=(700,),fds=(1,2)):
  view=View();view.fd[200][12]=list(fds);reader=HelperReader()
  users=(' users:(('+','.join('"j",pid='+str(pid)+',fd=8' for pid in holders)+'))') if holders else ''
  reader.data[M.SS[0]]+=('u_str ESTAB 0 0 * 12 /run/systemd/journal/stdout 42 users:(("a",pid=200,fd=1))\n'+'u_str ESTAB 0 0 /run/systemd/journal/stdout 42 * 12'+users+'\n').encode()
  return reader,view
 def test_journal_holder_shapes_not_authority(self):
  for holders in ((700,),(700,1),(1,700),(999,),()):
   with self.subTest(holders=holders):
    reader,view=self.journal_case(holders);value=M.observe(200,300,reader,view,100)
    self.assertEqual(value['journal_endpoint'],view.journal());self.assertEqual(len(value['postgres_pairs']),1)
 def test_journal_bad_holder_shape_still_requires_stdio(self):
  for holders in ((700,),(700,1),(1,700),(999,),()):
   for fds in ((3,),(1,3),()):
    with self.subTest(holders=holders,fds=fds):
     reader,view=self.journal_case(holders,fds)
     with self.assertRaises(P.Unknown):M.observe(200,300,reader,view,100)
 def test_journal_wrong_path(self):
  reader,view=self.journal_case();reader.data[M.SS[0]]=reader.data[M.SS[0]].replace(b'/run/systemd/journal/stdout',b'/tmp/journal')
  with self.assertRaises(P.Unknown):M.observe(200,300,reader,view,100)
 def test_journal_bad_reciprocity(self):
  reader,view=self.journal_case();reader.data[M.SS[0]]=reader.data[M.SS[0]].replace(b'42 * 12',b'42 * 99')
  with self.assertRaises(P.Unknown):M.observe(200,300,reader,view,100)
 def test_journal_endpoint_change(self):
  reader,view=self.journal_case();original=view.journal;calls=[]
  def changed():
   value=original();calls.append(1)
   if len(calls)>1:value['socket']['inode']+=1
   return value
  view.journal=changed
  with self.assertRaisesRegex(P.Unknown,'JOURNAL_RACED'):M.observe(200,300,reader,view,100)
 def journal_stat(self,path):
  return type('Info',(),{'st_mode':(0o140666 if path==M.JOURNAL_PATH else 0o040755),'st_uid':0,'st_dev':1,'st_ino':40 if path==M.JOURNAL_PATH else 100})()
 def test_actual_journal_guard_fixed_paths(self):
  seen=[]
  def lookup(path):seen.append(path);return self.journal_stat(path)
  with patch.object(M.os,'lstat',lookup):value=M.ProcView().journal()
  self.assertEqual(seen,list(M.JOURNAL_PARENTS)+[M.JOURNAL_PATH]);self.assertEqual(value['socket']['uid'],0)
 def test_actual_journal_unowned_or_mutable_parents(self):
  for field,value in [('st_uid',1000),('st_mode',0o040777),('st_mode',0o120777)]:
   for path in M.JOURNAL_PARENTS:
    with self.subTest(field=field,value=value,path=path):
     def lookup(target):
      info=self.journal_stat(target)
      if target==path:setattr(info,field,value)
      return info
     with patch.object(M.os,'lstat',lookup):
      with self.assertRaises(P.Unknown):M.ProcView().journal()
 def test_actual_journal_unowned_or_not_socket(self):
  for field,value in [('st_uid',1000),('st_mode',0o100644),('st_mode',0o120777)]:
   def lookup(path):
    info=self.journal_stat(path)
    if path==M.JOURNAL_PATH:setattr(info,field,value)
    return info
   with patch.object(M.os,'lstat',lookup):
    with self.assertRaises(P.Unknown):M.ProcView().journal()
 def test_control_both_spellings_same_object(self):
  for path in M.CONTROL_PATHS:
   reader=HelperReader();reader.data[M.SS[1]]=LISTEN.replace(P.TS_SOCKET.encode(),path.encode())
   value=M.observe(200,300,reader,View(),100)
   self.assertEqual(value['control_listener']['path'],P.TS_SOCKET);self.assertEqual(value['control_listener']['observed_path'],path)
 def test_control_alias_identity_or_owner_mismatch(self):
  for key,value in [('dev',2),('inode',99),('uid',1000)]:
   view=View();original=view.socket
   def changed(path):
    row=original(path)
    if path==M.CONTROL_PATHS[1]:row[key]=value
    return row
   view.socket=changed
   with self.assertRaises(P.Unknown):M.observe(200,300,HelperReader(),view,100)
 def test_control_alias_missing(self):
  view=View();original=view.socket
  def missing(path):
   if path==M.CONTROL_PATHS[1]:raise FileNotFoundError('PRIVATE')
   return original(path)
  view.socket=missing
  with self.assertRaises(FileNotFoundError):M.observe(200,300,HelperReader(),view,100)
 def test_control_second_listener(self):
  reader=HelperReader();reader.data[M.SS[1]]+=b'u_str LISTEN 0 128 /run/tailscale/tailscaled.sock 31 * 0 users:(("t",pid=300,fd=6))\n'
  with self.assertRaises(P.Unknown):M.observe(200,300,reader,View(),100)
 def test_control_wrong_listener_pid_or_state(self):
  for old,new in [(b'pid=300',b'pid=301'),(b'u_str LISTEN 0 128 '+P.TS_SOCKET.encode(),b'u_str ESTAB 0 128 '+P.TS_SOCKET.encode())]:
   reader=HelperReader();reader.data[M.SS[1]]=LISTEN.replace(old,new)
   with self.assertRaises(P.Unknown):M.observe(200,300,reader,View(),100)
 def test_control_alias_race(self):
  view=View();original=view.socket;seen=[]
  def changed(path):
   value=original(path)
   if path==M.CONTROL_PATHS[1]:
    seen.append(1)
    if len(seen)>1:value['inode']+=1
   return value
  view.socket=changed
  with self.assertRaises(P.Unknown):M.observe(200,300,HelperReader(),view,100)
 def test_late_unseen_api_socket_is_unknown(self):
  reader=HelperReader();reader.data[M.SS[0]]+=b'u_str ESTAB 0 0 * 999 /tmp/unknown 888 users:(("a",pid=200,fd=99))\n'
  with self.assertRaisesRegex(P.Unknown,'API_FD_VISIBILITY_UNKNOWN'):M.observe(200,300,reader,View(),100)
 def test_late_visible_unknown_peer_is_unknown(self):
  reader=HelperReader();reader.data[M.SS[0]]+=b'u_str ESTAB 0 0 * 999 /tmp/unknown 888 users:(("a",pid=200,fd=99))\n'
  view=View();view.fd[200][999]=[99]
  with self.assertRaisesRegex(P.Unknown,'API_UNIX_PEER_UNKNOWN'):M.observe(200,300,reader,view,100)
 def test_api_accepted_exemption_precedes_fd_requirement(self):
  reader=HelperReader();reader.data[M.SS[0]]+=('u_str ESTAB 0 0 '+P.API_SOCKET+' 999 * 888 users:(("a",pid=200,fd=99))\n').encode()
  self.assertEqual(len(M.observe(200,300,reader,View(),100)['postgres_pairs']),1)
 def test_fd_inventory_after_all_ss_allows_valid_late_pg(self):
  reader=HelperReader();view=View();view.fd[200].pop(11);original=reader.run;seen=[];fd_original=view.fds
  def run(*args):
   value=original(*args)
   if args[0]=='tcp-connections':view.fd[200][11]=[6]
   return value
  def fds(pid):
   if pid==200:seen.append(len(reader.receipts))
   return fd_original(pid)
  reader.run=run;view.fds=fds;value=M.observe(200,300,reader,view,100)
  self.assertEqual(seen,[4]);self.assertEqual(value['postgres_pairs'][0]['api_inode'],11)
 def test_parent_fsync_before_intent(self):
  events=[]
  class Output:
   parent='/synthetic-parent'
   def mkdir(self,**kw):events.append(('mkdir',kw))
   def __truediv__(self,name):return '/synthetic-output/'+name
  with patch.object(C.os,'open',lambda *a:(events.append(('open',a)),50)[1]),patch.object(C.os,'fsync',lambda fd:events.append(('fsync',fd))),patch.object(C.os,'close',lambda fd:events.append(('close',fd))),patch.object(C,'durable',lambda *a:events.append(('intent',a))):C.prepare_output(Output())
  self.assertEqual([e[0] for e in events],['mkdir','open','fsync','close','intent'])
  self.assertEqual(events[1][1][0],'/synthetic-parent');self.assertEqual(events[0][1],{'mode':0o700})
 def test_parent_fsync_failure_prevents_intent_or_reads(self):
  events=[]
  class Output:
   parent='/synthetic-parent'
   def mkdir(self,**kw):events.append('mkdir')
  with patch.object(C.os,'open',return_value=50),patch.object(C.os,'fsync',side_effect=OSError('PRIVATE')),patch.object(C.os,'close',lambda fd:events.append('close')),patch.object(C,'durable',side_effect=AssertionError('INTENT')),patch.object(C,'HostReads',side_effect=AssertionError('READ')):
   with self.assertRaises(OSError):C.prepare_output(Output())
  self.assertEqual(events,['mkdir','close'])
 def test_exclusive_output_replay_refusal(self):
  import tempfile
  with tempfile.TemporaryDirectory(dir='/tmp') as directory:
   output=Path(directory)/'output';C.prepare_output(output)
   self.assertTrue((output/'intent.json').is_file())
   with self.assertRaises(FileExistsError):C.prepare_output(output)
 def test_durable_final_exclusive(self):
  import tempfile
  with tempfile.TemporaryDirectory(dir='/tmp') as directory:
   path=Path(directory)/'final.json';C.durable(path,{'status':'UNKNOWN'})
   self.assertEqual(json.loads(path.read_text()),{'status':'UNKNOWN'})
   with self.assertRaises(FileExistsError):C.durable(path,{'status':'UNKNOWN'})
 def test_closure_cache_absence_then_present_rejects(self):
  import tempfile
  for module in (C,M):
   with tempfile.TemporaryDirectory(dir='/tmp') as directory:
    module.require_no_pycache(directory);cache=Path(directory)/'__pycache__';cache.mkdir()
    with self.assertRaisesRegex(RuntimeError,'CODE_CACHE_PRESENT'):module.require_no_pycache(directory)
    self.assertTrue(cache.is_dir())
 def test_cache_file_or_dangling_symlink_rejected(self):
  import tempfile
  for module in (C,M):
   for kind in ('file','link'):
    with tempfile.TemporaryDirectory(dir='/tmp') as directory:
     cache=Path(directory)/'__pycache__'
     if kind=='file':cache.write_bytes(b'x')
     else:cache.symlink_to('absent')
     with self.assertRaises(RuntimeError):module.require_no_pycache(directory)
     self.assertTrue(cache.is_symlink() or cache.is_file())
 def test_cache_inaccessible_unknown(self):
  for module in (C,M):
   with patch.object(module.os,'lstat',side_effect=PermissionError('PRIVATE')):
    with self.assertRaisesRegex(RuntimeError,'CODE_CACHE_VISIBILITY_UNKNOWN'):module.require_no_pycache('/synthetic')
 def test_pin_checks_fixed_cache_paths_before_images(self):
  paths=[]
  with patch.object(C,'require_no_pycache',lambda path:paths.append(str(path))),patch.object(C,'require_code_closure',lambda *args:None),patch.object(C,'checked_image',side_effect=RuntimeError('END')):
   with self.assertRaisesRegex(RuntimeError,'END'):C.HostReads.__new__(C.HostReads).pins()
  self.assertEqual(paths,[str(C.ROOT),str(Path(C.HELPER).parent)])
 def test_cache_checks_precede_local_imports(self):
  for module in (C,M):
   text=Path(module.__file__).read_text();self.assertLess(text.index('require_no_pycache(Path(__file__)'),text.index('from parsers import'))
 def test_endpoint_proof_retained_in_final_projection(self):
  value=C.collect(Adapter(),BIND)
  self.assertEqual(value['claims']['control_aliases'],meta()['control_aliases'])
  self.assertIn('journal_endpoint',value['claims']);self.assertIsNone(value['claims']['journal_endpoint'])
 def test_new_endpoint_fields_compared_across_helper_reads(self):
  for key in ('control_aliases','journal_endpoint'):
   adapter=Adapter();value=meta();value[key]={'changed':True};adapter.change['metadata-after']=raw(value)
   self.assertEqual(C.collect(adapter,BIND)['reason'],'ENDPOINT_CHANGED')

class UnattributedApiRows(unittest.TestCase):
 def unsupported(self,attribution=b''):
  view=View();view.fd[200][777]=[7];reader=HelperReader()
  reader.data[M.SS[0]]+=b'u_str ESTAB 0 0 * 777 /tmp/unsupported 778'+attribution+b'\n'
  reader.data[M.SS[0]]+=b'u_str ESTAB 0 0 /tmp/unsupported 778 * 777 users:(("x",pid=999,fd=7))\n'
  return reader,view
 def test_api_held_no_attribution_unsupported_refuses(self):
  reader,view=self.unsupported()
  with self.assertRaisesRegex(P.Unknown,'API_UNIX_PEER_UNKNOWN'):M.observe(200,300,reader,view,100)
 def test_api_held_other_pid_attribution_still_refuses(self):
  reader,view=self.unsupported(b' users:(("x",pid=999,fd=7))')
  with self.assertRaisesRegex(P.Unknown,'API_UNIX_PEER_UNKNOWN'):M.observe(200,300,reader,view,100)
 def test_unattributed_valid_pg_fully_proved(self):
  reader=HelperReader();reader.data[M.SS[0]]=UNIX.replace(b' users:(("a",pid=200,fd=6))',b'')
  value=M.observe(200,300,reader,View(),100)
  self.assertEqual(value['postgres_pairs'],[{'api_inode':11,'pg_inode':21,'pg_pid':401,'pg_path':'/var/run/postgresql/.s.PGSQL.5432'}])
 def test_unattributed_pg_still_requires_reciprocity(self):
  reader=HelperReader();reader.data[M.SS[0]]=UNIX.replace(b' users:(("a",pid=200,fd=6))',b'').replace(b'21 * 11',b'21 * 99')
  with self.assertRaisesRegex(P.Unknown,'API_UNIX_PEER_UNKNOWN'):M.observe(200,300,reader,View(),100)
 def test_unattributed_valid_journal_fully_proved(self):
  reader,view=OpusCorrections().journal_case();reader.data[M.SS[0]]=reader.data[M.SS[0]].replace(b' users:(("a",pid=200,fd=1))',b'')
  value=M.observe(200,300,reader,view,100);self.assertEqual(value['journal_endpoint'],view.journal())
 def test_unattributed_journal_still_requires_stdio(self):
  reader,view=OpusCorrections().journal_case(fds=(3,));reader.data[M.SS[0]]=reader.data[M.SS[0]].replace(b' users:(("a",pid=200,fd=1))',b'')
  with self.assertRaisesRegex(P.Unknown,'JOURNAL_STDIO_RELATION'):M.observe(200,300,reader,view,100)
 def test_api_held_accepted_api_socket_exempt_first(self):
  reader,view=self.unsupported();reader.data[M.SS[0]]=reader.data[M.SS[0]].replace(b'* 777 /tmp/unsupported',P.API_SOCKET.encode()+b' 777 /tmp/unsupported')
  self.assertEqual(len(M.observe(200,300,reader,view,100)['postgres_pairs']),1)
 def test_unattributed_unheld_row_remains_unrelated(self):
  reader,view=self.unsupported();del view.fd[200][777]
  value=M.observe(200,300,reader,view,100);self.assertEqual(len(value['postgres_pairs']),1)

class FixedClosure(unittest.TestCase):
 def copy_layout(self,directory,entry):
  import shutil
  origin=FIXTURE_ROOT/('collector-code' if entry=='collector.py' else 'helper-code')
  target=Path(directory)/'code';target.mkdir(mode=0o700);target.chmod(0o700)
  for name in (entry,'parsers.py','read_process.py'):
   shutil.copyfile(origin/name,target/name);(target/name).chmod(0o600)
  return target
 def import_layout(self,target,entry):
  import subprocess
  code="import importlib.util,sys; s=importlib.util.spec_from_file_location('candidate',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('DISABLED',m.ENACTED)"
  return subprocess.run([sys.executable,'-I','-B','-c',code,str(target/entry)],capture_output=True,env={'PATH':'/usr/bin'},cwd='/tmp',timeout=5)
 def test_valid_exact_three_file_imports(self):
  import tempfile
  for entry in ('collector.py','metadata_helper.py'):
   with tempfile.TemporaryDirectory(dir='/tmp') as directory:
    target=self.copy_layout(directory,entry);result=self.import_layout(target,entry)
    self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,b'DISABLED False\n');self.assertEqual(set(x.name for x in target.iterdir()),{entry,'parsers.py','read_process.py'})
 def test_actual_preimport_shadow_refusal(self):
  import tempfile
  for entry in ('collector.py','metadata_helper.py'):
   for shadow in ('uuid.py','read_process','__pycache__','extra.json'):
    with self.subTest(entry=entry,shadow=shadow),tempfile.TemporaryDirectory(dir='/tmp') as directory:
     target=self.copy_layout(directory,entry)
     if shadow in ('read_process','__pycache__'):
      (target/shadow).mkdir();(target/shadow/'__init__.py').write_text("raise RuntimeError('SHADOW_EXECUTED')")
     else:(target/shadow).write_text("raise RuntimeError('SHADOW_EXECUTED')")
     result=self.import_layout(target,entry);self.assertNotEqual(result.returncode,0);self.assertNotIn(b'SHADOW_EXECUTED',result.stderr)
     self.assertTrue(b'CODE_CLOSURE_ENTRIES' in result.stderr or b'CODE_CACHE_PRESENT' in result.stderr)
 def test_actual_preimport_module_symlink_refusal(self):
  import tempfile
  for entry in ('collector.py','metadata_helper.py'):
   with tempfile.TemporaryDirectory(dir='/tmp') as directory:
    target=self.copy_layout(directory,entry);(target/'parsers.py').rename(Path(directory)/'real.py');(target/'parsers.py').symlink_to(Path(directory)/'real.py')
    result=self.import_layout(target,entry);self.assertNotEqual(result.returncode,0);self.assertIn(b'CODE_CLOSURE_FILE',result.stderr)
 def test_missing_member_refusal(self):
  import tempfile
  for module,entry in ((C,'collector.py'),(M,'metadata_helper.py')):
   with tempfile.TemporaryDirectory(dir='/tmp') as directory:
    target=self.copy_layout(directory,entry);(target/'parsers.py').unlink()
    with self.assertRaisesRegex(RuntimeError,'CODE_CLOSURE_ENTRIES'):module.require_code_closure(target,entry)
 def test_nonregular_member_refusal(self):
  import tempfile
  for module,entry in ((C,'collector.py'),(M,'metadata_helper.py')):
   with tempfile.TemporaryDirectory(dir='/tmp') as directory:
    target=self.copy_layout(directory,entry);(target/'read_process.py').unlink();(target/'read_process.py').mkdir()
    with self.assertRaisesRegex(RuntimeError,'CODE_CLOSURE_FILE'):module.require_code_closure(target,entry)
 def test_closure_unreadable_enumeration_or_file(self):
  import tempfile
  for module,entry in ((C,'collector.py'),(M,'metadata_helper.py')):
   with tempfile.TemporaryDirectory(dir='/tmp') as directory:
    target=self.copy_layout(directory,entry)
    for operation in ('scandir','open'):
     with patch.object(module.os,operation,side_effect=PermissionError('PRIVATE')):
      with self.assertRaisesRegex(RuntimeError,'CODE_CLOSURE_VISIBILITY_UNKNOWN'):module.require_code_closure(target,entry)
 def test_closure_directory_symlink_refusal(self):
  import tempfile
  with tempfile.TemporaryDirectory(dir='/tmp') as directory:
   target=self.copy_layout(directory,'collector.py');alias=Path(directory)/'alias';alias.symlink_to(target)
   with self.assertRaisesRegex(RuntimeError,'CODE_CLOSURE_DIRECTORY'):C.require_code_closure(alias,'collector.py')
 def test_closure_bound_stops_before_fourth_metadata(self):
  class Entries:
   def __enter__(self):return iter(type('Entry',(),{'name':x})() for x in ('collector.py','parsers.py','read_process.py','uuid.py'))
   def __exit__(self,*args):pass
  with patch.object(C.os,'scandir',return_value=Entries()),patch.object(C.os,'open',side_effect=AssertionError('UNREACHED')):
   with self.assertRaisesRegex(RuntimeError,'CODE_CLOSURE_ENTRIES'):C.require_code_closure(FIXTURE_ROOT/'collector-code','collector.py')
 def test_file_race_refused(self):
  import tempfile
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory(dir='/tmp') as directory:
   target=self.copy_layout(directory,'collector.py');original=C.os.fstat
   def changed(fd):
    info=original(fd);fields={k:getattr(info,k) for k in ('st_dev','st_ino','st_mode','st_uid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')};fields['st_ino']+=1;return SimpleNamespace(**fields)
   with patch.object(C.os,'fstat',changed):
    with self.assertRaisesRegex(RuntimeError,'CODE_CLOSURE_RACED'):C.require_code_closure(target,'collector.py')
 def test_pin_time_changed_closure_both_locations(self):
  import tempfile
  for bad in ('collector','helper'):
   with tempfile.TemporaryDirectory(dir='/tmp') as directory:
    top=Path(directory);(top/'a').mkdir();(top/'b').mkdir();collector=self.copy_layout(top/'a','collector.py');helper=self.copy_layout(top/'b','metadata_helper.py')
    C.require_code_closure(collector,'collector.py');C.require_code_closure(helper,'metadata_helper.py')
    ((collector if bad=='collector' else helper)/'uuid.py').write_bytes(b'')
    with patch.object(C,'ROOT',collector),patch.object(C,'HELPER',str(helper/'metadata_helper.py')),patch.object(C,'checked_image',side_effect=AssertionError('NO_IMAGE_READ')):
     with self.assertRaisesRegex(RuntimeError,'CODE_CLOSURE_ENTRIES'):C.HostReads.__new__(C.HostReads).pins()
 def test_fixed_entry_allowlist_no_fixture_mode(self):
  with self.assertRaisesRegex(RuntimeError,'CODE_CLOSURE_ENTRY'):C.require_code_closure('/synthetic','test_f1.py')
 def test_source_guard_precedes_path_insertion_and_local_imports(self):
  for module in (C,M):
   text=Path(module.__file__).read_text();start=text.index('require_code_closure(Path(__file__)');self.assertLess(start,text.index('sys.path.insert'));self.assertLess(start,text.index('from parsers import'))

class SudoAlias(unittest.TestCase):
 def make(self):
  from types import SimpleNamespace
  def info(mode,ino):return SimpleNamespace(st_dev=1,st_ino=ino,st_mode=mode,st_uid=0,st_nlink=1,st_size=4,st_mtime_ns=1,st_ctime_ns=1)
  nodes={path:info(0o040755,i+1) for i,path in enumerate(C.SUDO_PARENTS)}
  nodes.update({path:info(0o120777,20+i) for i,(path,_) in enumerate(C.SUDO_LINKS)})
  nodes[C.SUDO_ENDPOINT]=info(0o104755,30)
  return nodes,dict(C.SUDO_LINKS)
 def check(self,nodes,links,digest=None,mutate=None,fstat_change=False,path='/usr/bin/sudo',alias=None,owner=0):
  from contextlib import ExitStack
  import copy
  opened=[];closed=[];reads=[b'data',b''];stat_calls=[];opened_info=copy.copy(nodes[C.SUDO_ENDPOINT])
  def lookup(path):return copy.copy(nodes[str(path)])
  def fstat(fd):
   value=copy.copy(opened_info);stat_calls.append(1)
   if fstat_change and len(stat_calls)>1:value.st_ctime_ns+=1
   return value
  def read(fd,limit):
   value=reads.pop(0)
   if not value and mutate:mutate(nodes,links)
   return value
  with ExitStack() as stack:
   for name,fn in [('lstat',lookup),('readlink',lambda path:links[str(path)]),('open',lambda *a:(opened.append(a),77)[1]),('fstat',fstat),('read',read),('close',lambda fd:closed.append(fd))]:stack.enter_context(patch.object(C.os,name,fn))
   C.checked_image(path,digest or hashlib.sha256(b'data').hexdigest(),owner,alias)
  return opened,closed
 def test_exact_chain_setuid_endpoint_accepted_without_exec(self):
  nodes,links=self.make();opened,closed=self.check(nodes,links)
  self.assertEqual(opened[0][0],C.SUDO_ENDPOINT);self.assertTrue(opened[0][1]&os.O_NOFOLLOW);self.assertEqual(closed,[77])
 def test_each_link_target_change_refused(self):
  for path,_ in C.SUDO_LINKS:
   nodes,links=self.make();links[path]='/tmp/other'
   with self.assertRaisesRegex(P.Unknown,'SUDO_LINK_CHANGED'):self.check(nodes,links)
 def test_nonroot_or_nonlink_alias_refused(self):
  for path,_ in C.SUDO_LINKS:
   for field,value in [('st_uid',1000),('st_mode',0o100755)]:
    nodes,links=self.make();setattr(nodes[path],field,value)
    with self.assertRaisesRegex(P.Unknown,'SUDO_LINK_GUARD'):self.check(nodes,links)
 def test_each_parent_writable_symlink_or_nonroot_refused(self):
  for path in C.SUDO_PARENTS:
   for field,value in [('st_uid',1000),('st_mode',0o040777),('st_mode',0o120777)]:
    nodes,links=self.make();setattr(nodes[path],field,value)
    with self.assertRaisesRegex(P.Unknown,'SUDO_PARENT_GUARD'):self.check(nodes,links)
 def test_endpoint_type_owner_nlink_write_refused(self):
  for field,value in [('st_mode',0o120777),('st_uid',1000),('st_nlink',2),('st_mode',0o100777)]:
   nodes,links=self.make();setattr(nodes[C.SUDO_ENDPOINT],field,value)
   with self.assertRaisesRegex(P.Unknown,'SUDO_ENDPOINT_GUARD'):self.check(nodes,links)
 def test_hash_mismatch_refused(self):
  nodes,links=self.make()
  with self.assertRaisesRegex(P.Unknown,'IMAGE_PIN_CHANGED'):self.check(nodes,links,digest='0'*64)
 def test_hash_fstat_race_refused(self):
  nodes,links=self.make()
  with self.assertRaisesRegex(P.Unknown,'IMAGE_RACED'):self.check(nodes,links,fstat_change=True)
 def test_link_inode_race_refused(self):
  nodes,links=self.make()
  def mutate(n,l):n['/usr/bin/sudo'].st_ino+=1
  with self.assertRaisesRegex(P.Unknown,'SUDO_CHAIN_RACED'):self.check(nodes,links,mutate=mutate)
 def test_parent_race_refused(self):
  nodes,links=self.make()
  def mutate(n,l):n['/etc/alternatives'].st_ino+=1
  with self.assertRaisesRegex(P.Unknown,'SUDO_CHAIN_RACED'):self.check(nodes,links,mutate=mutate)
 def test_posthash_link_target_change_refused(self):
  nodes,links=self.make()
  def mutate(n,l):l['/etc/alternatives/sudo']='/tmp/other'
  with self.assertRaisesRegex(P.Unknown,'SUDO_LINK_CHANGED'):self.check(nodes,links,mutate=mutate)
 def test_endpoint_replacement_after_hash_refused(self):
  nodes,links=self.make()
  def mutate(n,l):n[C.SUDO_ENDPOINT].st_ino+=1
  with self.assertRaisesRegex(P.Unknown,'SUDO_CHAIN_RACED'):self.check(nodes,links,mutate=mutate)
 def test_unbound_digest_no_metadata_read(self):
  with patch.object(C.os,'lstat',side_effect=AssertionError('NO_READ')):
   with self.assertRaisesRegex(P.Unknown,'IMAGE_PIN_UNBOUND'):C.checked_image('/usr/bin/sudo',None,0)
 def test_wrong_owner_or_alias_mode_refused(self):
  for owner,alias in ((1000,None),(0,'python3.14')):
   nodes,links=self.make()
   with self.assertRaisesRegex(P.Unknown,'SUDO_ALIAS_INPUT'):self.check(nodes,links,owner=owner,alias=alias)
 def test_non_sudo_path_not_given_chain_fallback(self):
  with patch.object(C,'sudo_chain',side_effect=AssertionError('NO_CHAIN')),patch.object(C.os,'open',side_effect=OSError('FINAL_LINK')):
   with self.assertRaises(OSError):C.checked_image('/usr/bin/other','a'*64,0)
 def test_python_exact_alias_retained(self):
  with patch.object(C,'sudo_chain',side_effect=AssertionError('NO_SUDO')),patch.object(C.os,'readlink',return_value='python3.99'):
   with self.assertRaisesRegex(P.Unknown,'ALIAS_CHANGED'):C.checked_image('/usr/bin/python3','a'*64,0,'python3.14')

class ClientsMapping(unittest.TestCase):
 def check(self,value):return P.local_map(raw(value))
 def reject(self,value):
  with self.assertRaises(P.Unknown):self.check(value)
 def test_valid_partial_allowed(self):self.assertTrue(self.check(CLIENTS)['partial'])
 def test_not_hardcoded_proximal(self):self.assertEqual(self.check(CLIENTS)['local_machine'],'root-central')
 def test_reported_omitted(self):self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(self.check(CLIENTS)))
 def test_archon_mapping_unknown(self):
  v=copy.deepcopy(CLIENTS);v['data']['rows'][0]['machine_id']='archon';self.reject(v)
 def test_machine_principal_unknown(self):
  v=copy.deepcopy(CLIENTS);v['data']['rows'][0]['principal']='machine:root-central/agent';self.reject(v)
 def test_empty_mapping_unknown(self):
  v=copy.deepcopy(CLIENTS);v['data']['rows'][0]['machine_id']='';self.reject(v)
 def test_bad_accounting(self):
  v=copy.deepcopy(CLIENTS);v['data']['truncated']=False;self.reject(v)
 def test_no_cohort_unknown(self):
  v=copy.deepcopy(CLIENTS);v['data']['rows']=[];v['data']['returned']=0;self.reject(v)
 def test_unsupported_unknown(self):
  v=copy.deepcopy(CLIENTS);v['data']['schema']='future/1';self.reject(v)
 def test_missing_required_unknown(self):
  v=copy.deepcopy(CLIENTS);del v['data']['server'];self.reject(v)
 def test_false_partial_unknown(self):
  v=copy.deepcopy(CLIENTS);v['data']['partial']=False;self.reject(v)
 def test_evicted_partial_allowed(self):
  v=copy.deepcopy(CLIENTS);v['data'].update(eligible_rows=1,truncated=False);v['data']['counters']['evicted_cohorts']=1;self.assertTrue(self.check(v)['partial'])
 def test_reported_local_label_cannot_repair(self):
  v=copy.deepcopy(CLIENTS);v['data']['rows'][0].update(machine_id='',reported={'machine_id':'root-central'});self.reject(v)
 def test_epoch_bad(self):
  v=copy.deepcopy(CLIENTS);v['data']['observation_epoch']='bad';self.reject(v)
 def test_principal_control(self):
  v=copy.deepcopy(CLIENTS);v['data']['rows'][0]['principal']='x\n';self.reject(v)

class Stream:
 def __init__(self,fd):self.fd=fd;self.closed=False
 def fileno(self):return self.fd
 def close(self):self.closed=True
class FakeIO:
 def __init__(self,out=b'{}',code=0,hang=False):
  self.t=0.;self.out=bytearray(out);self.code=code;self.hang=hang;self.launched=0;self.input=b''
  self.stdout=Stream(10);self.stderr=Stream(11);self.stdin=Stream(12);self.pid=999
 def now(self):return self.t
 def launch(self,*args,**kwargs):self.launched+=1;self.argv=args[0];self.kw=kwargs;return self
 def poll(self):return None if self.hang or self.out else self.code
 def start(self,pid):return 9990
 def read(self,fd,n):
  if self.hang:raise BlockingIOError()
  if fd==10:result=bytes(self.out[:n]);del self.out[:n];return result
  return b''
 def write(self,fd,data):self.input+=bytes(data[:2]);return min(2,len(data))
 def selector(self):
  outer=self
  class Select:
   def __init__(self):self.items={}
   def register(self,stream,event,tag):self.items[stream.fd]=type('Key',(),{'fileobj':stream,'data':tag})()
   def unregister(self,stream):del self.items[stream.fd]
   def get_map(self):return self.items
   def select(self,delay):outer.t+=max(.01,delay);return [(v,None) for v in list(self.items.values())]
   def close(self):pass
  return Select()
 def wait(self,*a):raise AssertionError('WAIT')
 def kill(self,*a):raise AssertionError('KILL')
 def terminate(self,*a):raise AssertionError('TERMINATE')
class ChildIntake(unittest.TestCase):
 def run_fake(self,io,body=b'',cap=65536,deadline=10):
  reader=R.ReadProcess([C.TS],clock=io.now,launch=io.launch,selector=io.selector,start=io.start)
  with patch.object(R.os,'set_blocking',lambda *a:None),patch.object(R.os,'read',io.read),patch.object(R.os,'write',io.write):
   try:return reader.run('route',C.TS,body,deadline,cap),reader
   except P.Unknown as e:return e,reader
 def test_success(self):
  io=FakeIO();out,r=self.run_fake(io);self.assertEqual(out,b'{}');self.assertEqual(io.launched,1);self.assertEqual(r.receipts[0]['outcome'],'COMPLETE_ZERO')
 def test_unknown_timeout(self):
  io=FakeIO(hang=True);out,r=self.run_fake(io);self.assertIsInstance(out,P.Unknown);self.assertEqual(r.receipts[0]['outcome'],'TIMEOUT_UNKNOWN');self.assertEqual(io.launched,1)
 def test_overflow(self):
  out,r=self.run_fake(FakeIO(b'x'*100),cap=20);self.assertIsInstance(out,P.Unknown);self.assertEqual(r.receipts[0]['outcome'],'OVERFLOW_UNKNOWN')
 def test_nonzero(self):
  out,r=self.run_fake(FakeIO(b'PRIVATE',code=1));self.assertIsInstance(out,P.Unknown);self.assertEqual(str(out),'READ_NONZERO');self.assertNotIn('PRIVATE',json.dumps(r.receipts))
 def test_partial_stdin(self):
  io=FakeIO();out,r=self.run_fake(io,b'123456');self.assertEqual(io.input,b'123456');self.assertEqual(out,b'{}')
 def test_launch_error(self):
  io=FakeIO();io.launch=lambda *a,**k:(_ for _ in ()).throw(OSError('SECRET'))
  out,r=self.run_fake(io);self.assertEqual(str(out),'READ_INTAKE_ERROR');self.assertIsNone(r.receipts[0]['pid']);self.assertNotIn('SECRET',json.dumps(r.receipts))
 def test_expired_no_launch(self):
  io=FakeIO();out,r=self.run_fake(io,deadline=-1);self.assertEqual(io.launched,0)
 def test_effect_rejected(self):
  io=FakeIO();r=R.ReadProcess([C.TS],clock=io.now,launch=io.launch)
  with self.assertRaises(P.Unknown):r.run('bad',['systemctl','restart','x'],b'',10)
  self.assertEqual(io.launched,0)
 def test_no_inherited_env(self):
  io=FakeIO();self.run_fake(io);self.assertEqual(io.kw['env'],R.ENV);self.assertNotIn('TS_DUMP_HELP',io.kw['env']);self.assertNotIn('HTTPS_PROXY',io.kw['env'])
 def test_zero_exit_without_eof_unknown(self):
  io=FakeIO(hang=True);io.poll=lambda:0
  out,r=self.run_fake(io);self.assertIsInstance(out,P.Unknown);self.assertEqual(r.receipts[0]['outcome'],'TIMEOUT_UNKNOWN')
 def test_stderr_overflow(self):
  io=FakeIO();old=io.read
  io.read=lambda fd,n:b'x'*n if fd==11 else old(fd,n)
  out,r=self.run_fake(io,cap=20);self.assertEqual(r.receipts[0]['outcome'],'OVERFLOW_UNKNOWN')
 def test_unknown_start_is_not_invented(self):
  io=FakeIO();io.start=lambda pid:None
  out,r=self.run_fake(io);self.assertEqual(out,b'{}');self.assertIsNone(r.receipts[0]['start_ticks'])
 def test_pin_symlink_refused(self):
  p=Path('/tmp/pin-target');p.write_bytes(b'bytes');p.chmod(0o600);q=Path('/tmp/pin-link');q.symlink_to(p)
  with self.assertRaises(OSError):C.checked_image(q,hashlib.sha256(b'bytes').hexdigest(),os.getuid())
  q.unlink();p.unlink()
 def test_pin_writable_refused(self):
  p=Path('/tmp/pin-writable');p.write_bytes(b'bytes');p.chmod(0o666)
  with self.assertRaises(P.Unknown):C.checked_image(p,hashlib.sha256(b'bytes').hexdigest(),os.getuid())
  p.unlink()
 def test_file_guard(self):
  path=Path('/tmp/pin-fixture');path.write_bytes(b'bytes');path.chmod(0o600)
  C.checked_image(path,hashlib.sha256(b'bytes').hexdigest(),os.getuid())
  with self.assertRaises(P.Unknown):C.checked_image(path,'a'*64,os.getuid())
  path.unlink()

if __name__=='__main__':unittest.main(verbosity=2)
