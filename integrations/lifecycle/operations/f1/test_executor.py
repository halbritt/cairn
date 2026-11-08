import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('candidate',ROOT/'executor.py')
E=importlib.util.module_from_spec(spec);spec.loader.exec_module(E)

# Synthetic source bytes exercise custody and pins; no installed or private
# collector/helper projection is copied into this fixture.
SYNTHETIC_SOURCES={
 'collector/collector.py':b'synthetic collector',
 'collector/parsers.py':b'synthetic collector parser',
 'collector/read_process.py':b'synthetic collector intake',
 'helper/metadata_helper.py':b'synthetic helper',
 'helper/parsers.py':b'synthetic helper parser',
 'helper/read_process.py':b'synthetic helper intake',
}

def synthetic_selection():
 directories=[
  {'path':'/synthetic-helper','uid':0,'gid':0,'initial_mode':'0700','final_mode':'0755','parent':'/'},
  {'path':'/synthetic-collector','uid':1000,'gid':1000,'initial_mode':'0700','final_mode':'0700','parent':'/'},
 ]
 rows=[]
 for relative,raw in SYNTHETIC_SOURCES.items():
  helper=relative.startswith('helper/')
  rows.append({'source':relative,'future_path':('/synthetic-helper/' if helper else '/synthetic-collector/')+Path(relative).name,
   'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'future_uid':0 if helper else 1000,'future_mode':'0644' if helper else '0600'})
 return {'source_root':'/synthetic-source','plan_id':'synthetic-plan','intent':'/synthetic-intent','output':'/synthetic-observation',
  'directories':directories,'files':rows,'observe_argv':['/usr/bin/python3','-I','-B','/synthetic-collector/collector.py'],
  'observe_env':{'PATH':'/usr/bin:/bin','LC_ALL':'C'},'tools':{},'source_owner':1000}

def setUpModule():
 assert E.SELECTION is None and E.OWNER_HOME is None
 E.SELECTION=synthetic_selection()
 E.OWNER_HOME='/synthetic-home'

def tearDownModule():
 E.SELECTION=None
 E.OWNER_HOME=None

class FakeFiles:
 def __init__(self):
  self.user=1000;self.events=[];self.records={};self.dirs={};self.files={};self.clock=0.;self.fail=None;self.environment=dict(E.SELECTION['observe_env'])
 def hit(self,name):
  self.events.append(name)
  if self.fail==name:raise OSError('PRIVATE_FAILURE')
 def uid(self):return self.user
 def gid(self):return self.user
 def now(self):self.clock+=0.01;return self.clock
 def env(self):return self.environment
 def check_parent(self,path):self.hit('parent:'+str(path));return 'fixed-parent'
 def absent(self,path):
  self.hit('absent:'+str(path));E.need(str(path) not in self.dirs and str(path) not in self.files,'DESTINATION_EXISTS')
 def source(self,row):
  self.hit('source:'+row['source']);raw=SYNTHETIC_SOURCES[row['source']]
  E.need(hashlib.sha256(raw).hexdigest()==row['sha256'] and len(raw)==row['bytes'],'SOURCE_PIN');return raw
 def create_dir(self,path,uid,gid):
  self.hit('mkdir:'+str(path));E.need(self.user==uid==gid,'KERNEL_INSTALL_IDENTITY');E.need(str(path) not in self.dirs,'DESTINATION_EXISTS');self.dirs[str(path)]={'uid':uid,'gid':gid,'mode':0o700};return dict(self.dirs[str(path)])
 def write_new(self,path,raw,uid,gid,mode):
  self.hit('write:'+str(path));E.need(self.user==uid==gid,'KERNEL_INSTALL_IDENTITY');E.need(str(path) not in self.files,'DESTINATION_EXISTS')
  self.files[str(path)]={'raw':raw,'uid':uid,'gid':gid,'mode':mode};return {'uid':uid,'mode':format(mode,'04o'),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
 def finish_dir(self,path,uid,gid,mode):
  self.hit('finish:'+str(path));self.dirs[str(path)]['mode']=mode
 def verify_dir(self,directory,rows,initial=False):
  self.hit('verify:'+str(directory['path']));d=self.dirs.get(directory['path']);E.need(d is not None,'ACTUAL_CLOSURE_ABSENT')
  E.need(d['uid']==directory['uid'] and d['mode']==int(directory['initial_mode'] if initial else directory['final_mode'],8),'ACTUAL_DIRECTORY')
  expected={row['future_path'] for row in rows};actual={path for path in self.files if str(Path(path).parent)==directory['path']};E.need(actual==expected,'CLOSURE_ENTRIES')
  out={}
  for row in rows:
   f=self.files[row['future_path']];E.need(f['uid']==row['future_uid'] and f['mode']==int(row['future_mode'],8) and hashlib.sha256(f['raw']).hexdigest()==row['sha256'],'ACTUAL_FILE_PIN');out[row['future_path']]=row['sha256']
  return out
 def record(self,name,value,root=False):
  self.hit('record:'+name);E.need(self.user==(0 if root else 1000),'RECORD_KERNEL_IDENTITY');E.need(name not in self.records,'RECORD_EXISTS');self.records[name]=copy.deepcopy(value)
 def read_record_bytes(self,name,root=False):return E.canonical(self.read_record(name,root))
 def read_record(self,name,root=False):self.hit('read-record:'+name);E.need(name in self.records,'RECORD_ABSENT');return copy.deepcopy(self.records[name])
 def tools(self,install_only=False):self.hit('tools:install' if install_only else 'tools:all');return {}
 def checked(self,path,*args,**kwargs):self.hit('checked:'+str(path));E.need(str(path) in self.files,'FINAL_ABSENT');return self.files[str(path)]['raw'],{}

class RootChild:
 def __init__(self,fs,mode='complete'):self.fs=fs;self.mode=mode;self.receipt=None;self.calls=[]
 def run(self,argv,env,deadline):
  self.calls.append((argv,env,deadline));self.receipt={'outcome':'TIMEOUT_UNKNOWN','pid':123,'returncode':None,'eof':False}
  if self.mode=='unknown':return None
  if self.mode=='reported-only':
   self.receipt={'outcome':'EXITED_ZERO_EOF','pid':123,'returncode':0,'eof':True};return E.canonical({'schema':'F1.root-install/1','plan_id':E.SELECTION['plan_id'],'status':'ROOT_CLOSURE_COMPLETE'})
  self.fs.user=0
  try:result=E.root_stage(self.fs)
  finally:self.fs.user=1000
  self.receipt={'outcome':'EXITED_ZERO_EOF' if result['status']=='ROOT_CLOSURE_COMPLETE' else 'EXITED_NONZERO_EOF','pid':123,'returncode':0 if result['status']=='ROOT_CLOSURE_COMPLETE' else 1,'eof':True}
  if self.mode=='tamper-after-completion':self.fs.files[E.role_rows(0)[0]['future_path']]['raw']=b'changed'
  return E.canonical(result) if self.receipt['returncode']==0 else None

class ObserveChild:
 def __init__(self,fs,mode='complete'):self.fs=fs;self.mode=mode;self.receipt=None;self.calls=[]
 def run(self,argv,env,deadline):
  self.calls.append((argv,env,deadline));self.receipt={'outcome':'TIMEOUT_UNKNOWN','pid':124,'returncode':None,'eof':False}
  if self.mode=='unknown':return None
  self.receipt={'outcome':'EXITED_ZERO_EOF','pid':124,'returncode':0,'eof':True}
  if self.mode!='missing-final':
   value={'schema':'cairn.F1-observation/2','status':'UNKNOWN' if self.mode=='unknown-final' else 'OBSERVED_COOPERATIVE_RELATIONSHIP_CANDIDATE','f1_acceptance':'NOT_ASSESSED','readiness':'NOT_ASSESSED'}
   self.fs.files[E.SELECTION['output']+'/final.json']={'raw':E.canonical(value)}
  return b'{"status":"synthetic"}\n'

def bind_synthetic_completion(case,result):
 source=hashlib.sha256((ROOT/'executor.py').read_bytes()).hexdigest()
 receipt={'outcome':'EXITED_ZERO_EOF','pid':987,'returncode':0,'eof':True}
 capture=E.completion_capture(E.canonical(result),receipt,source)
 raw=E.canonical(capture)
 for name,value in [('INSTALL_COMPLETION_CAPTURE_TEXT',raw.decode()),('INSTALL_COMPLETION_CAPTURE_SHA256',hashlib.sha256(raw).hexdigest()),('SELECTED_INSTALLER_SHA256',source)]:
  holder=patch.object(E,name,value);holder.start();case.addCleanup(holder.stop)
 return capture

class Flow(unittest.TestCase):
 def setUp(self):
  self.program=patch.object(E,'ROOT_PROGRAM_TEXT','FIXED_SYNTHETIC_PROGRAM');self.digest=patch.object(E,'ROOT_PROGRAM_SHA256',hashlib.sha256(b'FIXED_SYNTHETIC_PROGRAM').hexdigest());self.program.start();self.digest.start()
 def tearDown(self):self.program.stop();self.digest.stop()
 def installed(self):
  fs=FakeFiles();child=RootChild(fs);result=E.install(fs,child);self.assertEqual(result['status'],'INSTALLED_NOT_OBSERVED');bind_synthetic_completion(self,result);return fs
 def test_split_owner_complete_order(self):
  fs=FakeFiles();child=RootChild(fs);r=E.install(fs,child);self.assertEqual(r['status'],'INSTALLED_NOT_OBSERVED');self.assertEqual(len(child.calls),1)
  events=fs.events;self.assertLess(events.index('record:started.json'),events.index('record:root-launch.json'));self.assertLess(events.index('record:root-launch.json'),events.index('mkdir:'+E.role_dir(0)['path']))
  self.assertLess(events.index('verify:'+E.role_dir(0)['path']),events.index('mkdir:'+E.role_dir(1000)['path']));self.assertNotIn(E.SELECTION['output'],fs.dirs)
  self.assertEqual(child.calls[0][0],['/usr/bin/sudo','-n','/usr/bin/python3','-I','-B','-c','FIXED_SYNTHETIC_PROGRAM'])
  self.assertEqual(fs.dirs[E.role_dir(0)['path']]['uid'],0);self.assertEqual(fs.dirs[E.role_dir(1000)['path']]['uid'],1000)
 def test_unbound_program_prevents_reads(self):
  fs=FakeFiles()
  with patch.object(E,'ROOT_PROGRAM_TEXT',None):r=E.install(fs,RootChild(fs))
  self.assertEqual(r['reason'],'ROOT_PROGRAM_UNBOUND');self.assertEqual(fs.events,[])
 def test_wrong_coordinator_kernel_identity(self):
  fs=FakeFiles();fs.user=0;r=E.install(fs,RootChild(fs));self.assertEqual(r['reason'],'COORDINATOR_KERNEL_IDENTITY');self.assertEqual(fs.events,[])
 def test_source_failure_before_intent(self):
  fs=FakeFiles();fs.fail='source:'+E.SELECTION['files'][0]['source'];child=RootChild(fs);r=E.install(fs,child);self.assertEqual(r['status'],'UNKNOWN');self.assertFalse(fs.dirs);self.assertFalse(child.calls)
 def test_existing_target_refuses_no_intent(self):
  fs=FakeFiles();fs.dirs[E.role_dir(0)['path']]={};child=RootChild(fs);r=E.install(fs,child);self.assertEqual(r['reason'],'DESTINATION_EXISTS');self.assertFalse(child.calls);self.assertFalse(fs.records)
 def test_intent_fsync_or_started_failure_no_child(self):
  for failure in ('mkdir:'+E.SELECTION['intent'],'record:started.json','record:root-launch.json'):
   fs=FakeFiles();fs.fail=failure;child=RootChild(fs);r=E.install(fs,child);self.assertEqual(r['status'],'UNKNOWN');self.assertFalse(child.calls);self.assertNotIn(E.role_dir(0)['path'],fs.dirs)
 def test_unknown_root_no_collector_or_retry(self):
  fs=FakeFiles();child=RootChild(fs,'unknown');r=E.install(fs,child);self.assertEqual(r['reason'],'ROOT_CHILD_NOT_POSITIVE');self.assertNotIn(E.role_dir(1000)['path'],fs.dirs)
  again=E.install(fs,child);self.assertEqual(again['status'],'UNKNOWN');self.assertEqual(len(child.calls),1)
 def test_reported_success_is_not_actual_closure(self):
  fs=FakeFiles();r=E.install(fs,RootChild(fs,'reported-only'));self.assertEqual(r['reason'],'ACTUAL_CLOSURE_ABSENT');self.assertNotIn(E.role_dir(1000)['path'],fs.dirs)
 def test_actual_tamper_after_known_root_blocks_collector(self):
  fs=FakeFiles();r=E.install(fs,RootChild(fs,'tamper-after-completion'));self.assertEqual(r['reason'],'ACTUAL_FILE_PIN');self.assertNotIn(E.role_dir(1000)['path'],fs.dirs)
 def test_root_wrong_kernel_identity_refuses(self):
  fs=FakeFiles();r=E.root_stage(fs);self.assertEqual(r['reason'],'ROOT_KERNEL_IDENTITY');self.assertFalse(fs.records)
 def test_root_no_started_intent_refuses(self):
  fs=FakeFiles();fs.user=0;r=E.root_stage(fs);self.assertEqual(r['status'],'UNKNOWN');self.assertFalse(fs.dirs)
 def test_root_partial_write_preserved_no_collector(self):
  fs=FakeFiles();fs.fail='write:'+E.role_rows(0)[1]['future_path'];r=E.install(fs,RootChild(fs));self.assertEqual(r['status'],'UNKNOWN');self.assertIn(E.role_dir(0)['path'],fs.dirs);self.assertIn(E.role_rows(0)[0]['future_path'],fs.files);self.assertNotIn(E.role_dir(1000)['path'],fs.dirs)
 def test_collector_partial_failure_keeps_helper(self):
  fs=FakeFiles();fs.fail='write:'+E.role_rows(1000)[1]['future_path'];r=E.install(fs,RootChild(fs));self.assertEqual(r['status'],'UNKNOWN');fs.verify_dir(E.role_dir(0),E.role_rows(0));self.assertIn(E.role_rows(1000)[0]['future_path'],fs.files)
 def test_root_final_durability_failure_is_not_success(self):
  fs=FakeFiles();fs.fail='record:root-final.json';r=E.install(fs,RootChild(fs));self.assertEqual(r['status'],'UNKNOWN');self.assertNotIn(E.role_dir(1000)['path'],fs.dirs)
 def test_install_final_durability_failure_not_installed_claim(self):
  fs=FakeFiles();fs.fail='record:final.json';r=E.install(fs,RootChild(fs));self.assertEqual(r['reason'],'INSTALL_FINAL_NOT_DURABLE');self.assertEqual(r['status'],'UNKNOWN')
 def test_verifier_complete_mark_recheck_launch(self):
  fs=self.installed();fs.events=[];child=ObserveChild(fs);r=E.verify_then_observe(fs,child);self.assertEqual(r['status'],'COLLECTOR_EXITED_WITH_FINAL');self.assertEqual(r['acceptance'],'NOT_ASSESSED');self.assertEqual(len(child.calls),1)
  mark=fs.events.index('record:observe-launch.json');self.assertIn('tools:all',fs.events[mark+1:]);self.assertEqual(child.calls[0][0],E.SELECTION['observe_argv']);self.assertEqual(child.calls[0][1],E.SELECTION['observe_env'])
 def test_verifier_bad_environment_no_marker_or_child(self):
  fs=self.installed();fs.environment['UNEXPECTED']='x';child=ObserveChild(fs);r=E.verify_then_observe(fs,child);self.assertEqual(r['reason'],'MINIMAL_ENVIRONMENT');self.assertFalse(child.calls);self.assertNotIn('observe-launch.json',fs.records)
 def test_verifier_tools_failure_no_launch(self):
  fs=self.installed();fs.fail='tools:all';child=ObserveChild(fs);r=E.verify_then_observe(fs,child);self.assertEqual(r['status'],'UNKNOWN');self.assertFalse(child.calls)
 def test_verifier_marker_durability_failure_no_launch(self):
  fs=self.installed();fs.fail='record:observe-launch.json';child=ObserveChild(fs);r=E.verify_then_observe(fs,child);self.assertEqual(r['status'],'UNKNOWN');self.assertFalse(child.calls)
 def test_verifier_unknown_child_no_retry_even_output_absent(self):
  fs=self.installed();child=ObserveChild(fs,'unknown');r=E.verify_then_observe(fs,child);self.assertEqual(r['reason'],'COLLECTOR_CHILD_NOT_POSITIVE');self.assertNotIn(E.SELECTION['output'],fs.dirs)
  E.verify_then_observe(fs,child);self.assertEqual(len(child.calls),1)
 def test_verifier_known_exit_missing_final_unknown(self):
  fs=self.installed();r=E.verify_then_observe(fs,ObserveChild(fs,'missing-final'));self.assertEqual(r['reason'],'FINAL_ABSENT')
 def test_verifier_unknown_final_not_positive_evidence(self):
  fs=self.installed();r=E.verify_then_observe(fs,ObserveChild(fs,'unknown-final'));self.assertEqual(r['status'],'OBSERVATION_UNKNOWN')
 def test_verifier_wrapper_final_failure_unknown(self):
  fs=self.installed();fs.fail='record:observe-final.json';r=E.verify_then_observe(fs,ObserveChild(fs));self.assertEqual(r['reason'],'OBSERVATION_WRAPPER_FINAL_NOT_DURABLE')
 def test_reported_completion_flag_without_actual_status_refuses(self):
  for changed in ({'returncode':None},{'returncode':7},{'returncode':False},{'eof':False},{'pid':None}):
   fs=FakeFiles();child=RootChild(fs,'reported-only');run=child.run
   def altered(*args):
    raw=run(*args);child.receipt.update(changed);return raw
   child.run=altered;r=E.install(fs,child)
   self.assertEqual(r['reason'],'ROOT_CHILD_NOT_POSITIVE');self.assertNotIn(E.role_dir(1000)['path'],fs.dirs)
 def test_recheck_failure_after_marker_blocks_invocation(self):
  fs=self.installed();child=ObserveChild(fs);old=fs.record
  def marker(name,value,root=False):
   old(name,value,root)
   if name=='observe-launch.json':fs.fail='tools:all'
  fs.record=marker;r=E.verify_then_observe(fs,child)
  self.assertEqual(r['status'],'UNKNOWN');self.assertFalse(child.calls);self.assertIn('observe-launch.json',fs.records)
 def test_duplicate_json_refuses(self):
  with self.assertRaisesRegex(E.Refusal,'JSON_DUPLICATE'):E.parse_json(b'{"a":1,"a":2}')

class LocalFiles(E.Files):
 def __init__(self,root):
  self.root=root;self.owners={str(path):path.stat().st_uid for path in [root,*root.parents]}
 def owner_for(self,path):return self.owners.get(str(path),os.geteuid())

class FilePrimitives(unittest.TestCase):
 def setUp(self):
  os.chmod('/tmp',0o700);self.temp=tempfile.TemporaryDirectory(dir='/tmp');self.root=Path(self.temp.name);self.root.chmod(0o700);self.fs=LocalFiles(self.root);self.uid=os.geteuid();self.gid=os.getegid()
 def tearDown(self):self.temp.cleanup()
 def create(self,name='file',raw=b'fixed',mode=0o600):
  path=self.root/name;path.write_bytes(raw);path.chmod(mode);return path
 def test_checked_regular_exact_hash(self):
  path=self.create();raw,proof=self.fs.checked(path,self.uid,0o600,5,hashlib.sha256(b'fixed').hexdigest(),5,self.gid);self.assertEqual(raw,b'fixed');self.assertEqual(proof['mode'],'0600')
 def test_hash_length_and_bound_refuse(self):
  path=self.create()
  for limit,digest,length in [(4,None,None),(5,'0'*64,5),(5,None,4)]:
   with self.assertRaises(E.Refusal):self.fs.checked(path,self.uid,0o600,limit,digest,length)
 def test_wrong_owner_mode_and_hardlink_refuse(self):
  path=self.create()
  with self.assertRaises(E.Refusal):self.fs.checked(path,self.uid+1,0o600,5)
  path.chmod(0o666)
  with self.assertRaises(E.Refusal):self.fs.checked(path,self.uid,0o600,5)
  path.chmod(0o600);os.link(path,self.root/'other')
  with self.assertRaises(E.Refusal):self.fs.checked(path,self.uid,0o600,5)
 def test_file_symlink_and_parent_symlink_refuse(self):
  path=self.create();link=self.root/'link';link.symlink_to(path)
  with self.assertRaises(OSError):self.fs.checked(link,self.uid,0o600,5)
  directory=self.root/'dir';directory.mkdir(mode=0o700);alias=self.root/'alias';alias.symlink_to(directory)
  with self.assertRaises(OSError):self.fs.open_dir(alias)
 def test_writable_parent_refuses(self):
  directory=self.root/'dir';directory.mkdir(mode=0o777);directory.chmod(0o777)
  with self.assertRaisesRegex(E.Refusal,'PARENT_OWNER_MODE'):self.fs.open_dir(directory)
 def test_directory_creation_exclusive_fsync(self):
  path=self.root/'target';events=[];real=E.os.fsync
  with patch.object(E.os,'fsync',lambda fd:(events.append(os.fstat(fd).st_ino),real(fd))[1]):self.fs.create_dir(path,self.uid,self.gid)
  self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o700);self.assertEqual(events[-1],self.root.stat().st_ino)
  with self.assertRaises(FileExistsError):self.fs.create_dir(path,self.uid,self.gid)
 def test_directory_parent_fsync_failure_preserves_attempt(self):
  path=self.root/'target';calls=[];real=E.os.fsync
  def sync(fd):
   calls.append(fd)
   if os.fstat(fd).st_ino==self.root.stat().st_ino:raise OSError('FSYNC')
   real(fd)
  with patch.object(E.os,'fsync',sync):
   with self.assertRaises(OSError):self.fs.create_dir(path,self.uid,self.gid)
  self.assertTrue(path.is_dir())
 def test_partial_writes_complete_exactly(self):
  path=self.root/'target';real=E.os.write;calls=[]
  def partial(fd,raw):calls.append(len(raw));return real(fd,raw[:2])
  with patch.object(E.os,'write',partial):proof=self.fs.write_new(path,b'abcdef',self.uid,self.gid,0o600)
  self.assertEqual(path.read_bytes(),b'abcdef');self.assertGreater(len(calls),1);self.assertEqual(proof['sha256'],hashlib.sha256(b'abcdef').hexdigest())
 def test_zero_write_leaves_partial_no_retry(self):
  path=self.root/'target'
  with patch.object(E.os,'write',return_value=0):
   with self.assertRaisesRegex(E.Refusal,'WRITE_ZERO'):self.fs.write_new(path,b'x',self.uid,self.gid,0o600)
  self.assertTrue(path.exists())
  with self.assertRaises(FileExistsError):self.fs.write_new(path,b'x',self.uid,self.gid,0o600)
 def test_write_fsync_failure_no_cleanup(self):
  path=self.root/'target'
  with patch.object(E.os,'fsync',side_effect=OSError('FSYNC')):
   with self.assertRaises(OSError):self.fs.write_new(path,b'x',self.uid,self.gid,0o600)
  self.assertEqual(path.read_bytes(),b'x')
 def layout(self):
  directory=self.root/'closure';self.fs.create_dir(directory,self.uid,self.gid);rows=[]
  for name in ('a.py','b.py','c.py'):
   raw=name.encode();path=directory/name;self.fs.write_new(path,raw,self.uid,self.gid,0o600);rows.append({'future_path':str(path),'future_uid':self.uid,'future_mode':'0600','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
  descriptor={'path':str(directory),'uid':self.uid,'gid':self.gid,'initial_mode':'0700','final_mode':'0700'};return directory,rows,descriptor
 def test_closure_exact_and_extra_refusal(self):
  directory,rows,d=self.layout();self.assertEqual(len(self.fs.verify_dir(d,rows)),3);(directory/'extra').write_bytes(b'')
  with self.assertRaisesRegex(E.Refusal,'CLOSURE_ENTRIES'):self.fs.verify_dir(d,rows)
 def test_closure_missing_refusal(self):
  directory,rows,d=self.layout();(directory/'b.py').unlink()
  with self.assertRaisesRegex(E.Refusal,'CLOSURE_ENTRIES'):self.fs.verify_dir(d,rows)
 def test_alias_target_owner_shape(self):
  link=self.root/'link';link.symlink_to('/fixed/target')
  # Root ownership is mocked only for this isolated symlink metadata, never a real alias.
  real=E.os.stat
  def root_link(*a,**kw):
   info=real(*a,**kw)
   if stat.S_ISLNK(info.st_mode):
    from types import SimpleNamespace
    data={k:getattr(info,k) for k in ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')};data['st_uid']=0;return SimpleNamespace(**data)
   return info
  with patch.object(E.os,'stat',root_link):
   self.fs.alias(link,'/fixed/target')
   with self.assertRaisesRegex(E.Refusal,'ALIAS_CHANGED'):self.fs.alias(link,'/other')
 def test_checked_fstat_race_refuses(self):
  from types import SimpleNamespace
  path=self.create();original=E.os.fstat;seen=[]
  def changed(fd):
   info=original(fd)
   if stat.S_ISREG(info.st_mode):
    seen.append(1)
    if len(seen)>1:
     values={k:getattr(info,k) for k in ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')};values['st_ctime_ns']+=1;return SimpleNamespace(**values)
   return info
  with patch.object(E.os,'fstat',changed):
   with self.assertRaisesRegex(E.Refusal,'FILE_RACED'):self.fs.checked(path,self.uid,0o600,5)
 def test_intent_directory_mode_guard(self):
  directory=self.root/'intent';directory.mkdir(mode=0o700)
  with patch.dict(E.SELECTION,{'intent':str(directory)}):
   if self.uid==1000 and self.gid==1000:self.fs.check_intent()
   directory.chmod(0o755)
   with self.assertRaisesRegex(E.Refusal,'INTENT_DIRECTORY_GUARD'):self.fs.check_intent()
 def test_wrong_kernel_owner_prevents_creation(self):
  with self.assertRaisesRegex(E.Refusal,'KERNEL_INSTALL_IDENTITY'):self.fs.create_dir(self.root/'target',self.uid+1,self.gid)
  self.assertFalse((self.root/'target').exists())

class Transport(unittest.TestCase):
 def run_fake(self,status=0,forever=False,overflow=False,launch_error=False,samples=None,deadline=1):
  class Stream:
   def __init__(self,fd):self.fd=fd;self.closed=False
   def fileno(self):return self.fd
   def close(self):self.closed=True
  class Process:
   pid=333
   waits=0
   def __init__(self):self.stdout=Stream(90);self.stderr=Stream(91)
   def poll(self):return None if forever or overflow else status
   def kill(self):raise AssertionError('KILL')
   def terminate(self):raise AssertionError('TERMINATE')
   def wait(self):raise AssertionError('WAIT')
  process=Process()
  class Selector:
   def __init__(self):self.items={}
   def register(self,stream,*args):self.items[stream.fd]=stream
   def unregister(self,stream):self.items.pop(stream.fd)
   def get_map(self):return self.items
   def select(self,timeout):
    assert E.finite_seconds(timeout) and 0<timeout<=.05
    process.waits+=1
    return [] if forever else [(type('Key',(),{'fileobj':v})(),1) for v in list(self.items.values())]
   def close(self):pass
  tick=[0.];reads={90:0,91:0};launches=[]
  clock=iter(samples) if samples is not None else None
  def now():
   if clock is not None:
    try:return next(clock)
    except StopIteration:raise OSError('BOUNDED_FAKE_CLOCK_EXHAUSTED')
   tick[0]+=.01;return tick[0]
  def read(fd,limit):
   reads[fd]+=1
   if fd==91:return b''
   if overflow:return b'x'*8192
   return b'{}' if reads[fd]==1 else b''
  def launch(*a,**kw):
   launches.append((a,kw))
   if launch_error:raise OSError('PRIVATE')
   return process
  child=E.Child()
  with patch.object(E.subprocess,'Popen',launch),patch.object(E.selectors,'DefaultSelector',Selector),patch.object(E.time,'monotonic',now),patch.object(E.os,'set_blocking',lambda *a:None),patch.object(E.os,'read',read):raw=child.run(['/fixed'],{'PATH':'/usr/bin'},deadline)
  return child,raw,process,launches
 def test_positive_requires_zero_and_both_eof(self):
  child,raw,p,launches=self.run_fake();self.assertEqual(raw,b'{}');self.assertEqual(child.receipt['outcome'],'EXITED_ZERO_EOF');self.assertTrue(p.stdout.closed and p.stderr.closed);self.assertFalse(launches[0][1]['shell']);self.assertEqual(launches[0][1]['umask'],0o077);self.assertEqual(launches[0][1]['cwd'],'/')
 def test_nonzero_known_failure(self):
  child,raw,_,_=self.run_fake(status=7);self.assertIsNone(raw);self.assertEqual(child.receipt['outcome'],'EXITED_NONZERO_EOF')
 def test_timeout_unknown_no_second_launch(self):
  child,raw,_,launches=self.run_fake(forever=True);self.assertIsNone(raw);self.assertEqual(child.receipt['outcome'],'TIMEOUT_UNKNOWN');self.assertEqual(len(launches),1)
 def test_overflow_unknown(self):
  child,raw,_,_=self.run_fake(overflow=True);self.assertIsNone(raw);self.assertEqual(child.receipt['outcome'],'OUTPUT_BOUND_UNKNOWN')
 def test_launch_exception_unknown(self):
  child,raw,_,launches=self.run_fake(launch_error=True);self.assertIsNone(raw);self.assertEqual(child.receipt['outcome'],'LAUNCH_UNKNOWN');self.assertEqual(len(launches),1)

class CompletionHandoff(unittest.TestCase):
 setUp=Flow.setUp
 tearDown=Flow.tearDown
 installed=Flow.installed
 def test_visible_positive_after_fsync_or_readback_failure_does_not_launch(self):
  for phase in ('parent-fsync','checked-readback'):
   with self.subTest(phase=phase):
    class VisibleThenFail(FakeFiles):
     def record(self,name,value,root=False):
      super().record(name,value,root)
      if name=='final.json':raise OSError(phase)
    fs=VisibleThenFail();r=E.install(fs,RootChild(fs))
    self.assertEqual(r['reason'],'INSTALL_FINAL_NOT_DURABLE');self.assertEqual(r['status'],'UNKNOWN')
    self.assertEqual(fs.records['final.json']['status'],'INSTALLED_NOT_OBSERVED')
    observer=ObserveChild(fs);v=E.verify_then_observe(fs,observer)
    self.assertEqual(v['reason'],'INSTALL_COMPLETION_UNBOUND');self.assertEqual(observer.calls,[])
    self.assertNotIn('observe-launch.json',fs.records)
    with self.assertRaisesRegex(E.Refusal,'INSTALLER_CAPTURE_NOT_COMPLETE'):
     E.completion_capture(E.canonical(r),{'outcome':'EXITED_ZERO_EOF','pid':987,'returncode':0,'eof':True},'a'*64)
 def test_unbound_despite_successful_visible_final_refuses(self):
  fs=FakeFiles();E.install(fs,RootChild(fs));child=ObserveChild(fs)
  r=E.verify_then_observe(fs,child);self.assertEqual(r['reason'],'INSTALL_COMPLETION_UNBOUND');self.assertFalse(child.calls)
 def test_external_capture_nonpositive_or_missing_receipt_refuses(self):
  fs=self.installed();raw=E.canonical(fs.records['final.json'])
  for receipt in (None,{}, {'outcome':'EXITED_ZERO_EOF','pid':1,'returncode':False,'eof':True}, {'outcome':'TIMEOUT_UNKNOWN','pid':1,'returncode':None,'eof':False}):
   with self.subTest(receipt=receipt),self.assertRaisesRegex(E.Refusal,'INSTALLER_CAPTURE_CHILD_NOT_POSITIVE'):E.completion_capture(raw,receipt,'a'*64)
 def test_incorrect_pin_refuses_before_launch(self):
  fs=self.installed();child=ObserveChild(fs)
  with patch.object(E,'INSTALL_COMPLETION_CAPTURE_SHA256','0'*64):r=E.verify_then_observe(fs,child)
  self.assertEqual(r['reason'],'INSTALL_COMPLETION_CAPTURE_PIN');self.assertFalse(child.calls)
 def test_wrong_source_plan_intent_or_stdout_digest_refuses(self):
  fs=self.installed();original=E.parse_json(E.INSTALL_COMPLETION_CAPTURE_TEXT.encode())
  for key,value in [('installer_sha256','0'*64),('plan_id','wrong'),('intent','wrong'),('stdout_sha256','0'*64)]:
   capture=copy.deepcopy(original);capture[key]=value;raw=E.canonical(capture);child=ObserveChild(fs)
   with patch.object(E,'INSTALL_COMPLETION_CAPTURE_TEXT',raw.decode()),patch.object(E,'INSTALL_COMPLETION_CAPTURE_SHA256',hashlib.sha256(raw).hexdigest()):r=E.verify_then_observe(fs,child)
   self.assertEqual(r['reason'],'INSTALL_COMPLETION_CAPTURE_BINDING');self.assertFalse(child.calls)
 def test_visible_final_changes_after_capture_refuse(self):
  fs=self.installed();fs.records['final.json']['stages'].append({'stage':'not-captured'});child=ObserveChild(fs)
  r=E.verify_then_observe(fs,child);self.assertEqual(r['reason'],'INSTALL_COMPLETION_FINAL_DIFFERS');self.assertFalse(child.calls)
 def test_selected_capture_does_not_bypass_actual_closure(self):
  fs=self.installed();fs.files[E.role_rows(1000)[0]['future_path']]['raw']=b'tampered';child=ObserveChild(fs)
  r=E.verify_then_observe(fs,child);self.assertEqual(r['reason'],'ACTUAL_FILE_PIN');self.assertFalse(child.calls)
 def test_selected_capture_is_not_observation_enablement(self):
  self.installed();self.assertFalse(E.OBSERVE_ENACTED)
 def test_unknown_external_stdout_never_selectable_even_positive_status(self):
  fs=FakeFiles();fs.fail='record:final.json';r=E.install(fs,RootChild(fs))
  with self.assertRaisesRegex(E.Refusal,'INSTALLER_CAPTURE_NOT_COMPLETE'):bind_synthetic_completion(self,r)
 def test_capture_canonical_output_and_unknown_fields(self):
  fs=self.installed();r=fs.records['final.json'];child={'outcome':'EXITED_ZERO_EOF','pid':987,'returncode':0,'eof':True}
  with self.assertRaisesRegex(E.Refusal,'INSTALLER_CAPTURE_NONCANONICAL'):E.completion_capture(json.dumps(r).encode(),child,'a'*64)
  capture=E.parse_json(E.INSTALL_COMPLETION_CAPTURE_TEXT.encode());capture['authority']=True;raw=E.canonical(capture)
  with patch.object(E,'INSTALL_COMPLETION_CAPTURE_TEXT',raw.decode()),patch.object(E,'INSTALL_COMPLETION_CAPTURE_SHA256',hashlib.sha256(raw).hexdigest()):
   observer=ObserveChild(fs);v=E.verify_then_observe(fs,observer)
  self.assertEqual(v['reason'],'INSTALL_COMPLETION_CAPTURE_BINDING');self.assertFalse(observer.calls)

class ClockDomains(unittest.TestCase):
 run_fake=Transport.run_fake
 def test_invalid_deadlines_prevent_launch(self):
  for value in (float('inf'),float('-inf'),float('nan'),True,False,-1,'30',None,10**1000):
   with self.subTest(value=str(value)[:20]):
    child,raw,p,launches=self.run_fake(deadline=value)
    self.assertIsNone(raw);self.assertEqual(launches,[]);self.assertEqual(p.waits,0);self.assertEqual(child.receipt['outcome'],'NOT_LAUNCHED_CLOCK_INVALID')
 def test_invalid_initial_samples_prevent_launch(self):
  for value in (float('inf'),float('-inf'),float('nan'),True,-1,'0',None,10**1000):
   with self.subTest(value=str(value)[:20]):
    child,raw,p,launches=self.run_fake(samples=[value]);self.assertFalse(launches);self.assertIsNone(raw);self.assertEqual(p.waits,0)
 def test_invalid_after_launch_retains_unknown_without_wait(self):
  for value in (float('inf'),float('-inf'),float('nan'),True,-1,'0',None,10**1000):
   with self.subTest(value=str(value)[:20]):
    child,raw,p,launches=self.run_fake(samples=[0.,value],forever=True)
    self.assertIsNone(raw);self.assertEqual(len(launches),1);self.assertEqual(p.waits,0);self.assertEqual(child.receipt['outcome'],'CLOCK_INVALID_UNKNOWN');self.assertEqual(child.receipt['pid'],333);self.assertTrue(p.stdout.closed and p.stderr.closed)
 def test_regression_before_wait_is_unknown(self):
  child,raw,p,launches=self.run_fake(samples=[0.,.2,.1],forever=True)
  self.assertEqual(child.receipt['outcome'],'CLOCK_INVALID_UNKNOWN');self.assertEqual(p.waits,0);self.assertEqual(len(launches),1)
 def test_invalid_after_one_wait_no_retry(self):
  child,raw,p,launches=self.run_fake(samples=[0.,.1,.2,float('-inf')],forever=True)
  self.assertEqual(child.receipt['outcome'],'CLOCK_INVALID_UNKNOWN');self.assertEqual(p.waits,1);self.assertEqual(len(launches),1);self.assertEqual(child.receipt['elapsed_seconds'],.2)
 def test_equal_or_expired_deadline_no_launch(self):
  for value in (0.,.1):
   child,raw,p,launches=self.run_fake(samples=[.1],deadline=value);self.assertFalse(launches);self.assertEqual(child.receipt['outcome'],'NOT_LAUNCHED_DEADLINE')
 def test_equal_clock_samples_fractional_deadline_are_valid(self):
  child,raw,p,launches=self.run_fake(samples=[0.,0.,0.,.01,.01,.02,.02,.02],deadline=.5)
  self.assertEqual(raw,b'{}');self.assertEqual(child.receipt['outcome'],'EXITED_ZERO_EOF')
 def test_deadline_reached_after_poll_prevents_terminal_acceptance(self):
  child,raw,p,launches=self.run_fake(samples=[0.,.1,.1,.2,.2,.3,1.],deadline=1)
  self.assertIsNone(raw);self.assertEqual(child.receipt['outcome'],'TIMEOUT_UNKNOWN')

class Disabled(unittest.TestCase):
 def test_unbound_main_and_owner_home_refuse_before_io(self):
  import contextlib,io
  fs=E.Files()
  with patch.object(E,'SELECTION',None),patch.object(E,'OWNER_HOME',None),patch.object(E.os,'open',side_effect=AssertionError('UNBOUND_IO')):
   with self.assertRaisesRegex(E.Refusal,'OWNER_HOME_UNBOUND'):E.expected_owner('/')
   with self.assertRaisesRegex(E.Refusal,'OWNER_HOME_UNBOUND'):fs.tools()
   output=io.StringIO()
   with patch.object(E,'Files',side_effect=AssertionError('UNBOUND_FILES')),patch.object(E,'Child',side_effect=AssertionError('UNBOUND_CHILD')),contextlib.redirect_stdout(output):
    self.assertEqual(E.main(),1)
   self.assertEqual(output.getvalue(),'REFUSED_UNBOUND\n')
 def test_all_operational_flags_false_and_root_program_unbound(self):
  self.assertFalse(E.INSTALL_ENACTED);self.assertFalse(E.ROOT_STAGE_ENACTED);self.assertFalse(E.OBSERVE_ENACTED);self.assertIsNone(E.ROOT_PROGRAM_TEXT);self.assertIsNone(E.ROOT_PROGRAM_SHA256);self.assertIsNone(E.INSTALL_COMPLETION_CAPTURE_TEXT);self.assertIsNone(E.INSTALL_COMPLETION_CAPTURE_SHA256);self.assertIsNone(E.SELECTED_INSTALLER_SHA256)
 def test_root_program_mismatched_hash_refuses(self):
  with patch.object(E,'ROOT_PROGRAM_TEXT','fixed'),patch.object(E,'ROOT_PROGRAM_SHA256','0'*64):
   with self.assertRaisesRegex(E.Refusal,'ROOT_PROGRAM_UNBOUND'):E.root_program()

class FocusProperties(unittest.TestCase):
 setUp=Flow.setUp
 tearDown=Flow.tearDown
 installed=Flow.installed
 def test_root_start_failure_precedes_any_helper_mutation(self):
  fs=FakeFiles();fs.user=0;fs.records['started.json']={'schema':'F1.install-intent/1','plan_id':E.SELECTION['plan_id'],'state':'STARTED_NO_RETRY'};fs.fail='record:root-start.json'
  result=E.root_stage(fs);self.assertEqual(result['status'],'UNKNOWN');self.assertNotIn(E.role_dir(0)['path'],fs.dirs);self.assertFalse(fs.files)
 def test_root_checks_collector_absence_before_helper_mutation(self):
  fs=FakeFiles();fs.user=0;fs.records['started.json']={'schema':'F1.install-intent/1','plan_id':E.SELECTION['plan_id'],'state':'STARTED_NO_RETRY'};fs.dirs[E.role_dir(1000)['path']]={}
  result=E.root_stage(fs);self.assertEqual(result.get('reason'),'DESTINATION_EXISTS');self.assertNotIn(E.role_dir(0)['path'],fs.dirs)
 def test_post_marker_closure_tamper_blocks_child(self):
  fs=self.installed();old=fs.record
  def record(name,value,root=False):
   old(name,value,root)
   if name=='observe-launch.json':fs.files[E.role_rows(1000)[0]['future_path']]['raw']=b'changed-after-marker'
  fs.record=record;child=ObserveChild(fs);result=E.verify_then_observe(fs,child)
  self.assertEqual(result.get('reason'),'ACTUAL_FILE_PIN');self.assertEqual(child.calls,[])
 def test_post_marker_output_creation_blocks_child(self):
  fs=self.installed();old=fs.record
  def record(name,value,root=False):
   old(name,value,root)
   if name=='observe-launch.json':fs.dirs[E.SELECTION['output']]={}
  fs.record=record;child=ObserveChild(fs);result=E.verify_then_observe(fs,child)
  self.assertEqual(result.get('reason'),'DESTINATION_EXISTS');self.assertEqual(child.calls,[])
 def test_coordinator_initial_tool_pin_failure_precedes_intent(self):
  fs=FakeFiles();fs.fail='tools:install';child=RootChild(fs);result=E.install(fs,child)
  self.assertEqual(result['status'],'UNKNOWN');self.assertEqual(child.calls,[]);self.assertEqual(fs.dirs,{});self.assertEqual(fs.records,{})
 def test_coordinator_rechecks_tools_after_marker(self):
  fs=FakeFiles();old=fs.record
  def record(name,value,root=False):
   old(name,value,root)
   if name=='root-launch.json':fs.fail='tools:install'
  fs.record=record;child=RootChild(fs);result=E.install(fs,child)
  self.assertEqual(result['status'],'UNKNOWN');self.assertFalse(child.calls);self.assertNotIn(E.role_dir(0)['path'],fs.dirs)
 def test_started_content_is_exact_not_any_dict(self):
  valid={'schema':'F1.install-intent/1','plan_id':E.SELECTION['plan_id'],'state':'STARTED_NO_RETRY'}
  for change in ({'schema':'wrong'},{'plan_id':'wrong'},{'state':'wrong'},{'extra':True}):
   fs=FakeFiles();fs.user=0;fs.records['started.json']={**valid,**change};result=E.root_stage(fs)
   self.assertEqual(result.get('reason'),'INSTALL_INTENT');self.assertFalse(fs.dirs);self.assertNotIn('root-start.json',fs.records)

class FocusPrimitives(unittest.TestCase):
 setUp=FilePrimitives.setUp
 tearDown=FilePrimitives.tearDown
 layout=FilePrimitives.layout
 def test_write_new_parent_fsync_follows_file_and_failure_visible(self):
  path=self.root/'record';parent_inode=self.root.stat().st_ino;events=[];real=E.os.fsync
  def sync(fd):
   inode=os.fstat(fd).st_ino;events.append(inode)
   if inode==parent_inode:raise OSError('SYNTHETIC_PARENT_FSYNC')
   real(fd)
  with patch.object(E.os,'fsync',sync):
   with self.assertRaisesRegex(OSError,'SYNTHETIC_PARENT_FSYNC'):self.fs.write_new(path,b'positive-visible-bytes',self.uid,self.gid,0o600)
  self.assertEqual(events,[path.stat().st_ino,parent_inode]);self.assertEqual(path.read_bytes(),b'positive-visible-bytes')
 def test_verify_dir_direct_fd_race_not_just_reopened_path(self):
  from types import SimpleNamespace
  directory,rows,descriptor=self.layout();real=E.os.fstat;original_open=self.fs.open_dir;original_checked=self.fs.checked;selected=[None];finished=[0]
  def opened(path):
   fd=original_open(path)
   if selected[0] is None:selected[0]=fd
   return fd
  def checked(*args,**kwargs):
   value=original_checked(*args,**kwargs);finished[0]+=1;return value
  def changed(fd):
   info=real(fd)
   if fd==selected[0] and finished[0]==3:
    values={k:getattr(info,k) for k in ('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')};values['st_ctime_ns']+=1;return SimpleNamespace(**values)
   return info
  with patch.object(self.fs,'open_dir',opened),patch.object(self.fs,'checked',checked),patch.object(E.os,'fstat',changed):
   with self.assertRaisesRegex(E.Refusal,'DIRECTORY_RACED'):self.fs.verify_dir(descriptor,rows)
 def test_setgid_parent_refuses_before_creation(self):
  parent=self.root/'setgid';parent.mkdir(mode=0o700);parent.chmod(0o2700)
  with self.assertRaisesRegex(E.Refusal,'PARENT_SETGID'):self.fs.check_parent(parent/'new')
  self.assertFalse((parent/'new').exists())

class SignalDisposition(unittest.TestCase):
 run_fake=Transport.run_fake
 def test_ignored_sigchld_refuses_before_fake_launch(self):
  with patch.object(E.signal,'getsignal',return_value=E.signal.SIG_IGN):child,raw,p,launches=self.run_fake()
  self.assertFalse(launches);self.assertEqual(child.receipt['outcome'],'NOT_LAUNCHED_SIGCHLD')
 def test_changed_before_or_after_poll_keeps_unknown(self):
  for sequence in ([E.signal.SIG_DFL,E.signal.SIG_IGN],[E.signal.SIG_DFL,E.signal.SIG_DFL,E.signal.SIG_IGN]):
   with patch.object(E.signal,'getsignal',side_effect=sequence):child,raw,p,launches=self.run_fake()
   self.assertIsNone(raw);self.assertEqual(len(launches),1);self.assertEqual(child.receipt['outcome'],'SIGCHLD_CHANGED_UNKNOWN');self.assertFalse(E.positive_child(child.receipt))

class PhaseBudgets(unittest.TestCase):
 setUp=Flow.setUp
 tearDown=Flow.tearDown
 installed=Flow.installed
 def test_check_deadline_and_root_budget_invalid_or_regressing(self):
  for values in ([float('inf')],[0.,float('-inf')],[1.,.5],[0.,True]):
   samples=iter(values);clock=E.Clock(lambda:next(samples))
   with self.assertRaises((E.Refusal,OverflowError)):
    budget=clock.budget(30);E.check_deadline(None,budget)
  fs=FakeFiles();fs.user=0;fs.now=lambda:float('inf');r=E.root_stage(fs);self.assertEqual(r['status'],'UNKNOWN');self.assertFalse(fs.records)
 def test_fresh_child40_not_remaining_preflight_then_fresh_postflight(self):
  fs=FakeFiles();oldtools=fs.tools
  def slowtools(install_only=False):oldtools(install_only);fs.clock+=10
  fs.tools=slowtools;child=RootChild(fs);run=child.run;seen=[]
  def slowchild(argv,env,deadline):
   seen.append(deadline-fs.clock);raw=run(argv,env,deadline);fs.clock+=30;return raw
  child.run=slowchild;r=E.install(fs,child)
  self.assertEqual(r['status'],'INSTALLED_NOT_OBSERVED');self.assertAlmostEqual(seen[0],40);self.assertEqual(r['budgets']['child_intake'],40);self.assertEqual(r['budgets']['wrapper_envelope'],105)
 def test_root_work_and_final_publication_fit_after_startup(self):
  # R-S1: 30s root work plus 5s final publication leaves no startup in 35s.
  fs=FakeFiles();finish=fs.finish_dir;record=fs.record
  def near_work(path,uid,gid,mode):
   finish(path,uid,gid,mode)
   if uid==0:fs.clock+=29
  def near_final(name,value,root=False):
   record(name,value,root)
   if name=='root-final.json':fs.clock+=4.5
  fs.finish_dir=near_work;fs.record=near_final
  child=RootChild(fs);run=child.run
  def timed(argv,env,deadline):
   fs.clock+=4 # Fixed startup delay, not part of root work/publication.
   raw=run(argv,env,deadline)
   if fs.clock>=deadline:child.receipt.update(outcome='TIMEOUT_UNKNOWN',eof=False);return None
   return raw
  child.run=timed;result=E.install(fs,child)
  self.assertEqual(result['status'],'INSTALLED_NOT_OBSERVED')
  self.assertEqual(result['budgets']['child_intake'],40)
  self.assertEqual(result['budgets']['child_final_publication'],5)
  self.assertEqual(result['budgets']['child_startup_margin'],5)
 def test_preflight_exhaustion_before_intent(self):
  fs=FakeFiles();original=fs.tools
  def slow(install_only=False):original(install_only);fs.clock+=31
  fs.tools=slow;child=RootChild(fs);r=E.install(fs,child)
  self.assertEqual(r['reason'],'COOPERATIVE_DEADLINE');self.assertFalse(fs.records);self.assertFalse(child.calls)
 def test_final_publication_overrun_preserves_unknown_despite_visible_positive(self):
  fs=FakeFiles();original=fs.record
  def slow(name,value,root=False):
   original(name,value,root)
   if name=='final.json':fs.clock+=6
  fs.record=slow;r=E.install(fs,RootChild(fs));self.assertEqual(r['reason'],'INSTALL_FINAL_NOT_DURABLE');self.assertEqual(fs.records['final.json']['status'],'INSTALLED_NOT_OBSERVED')
 def test_observe_child35_and_phase_overrun(self):
  fs=self.installed();child=ObserveChild(fs);run=child.run;seen=[]
  def slow(argv,env,deadline):seen.append(deadline-fs.clock);r=run(argv,env,deadline);fs.clock+=30;return r
  child.run=slow;r=E.verify_then_observe(fs,child);self.assertEqual(r['status'],'COLLECTOR_EXITED_WITH_FINAL');self.assertAlmostEqual(seen[0],35)

class CaptureSupervisor(unittest.TestCase):
 setUp=Flow.setUp
 tearDown=Flow.tearDown
 def layout(self,fs):
  directory={'path':'/synthetic-executor','uid':1000,'gid':1000,'initial_mode':'0700','final_mode':'0700'};rows=[];fs.dirs[directory['path']]={'uid':1000,'gid':1000,'mode':0o700}
  for name in ('installer.py','root_stage.py','verify_invoke.py'):
   raw=('synthetic source '+name).encode();path=directory['path']+'/'+name;fs.files[path]={'uid':1000,'gid':1000,'mode':0o600,'raw':raw};rows.append({'future_path':path,'future_uid':1000,'future_mode':'0600','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
  return {'directory':directory,'files':rows}
 def fixture(self,kind='install',mode='complete'):
  fs=FakeFiles();layout=self.layout(fs);fs.environment=dict(E.INSTALL_ENV if kind=='install' else E.SELECTION['observe_env'])
  for key,value in [('EXECUTOR_LAYOUT',layout),('CAPTURE_KIND',kind)]:
   h=patch.object(E,key,value);h.start();self.addCleanup(h.stop)
  case=self
  class WrapperChild:
   def __init__(self):self.receipt=None;self.calls=[]
   def run(self,argv,env,deadline):
    self.calls.append((argv,env,deadline));self.receipt={'outcome':'TIMEOUT_UNKNOWN','pid':600,'returncode':None,'eof':False}
    if mode=='unknown':return None
    if kind=='install':value=E.install(fs,RootChild(fs))
    else:
     installed=E.install(fs,RootChild(fs));bind_synthetic_completion(case,installed);value=E.verify_then_observe(fs,ObserveChild(fs))
    if mode=='nonpositive':return None
    self.receipt={'outcome':'EXITED_ZERO_EOF','pid':600,'returncode':0,'eof':True,'sigchld_before_launch':'SIG_DFL_OBSERVED','sigchld_last':'SIG_DFL_OBSERVED'}
    if mode=='tamper-final':fs.records['final.json' if kind=='install' else 'observe-final.json']['extra']='not-output'
    return E.canonical(value)
  return fs,WrapperChild(),layout
 def test_unbound_supervisor_no_reads_or_child(self):
  fs=FakeFiles();child=RootChild(fs);r=E.capture_wrapper(fs,child)
  self.assertEqual(r['reason'],'CAPTURE_KIND_UNBOUND');self.assertFalse(fs.events);self.assertFalse(child.calls)
 def test_install_capture_positive_actual_final_and_fixed_launch(self):
  fs,child,layout=self.fixture();r=E.capture_wrapper(fs,child)
  self.assertEqual(r['status'],'CAPTURED_INSTALL_COMPLETION');self.assertEqual(len(child.calls),1);self.assertEqual(child.calls[0][0],['/usr/bin/python3','-I','-B','/synthetic-executor/installer.py']);self.assertEqual(child.calls[0][1],E.INSTALL_ENV)
  self.assertEqual(r['capture']['final_sha256'],hashlib.sha256(E.canonical(fs.records['final.json'])).hexdigest());self.assertEqual(r['capture']['installer_sha256'],layout['files'][0]['sha256']);self.assertFalse(E.CAPTURE_ENACTED)
 def test_unknown_child_no_retry_even_missing_install_intent(self):
  fs,child,_=self.fixture(mode='unknown');r=E.capture_wrapper(fs,child);self.assertEqual(r['reason'],'SUPERVISED_CHILD_NOT_POSITIVE');self.assertFalse(fs.records)
  E.capture_wrapper(fs,child);self.assertEqual(len(child.calls),1)
 def test_marker_post_store_failure_prevents_launch_and_no_retry(self):
  fs,child,_=self.fixture();original=fs.write_new
  def fail(*args):original(*args);raise OSError('SYNTHETIC_MARKER_FSYNC')
  fs.write_new=fail;r=E.capture_wrapper(fs,child);self.assertEqual(r['status'],'UNKNOWN');self.assertFalse(child.calls)
  fs.write_new=original;E.capture_wrapper(fs,child);self.assertFalse(child.calls)
 def test_layout_tamper_after_marker_prevents_launch(self):
  fs,child,layout=self.fixture();original=fs.write_new
  def changed(*args):
   value=original(*args);fs.files[layout['files'][0]['future_path']]['raw']=b'changed';return value
  fs.write_new=changed;r=E.capture_wrapper(fs,child);self.assertEqual(r['reason'],'ACTUAL_FILE_PIN');self.assertFalse(child.calls)
 def test_actual_final_must_match_exact_producer_bytes(self):
  fs,child,_=self.fixture(mode='tamper-final');r=E.capture_wrapper(fs,child);self.assertEqual(r['reason'],'SUPERVISED_FINAL_DIFFERS');self.assertNotIn('capture',r)
 def test_observe_final_requires_producer_observed_completion(self):
  fs,child,_=self.fixture(kind='observe');r=E.capture_wrapper(fs,child);self.assertEqual(r['status'],'CAPTURED_VERIFIER_COMPLETION');self.assertEqual(r['capture']['acceptance'],'NOT_ASSESSED')
 def test_positive_visible_observe_final_and_failed_producer_cannot_capture(self):
  fs,child,_=self.fixture(kind='observe');original=fs.record
  def fail(name,value,root=False):
   original(name,value,root)
   if name=='observe-final.json':raise OSError('AFTER_VISIBLE')
  fs.record=fail;r=E.capture_wrapper(fs,child)
  self.assertEqual(fs.records['observe-final.json']['status'],'COLLECTOR_EXITED_WITH_FINAL');self.assertEqual(r['status'],'UNKNOWN');self.assertNotIn('capture',r)
 def test_capture_rejects_wrong_final_digest_and_new_capture_field(self):
  fs,child,_=self.fixture();r=E.capture_wrapper(fs,child);capture=r['capture'];capture['final_sha256']='0'*64;raw=E.canonical(capture)
  with patch.object(E,'INSTALL_COMPLETION_CAPTURE_TEXT',raw.decode()),patch.object(E,'INSTALL_COMPLETION_CAPTURE_SHA256',hashlib.sha256(raw).hexdigest()),patch.object(E,'SELECTED_INSTALLER_SHA256',capture['installer_sha256']):
   with self.assertRaisesRegex(E.Refusal,'INSTALL_COMPLETION_CAPTURE_BINDING'):E.selected_completion(fs.records['final.json'],E.canonical(fs.records['final.json']))
 def test_outer_intake110_and_fixed_marker_no_success_marker(self):
  fs,child,_=self.fixture(mode='unknown');run=child.run;seen=[]
  def remember(argv,env,deadline):seen.append(deadline-fs.clock);return run(argv,env,deadline)
  child.run=remember;r=E.capture_wrapper(fs,child);self.assertAlmostEqual(seen[0],110);self.assertEqual(len([p for p in fs.files if 'supervisor-launch' in p]),1);self.assertEqual(r['status'],'UNKNOWN')

 def test_supervisor_envelopes_contain_wrapper_and_startup(self):
  for kind,wrapper,intake,supervisor,external in [('install',105,110,175,180),('observe',100,105,170,175)]:
   with self.subTest(kind=kind):
    fs,child,_=self.fixture(kind=kind);run=child.run
    def near_envelope(argv,env,deadline):
     start=fs.clock;raw=run(argv,env,deadline)
     fs.clock=max(fs.clock,start+wrapper+4)
     if fs.clock>=deadline:child.receipt.update(outcome='TIMEOUT_UNKNOWN',eof=False);return None
     return raw
    child.run=near_envelope;result=E.capture_wrapper(fs,child)
    self.assertEqual(result['status'],'CAPTURED_INSTALL_COMPLETION' if kind=='install' else 'CAPTURED_VERIFIER_COMPLETION')
    budgets=result['budgets']
    self.assertEqual((budgets['wrapper_envelope'],budgets['child_intake'],budgets['supervisor_envelope'],budgets['external_observer_allowance']),(wrapper,intake,supervisor,external))

if __name__=='__main__':unittest.main(verbosity=2)

