"""Selected weekly store/workspace owners. Effects require separate selection.

No native admission, provider launch, containment certificate or grading. The
original preparation allocation pays retained graphs and failure frames; its
physical sufficiency is not proved by this source composition.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

from trial_paired_request import canonical_sha, need, pin, plain_path


class WeeklyCluster:
    """One newly created cluster, never an environment-selected existing store."""
    def __init__(self, environment):
        self.environment=environment
        self.root=environment.root/'cluster';self.pg_bin=environment.pg_bin
        self.root_identity=None
        self.state='new';self.start_attempted=False;self.cleanup_attempted=False
        self.status=None;self.commands=[]

    def prepare(self):
        from trial_task_eval import run
        need(self.state=='new','weekly_cluster_consumed')
        self.state='creating'
        try:
            self.root.mkdir(mode=0o700)
            info=self.root.lstat()
            self.root_identity=(info.st_dev,info.st_ino)
            # This exclusive root is retained even if initdb/start is interrupted.
            self.commands.append('initdb')
            run([self.pg_bin/'initdb','-D',self.root/'data','--auth-local=trust',
                 '--auth-host=reject','--no-locale','-E','UTF8'])
            (self.root/'socket').mkdir(mode=0o700)
            self.start_attempted=True
            self.commands.append('start')
            run([self.pg_bin/'pg_ctl','-D',self.root/'data','-l',self.root/'postgres.log',
                 '-o',f"-F -k {self.root/'socket'} -c listen_addresses='' -c max_connections=200",'-w','start'])
            self.state='running'
        except BaseException:
            self.state='unknown'
            raise

    def routing(self):
        need(self.state=='running' and not self.cleanup_attempted,'weekly_cluster_unavailable')
        return str(self.root/'socket'),self.pg_bin

    def close(self):
        from trial_task_eval import run
        if getattr(self,'cleanup_attempted',False):return self.state=='closed'
        environment=getattr(self,'environment',None)
        if environment is None or not environment.runs_closed():return False
        if any(not owner._process_closed() or getattr(owner,'close_states',{}).get('process')=='unknown'
               for owner in environment.stores.values()):return False
        if not getattr(self,'root_identity',None):return False
        self.cleanup_attempted=True
        try:
            info=self.root.lstat()
            need((info.st_dev,info.st_ino)==self.root_identity and not self.root.is_symlink(),
                 'weekly_cluster_identity')
            if self.start_attempted or (self.root/'data/postmaster.pid').exists():
                self.status=run([self.pg_bin/'pg_ctl','-D',self.root/'data','status'],check=False).returncode
                if self.status==0:
                    run([self.pg_bin/'pg_ctl','-D',self.root/'data','-m','immediate','-w','stop'],check=False)
                    self.status=run([self.pg_bin/'pg_ctl','-D',self.root/'data','status'],check=False).returncode
                if self.status!=3:
                    self.state='cleanup_unknown';return False
            shutil.rmtree(self.root)
            self.state='closed';return True
        except BaseException:
            self.state='cleanup_unknown'
            raise


class WeeklyStoreOwner:
    def __init__(self, environment, arm):
        self.environment=environment;self.arm=arm;self.store=None
        self.state='new';self.close_states={};self.wait=None;self.faults=[]
        self.provenance=None;self.before=self.after=None

    def prepare(self):
        import trial_task_eval as legacy
        need(self.state=='new','weekly_store_consumed')
        self.state='creating'
        selected=self.environment.selected
        data=selected._runtime_selection
        config=data['execution']['memory_arms'][self.arm]
        for key in ('binary','embedding_worker','embedding_config','embedding_model_identity'):
            pin(config[key])
        self.store=legacy.TrialStore.__new__(legacy.TrialStore)
        try:
            legacy.TrialStore.__init__(self.store,self.environment.root/'stores'/self.arm,
                config['binary']['path'],self.arm.lower(),embedding_worker=config['embedding_worker']['path'],
                cluster=self.environment.cluster)
            self.state='importing'
            notes=data['notes'];growth=data['distractor_notes']
            need(len(growth)==1900,'weekly_growth_changed')
            all_ids=[n['id'] for n in notes+growth]
            need(len(set(all_ids))==len(all_ids),'weekly_duplicate_input_id')
            self.store.seed_corpus(notes,workers=1)
            # Use the original grow boundary; verify its deterministic inputs
            # against the actual held selection before causing import effects.
            generated=[legacy.distractor(i) for i in range(1900)]
            need(canonical_sha(generated)==canonical_sha(growth),'weekly_growth_changed')
            self.store.grow(1900,0)
            need(set(self.store.ids)==set(all_ids) and len(self.store.names)==len(all_ids)
                 and all(self.store.names.get(record)==name for name,record in self.store.ids.items()),
                 'weekly_import_identity')
            self.provenance=legacy.import_provenance(notes+growth,self.store.ids)
            self.state='starting'
            self.store.start()
            self.store.wait_for_full_coverage(timeout=self.environment.readiness_timeout)
            need(self.store.readiness is not None and self.store.readiness['state']=='full_eligible_coverage',
                 'weekly_embedding_not_ready')
            self.before=self.store.fingerprint()
            self.state='ready'
        except BaseException:
            self.state='unknown'
            raise

    def _process_closed(self):
        store=getattr(self,'store',None)
        return store is None or (getattr(store,'server',None) is None and
            not getattr(store,'_launch_attempted',False)) or self.wait is not None

    def _close_action(self,name):
        store=self.store
        if name=='stamp':
            if self.state=='ready':self.after=store.fingerprint()
        elif name=='process':
            server=getattr(store,'server',None)
            if server is not None:
                if server.poll() is None:
                    server.terminate()
                    try:self.wait=server.wait(15)
                    except subprocess.TimeoutExpired:
                        server.kill();self.wait=server.wait(15)
                else:self.wait=server.wait(15)
            else:need(not getattr(store,'_launch_attempted',False),'server_launch_unknown')
        elif name=='log':
            log=getattr(store,'log',None)
            if log is not None:log.close()
        else:
            need(self._process_closed(),'server_wait_unknown')
            if name=='token':path=getattr(store,'token_file',None)
            elif name=='identity':
                home=getattr(store,'home',None);path=home/'identities.json' if home else None
            else:path=getattr(store,'sockdir',None)
            if path is not None:
                if name=='socket':shutil.rmtree(path)
                else:path.unlink(missing_ok=True)

    def close(self):
        if getattr(self,'store',None) is None:self.state='closed';return True
        if not self.environment.runs_closed():return False
        primary=None
        try:
            for name in ('stamp','process','log','token','identity','socket'):
                if name in self.close_states:continue
                self.close_states[name]='unknown'  # One consumed/uncertain publication.
                try:
                    self._close_action(name)
                    self.close_states[name]='closed'
                except BaseException as error:
                    if primary is None:primary=error
            self.state='closed' if all(v=='closed' for v in self.close_states.values()) and self._process_closed() else 'cleanup_unknown'
        except BaseException as error:
            self.state='cleanup_unknown'
            if primary is None:primary=error
        if primary is not None:raise primary
        return self.state=='closed'


class WeeklyWorkspaceOwner:
    def __init__(self, environment, index, root):
        self.environment=environment;self.index=index;self.root=Path(root)
        self.state='new';self.cwd=None;self.prepared=None

    def prepare(self):
        import trial_task_eval as legacy
        need(self.state=='new','weekly_workspace_consumed')
        self.state='preparing'
        try:
            selected=self.environment.selected;selected.verify_runtime_selection()
            data=selected._runtime_selection;row=selected.rows[self.index]
            case=next(c for c in data['cases'] if c['id']==row['case'])
            source=data['source'];root=Path(source['root'])
            need(legacy.label_manifest(source['label_version'],root=root)==source['frozen_identity'],
                 'weekly_workspace_source_changed')
            need(str(legacy.workspace_dir(case,root=root).resolve())==data['workspace_paths'][case['id']],
                 'weekly_workspace_source_changed')
            self.root.mkdir(mode=0o700)
            self.cwd=legacy.prepare_workspace(case,self.root,source_root=root)
            need(legacy.label_manifest(source['label_version'],root=root)==source['frozen_identity'],
                 'weekly_workspace_source_changed')
            # Preserve the weekly tag and all original refs. Never reinterpret
            # this result as the detached/no-refs prospective constructor.
            refs=legacy.run(['git','for-each-ref','--format=%(refname) %(objectname)'],cwd=self.cwd).stdout.decode()
            initial_refs={}
            for line in refs.splitlines():
                name,oid=line.split(' ');need(name not in initial_refs,'weekly_refs_duplicate');initial_refs[name]=oid
            head=legacy.run(['git','rev-parse','HEAD'],cwd=self.cwd).stdout.decode().strip()
            need(initial_refs.get('refs/tags/eval-base')==head,'weekly_eval_base')
            initial=json.loads((self.root/'.eval-snapshot.json').read_text())
            self.prepared=dict(schema='cairn.weekly-owned-workspace/1',actor_path=str(self.cwd),
                base_commit=head,initial_refs=initial_refs,initial=initial,
                selection_sha256=selected._runtime_pin['sha256'],row=dict(row),
                request_id=selected.identity['request_id'],occurrence_id=selected.identity['occurrence_id'])
            self.prepared['workspace_sha256']=canonical_sha(self.prepared)
            self.state='prepared'
        except BaseException:
            self.state='unknown'
            raise

    def verify_preparation(self, preparation):
        need(self.state=='prepared' and self.prepared['workspace_sha256']==canonical_sha(
            {k:v for k,v in self.prepared.items() if k!='workspace_sha256'}),'weekly_workspace_unknown')
        need(preparation.review_bindings()['binding']['workspace_sha256']==self.prepared['workspace_sha256'],
             'weekly_workspace_binding')

    def close(self):
        # Retain task output/partial setup for the real evidence owner; no
        # containment release or deletion is inferred from a returning setup.
        if self.state in ('preparing','new'):self.state='unknown'
        return True


class WeeklyEnvironmentOwner:
    def __init__(self, selected, root, pg_bin, readiness_timeout):
        self.selected=selected;self.root=Path(root);self.pg_bin=Path(pg_bin)
        self.readiness_timeout=readiness_timeout;self.state='new'
        self.cluster=None;self.stores={};self.workspaces={};self.runs=[];self.cleanup_attempts=set();self.faults=[]

    def protect_destination(self, destination):
        destination=plain_path(str(destination))
        protected={plain_path(self.selected._runtime_selection['source']['root']),self.pg_bin}
        def pins(value):
            if type(value) is dict:
                if set(value)=={'path','sha256'}:protected.add(plain_path(value['path']))
                else:
                    for item in value.values():pins(item)
            elif type(value) is list:
                for item in value:pins(item)
        pins(self.selected._runtime_selection['execution'])
        snapshots=[self.selected.snapshot_owner]
        snapshots.extend(p._snapshot for p in self.selected._pending_preparations
                         if getattr(p,'_snapshot',None) is not None)
        for snapshot in snapshots:
            snapshot._verify_files()
            # Actual held file/directory paths, not a caller's claimed inventory.
            leases=[snapshot.directory]+[v[0] for v in snapshot.sources.values()]+[v[0] for v in snapshot.files.values()]
            for lease in leases:
                lease.check()
                protected.add(plain_path(os.readlink('/proc/self/fd/'+str(lease.fd))))
        protected.update(w.root for w in self.workspaces.values() if hasattr(w,'root'))
        if destination!=self.root:protected.add(self.root)
        need(all(destination!=source and destination not in source.parents and source not in destination.parents
                 for source in protected),'weekly_destination_overlap')
        need(not destination.exists(),'weekly_destination_exists')
        return destination

    def prepare(self):
        need(self.state=='new','weekly_environment_consumed')
        self.state='preparing'
        try:
            self.selected.verify_runtime_selection()
            self.root=self.protect_destination(self.root)
            self.root.mkdir(mode=0o700)
            self.cluster=WeeklyCluster.__new__(WeeklyCluster)
            WeeklyCluster.__init__(self.cluster,self)
            self.cluster.prepare()
            for arm in ('CURRENT','CAND'):
                owner=WeeklyStoreOwner.__new__(WeeklyStoreOwner)
                self.stores[arm]=owner
                WeeklyStoreOwner.__init__(owner,self,arm)
                owner.prepare()
            self.state='ready'  # Both stores have actual import/readiness results.
        except BaseException as primary:
            self.state='unknown';self.selected.allocation.poison(primary)
            raise

    def prepare_workspace(self,index,root):
        need(self.state=='ready' and index not in self.workspaces,'weekly_workspace_consumed')
        try:
            self.selected.verify_runtime_selection()
            need(type(index) is int and 0<=index<len(self.selected.rows),'weekly_row')
            root=self.protect_destination(root)
            owner=WeeklyWorkspaceOwner.__new__(WeeklyWorkspaceOwner)
            self.workspaces[index]=owner
            WeeklyWorkspaceOwner.__init__(owner,self,index,root)
            owner.prepare()
        except BaseException as primary:
            self.state='unknown';self.selected.allocation.poison(primary)
            raise

    def bind_run(self,run,preparation,binding,index,service_document,*args,**kwargs):
        need(self.state=='ready','weekly_environment_unavailable')
        try:
            from trial_sdk_fixed_run import FixedRunOwner
            from trial_native_bootstrap import SDKReviewPreparation
            from trial_sdk_session_resources import SDKSessionResources
            need(type(run) is FixedRunOwner and run._state=='new' and
                 type(preparation) is SDKReviewPreparation and type(binding) is SDKSessionResources
                 and run.resources is binding.resources and preparation._allocation is not None
                 and preparation._allocation.resources is binding.resources,'weekly_run_owner')
            self.runs.append(run)
            if preparation not in self.selected._pending_preparations:
                self.selected._pending_preparations.append(preparation)
            run._preparation=preparation
            run._session_binding=binding
            run._weekly_environment=self
            self.selected.verify_runtime_selection()
            workspace=self.workspaces[index];workspace.verify_preparation(preparation)
            row=self.selected.rows[index];store=self.stores[row['arm']]
            need(store.state=='ready','weekly_store_unavailable')
            policy=service_document['policy']
            from trial_task_eval import TRIAL_REPO
            need(policy['repo']==TRIAL_REPO and policy['socket']==str(store.store.socket) and policy['token_file']==str(store.store.token_file)
                 and policy['executable']==str(store.store.binary),'weekly_store_policy_binding')
            need(service_document['cwd']==str(workspace.cwd),'weekly_workspace_cwd')
            metadata=preparation.review_bindings()
            execution=self.selected._runtime_selection['execution']
            need(metadata['snapshots']['cairn']['sha256']==execution['memory_arms'][row['arm']]['binary']['sha256']
                 and metadata['snapshots']['claude']['sha256']==execution['native']['binary']['sha256'],
                 'weekly_binary_binding')
            run._weekly_workspace=workspace
            self.selected.bind_run(run,preparation,binding,index,service_document,*args,**kwargs)
        except BaseException as primary:
            self.state='unknown';self.selected.allocation.poison(primary)
            run._fail(primary)
            raise

    def runs_closed(self):
        if any(not run._sources_closed for run in getattr(self,'runs',())):return False
        for run in getattr(self,'runs',()):
            for owner in run._original_owners():
                receipt=owner.snapshot()
                if (receipt['launch_attempted'] and receipt['wait'] is None) or receipt['has_handles']:
                    return False
                release=receipt['release']
                if not release or not release['released'] or not release['handles_detached'] or release['cleanup']:
                    return False
        return True

    def close(self):
        primary=None
        if not self.runs_closed():return False
        if not getattr(self,'stores',{}) and getattr(self,'cluster',None) is None:return True
        self.state='closing'  # No new workspace or run can enter during teardown.
        # Progress is recorded per owner. An interrupt between actions does not
        # mark unattempted independent actions closed or erase their ownership.
        try:
            for arm,owner in self.stores.items():
                self.cleanup_attempts.add(arm)
                try:owner.close()
                except BaseException as error:
                    self.faults.append('store_close_unknown')
                    if primary is None:primary=error
            closed=all(getattr(owner,'state',None)=='closed' for owner in self.stores.values())
            if self.cluster is not None:
                try:self.cluster.close()
                except BaseException as error:
                    self.faults.append('cluster_close_unknown')
                    if primary is None:primary=error
            self.state='closed' if closed and (self.cluster is None or self.cluster.state=='closed') else 'cleanup_unknown'
        except BaseException as error:
            self.state='cleanup_unknown'
            if primary is None:primary=error
        if primary is not None:raise primary
        return self.state=='closed'
