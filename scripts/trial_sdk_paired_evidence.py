"""Owned original weekly preparation -> fixed run -> private evidence publication.

No launch, task evaluation, containment or grader authority. This is a trusted
source-owned path, not protection against arbitrary Python object mutation.
Original tickets remain held, including failed publication and exception graphs.
"""
import hashlib
import json
import os
import stat
from pathlib import Path
import uuid

from trial_bootstrap_snapshot import Snapshot, write_all, READ_FLAGS, file_identity, identity
from trial_native_bootstrap import SDKReviewPreparation, SDKPreparationAllocation
from trial_sdk_incoming import canonical_chunks, canonical_size_sha256
from trial_paired_request import need, canonical_sha, mcp_arguments, FILE_LIMIT
from trial_sdk_incoming import parse_document


def text_sha(text):
    digest = hashlib.sha256()
    for offset in range(0, len(text), 128):
        block = text[offset:offset+128].encode('utf8')
        digest.update(block)
        block = None
    return digest.hexdigest()


def seal_document(snapshot, name, document):
    # Full validation/count BEFORE destination creation; transport stays bounded.
    length, digest = canonical_size_sha256(document)
    need(length <= FILE_LIMIT, 'paired_evidence_file_bound')
    target = snapshot._destination(name)
    for block in canonical_chunks(document):
        write_all(target.fd, block)
    block = None
    snapshot._seal(name, target, digest, length, False)
    return dict(bytes=length, sha256=digest)


def seal_resolved_corpus(snapshot, notes):
    """Legacy wire form, selected bytes only, not a load witness.

    iterencode can retain a whole escaped token plus its UTF-8 encoding before
    FILE_LIMIT refusal. This is charged preparation materialization, NOT bounded
    scratch; physical sufficiency remains unknown, including failure frames.
    """
    document = dict(notes=notes)
    canonical_size_sha256(document)  # Complete strict shape/Unicode validation.
    def pieces():
        yield from json.JSONEncoder(ensure_ascii=False, indent=2).iterencode(document)
        yield '\n'
    length = 0
    digest = hashlib.sha256()
    for piece in pieces():
        raw = piece.encode('utf8')
        length += len(raw)
        digest.update(raw)
        raw = None
    need(length <= FILE_LIMIT, 'paired_evidence_file_bound')
    target = snapshot._destination('selected-resolved-corpus.json')
    for piece in pieces():
        raw = piece.encode('utf8')
        write_all(target.fd, raw)
        raw = None
    snapshot._seal('selected-resolved-corpus.json', target, digest.hexdigest(), length, False)
    return dict(bytes=length, sha256=digest.hexdigest())


class WeeklyPreparationOwner:
    """Retain before calling prepare; no deserialized receipt can construct it.

    The actual _checked/_publish path establishes selection provenance, not
    runtime admission. The caller's original SDK preparation allocation pays
    for validation graphs, retained schedule, source copies and failure frames.
    Its sufficiency remains unproved; no new allowance is granted here.
    """
    def __init__(self, preparation):
        need(type(preparation) is SDKReviewPreparation and
             type(preparation._allocation) is SDKPreparationAllocation, 'paired_original_allocation')
        self.allocation = preparation._allocation
        self.allocation.check()
        self._pending_preparations = [preparation]
        self.state = 'new'
        self.snapshot_owner = None
        self.rows = None
        self.claimed = set()
        self.runs = []
        self._initial = None
        self._environment = None
        self._runtime_selection = None

    def prepare(self, request, *, registry, now, companion, review):
        need(self.state == 'new', 'paired_preparation_consumed')
        try:
            self.state = 'preparing'
            self.allocation.check()  # Before validation copies/materialization.
            from trial_weekly_request import _checked, _publish
            request, bundle, evidence, retained = _checked(request, now=now,
                companion=companion, review=review)
            receipt = _publish(request, bundle, evidence, retained, registry, companion, review)
            self._capture(request, bundle, receipt)
            self.state = 'prepared'
        except BaseException as primary:
            self.state = 'unknown'
            self.allocation.poison(primary)
            raise

    def _capture(self, request, bundle, receipt):
        # Internal returning frame: no case/oracle graph escapes into the run.
        self.snapshot_owner = Snapshot.__new__(Snapshot)
        Snapshot.__init__(self.snapshot_owner, str(Path(request['output'])/'owned-selection'))
        for name in ('request.json','schedule.json','source.json','preparation.json'):
            from trial_paired_request import selected_bytes
            expected = (request if name=='request.json' else bundle['schedule'] if name=='schedule.json'
                        else bundle['source'] if name=='source.json' else receipt)
            # schedule.json retains pinned bytes, which need not use selected_bytes.
            digest = (request['schedule']['sha256'] if name=='schedule.json'
                      else hashlib.sha256(selected_bytes(expected)).hexdigest())
            self.snapshot_owner.copy(name, dict(path=str(Path(request['output'])/name),sha256=digest),FILE_LIMIT)
        self.identity = dict(request_id=request['request_id'],campaign_id=request['campaign_id'],
            occurrence_id=request['occurrence_id'],request_sha256=receipt['request_sha256'],
            # Whole schedule hash is distinct from its inner sha256 field.
            schedule_sha256=canonical_sha(bundle['schedule']),
            schedule_inner_sha256=bundle['schedule']['sha256'],wording=bundle['schedule']['wording'],
            frozen_corpus_sha256=bundle['frozen']['corpus_sha256'],
            resolved_corpus_sha256=bundle['source']['resolved_corpus_sha256'],
            native_model=bundle['execution']['native']['model'],
            native_effort=bundle['execution']['native']['effort'])
        self.resolved_corpus = seal_resolved_corpus(self.snapshot_owner,bundle['notes'])
        self.identity['selected_resolved_corpus_file_sha256'] = self.resolved_corpus['sha256']
        overlay = dict(notes=bundle['notes'] + bundle['distractor_notes'])
        self.overlay = seal_document(self.snapshot_owner,'selected-overlay.json',overlay)
        # This file is the selected overlay; no claim the service observed/loaded it.
        self.identity['selected_overlay_sha256'] = self.overlay['sha256']
        self.rows = []
        for index, row in enumerate(bundle['schedule']['runs']):
            cases = [case for case in bundle['cases'] if case['id']==row['case']]
            need(len(cases)==1,'paired_case_ambiguous')
            self.rows.append(dict(row, index=index,
                prompt_sha256=text_sha(cases[0]['wordings'][self.identity['wording']]),
                run_id=f"{row['case']}.{row['arm']}.s{row['seed']}",
                session_id=str(uuid.uuid5(uuid.UUID(request['occurrence_id']),
                    self.identity['schedule_sha256']+':'+str(index)))))
        # Retain only execution inputs, not case grading/oracle fields. These
        # graphs remain under the original preparation allocation.
        self._runtime_selection = dict(notes=bundle['notes'],distractor_notes=bundle['distractor_notes'],
            cases=[{k:c[k] for k in ('id','workspace','copy_to','cwd','workspace_commit') if k in c}
                   for c in bundle['cases']],workspace_paths=bundle['workspace_paths'],
            execution=bundle['execution'],source=bundle['source'])
        self._runtime_pin=seal_document(self.snapshot_owner,'runtime-selection.json',self._runtime_selection)
        self._join = seal_document(self.snapshot_owner,'join.json',dict(identity=self.identity,rows=self.rows))
        self._initial = self.snapshot_owner.bindings()

    def verify_runtime_selection(self):
        need(self.state=='prepared','paired_preparation_unavailable')
        self.allocation.check()
        self.snapshot_owner.verify_bindings(self._initial)
        need(canonical_size_sha256(self._runtime_selection)[1]==self._runtime_pin['sha256'],
             'weekly_runtime_selection_changed')

    def prepare_environment(self, root, pg_bin, *, readiness_timeout=1800):
        from trial_sdk_weekly_owners import WeeklyEnvironmentOwner
        need(self._environment is None,'weekly_environment_consumed')
        try:
            self.verify_runtime_selection()
            self._environment=WeeklyEnvironmentOwner.__new__(WeeklyEnvironmentOwner)
            WeeklyEnvironmentOwner.__init__(self._environment,self,root,pg_bin,readiness_timeout)
            self._environment.prepare()
        except BaseException as primary:
            self.state='unknown';self.allocation.poison(primary)
            raise

    def prepare_workspace(self,index,root):
        need(self._environment is not None,'weekly_environment_unavailable')
        self._environment.prepare_workspace(index,root)

    def workspace_binding(self,index):
        # Explicit charged scalar export; the workspace owner remains held.
        self.verify_runtime_selection()
        owner=self._environment.workspaces[index]
        need(owner.state=='prepared','weekly_workspace_unknown')
        return owner.prepared['workspace_sha256']

    def bind_loaded_run(self,*args,**kwargs):
        need(self._environment is not None,'weekly_environment_unavailable')
        return self._environment.bind_run(*args,**kwargs)

    def bind_run(self, run, preparation, binding, index, service_document, source_output,
                 callback_root, callback_quote, command_quote, *, stream_output):
        from trial_sdk_fixed_run import FixedRunOwner
        need(self.state == 'prepared', 'paired_preparation_unavailable')
        try:
            self.allocation.check()
            need(type(run) is FixedRunOwner and run._state=='new' and run._paired is None,
                 'paired_run_consumed')
            need(type(index) is int and 0 <= index < len(self.rows) and index not in self.claimed,
                 'paired_row_consumed')
            self.snapshot_owner.verify_bindings(self._initial)
            need(type(preparation) is SDKReviewPreparation and preparation._allocation is not None,
                 'paired_original_preparation')
            preparation._allocation.check()
            if preparation not in self._pending_preparations:
                self._pending_preparations.append(preparation)
            need(canonical_size_sha256(dict(identity=self.identity,rows=self.rows))[1]==self._join['sha256'],
                 'paired_selection_changed')
            original_allocation = preparation._allocation
            selected = self.rows[index]
            metadata = preparation.review_bindings()
            original = metadata['binding']
            need(preparation.launch.argv[2:6] == ('--model',self.identity['native_model'],
                 '--effort',self.identity['native_effort']), 'paired_launch_binding')
            need(all(original[k]==self.identity[k] for k in ('request_id','occurrence_id','request_sha256'))
                 and original['case_id']==selected['case'] and original['arm']==selected['arm']
                 and original['session_id']==selected['session_id'], 'paired_preparation_binding')
            need(text_sha(preparation._snapshot.read_text('prompt.txt'))==selected['prompt_sha256'],
                 'paired_prompt_binding')
            need(service_document['policy']['run']==selected['run_id'] and
                 service_document['policy']['task']=='task-eval/'+selected['run_id'], 'paired_service_binding')
            mcp_text = preparation._snapshot.read_text('mcp.json')
            mcp = parse_document(mcp_text,preparation._snapshot.files['mcp.json'][3])
            policy = service_document['policy']
            need(mcp['mcpServers']['cairn']['args']==mcp_arguments(
                {k:policy[k] for k in ('socket','token_file','repo','task','run')},profile='weekly-sdk'), 'paired_mcp_binding')
            # Capture immutable scalar selection BEFORE moving the SDK preparation.
            join = dict(self.identity, **{k:selected[k] for k in ('case','arm','seed','run_id','session_id','index')})
            self.claimed.add(index)  # An uncertain bind may never reselect this row.
            self.runs.append(run)  # Independent cleanup remains reachable on interruption.
            run._paired = self
            run._paired_join = join
            run.prepare(preparation,binding,service_document,source_output,callback_root,
                        callback_quote,command_quote,stream_output=stream_output)
            need(run._session._custody._allocation is original_allocation,
                 'paired_moved_allocation')
            run._evidence._paired_owner = self
            run._evidence._paired_join = join
            self._pending_preparations.remove(preparation)
        except BaseException as primary:
            self.state = 'unknown'
            self.allocation.poison(primary)
            if type(run) is FixedRunOwner:run._fail(primary)
            raise

    def verify_run(self, run):
        # Failure evidence may use frozen, still-held charges, never a new grant.
        self.allocation.resources._lookup(self.allocation.resources._memory,self.allocation.ticket)
        self.snapshot_owner.verify_bindings(self._initial)
        need(canonical_size_sha256(dict(identity=self.identity,rows=self.rows))[1]==self._join['sha256'],
             'paired_selection_changed')
        index=run._paired_join['index']
        need(type(index) is int and index in self.claimed and 0<=index<len(self.rows), 'paired_row_binding')
        selected=self.rows[index]
        expected=dict(self.identity, **{k:selected[k] for k in ('case','arm','seed','run_id','session_id','index')})
        need(run._paired_join==expected and run._session._custody._initial['binding']['session_id']==selected['session_id'],
             'paired_join_changed')

    def close(self):
        # Source backing may close only after all linked run cleanup completed.
        environment_runs=getattr(self._environment,'runs',())
        need(all(run._sources_closed for run in (*self.runs,*environment_runs)), 'paired_runs_live')
        faults = set()
        primary = None
        for preparation in self._pending_preparations:
            try:faults.update(preparation.close())
            except BaseException as error:
                faults.add('paired_preparation_close_unknown')
                if primary is None:primary=error
        need(all(run._sources_closed for run in self.runs), 'paired_runs_live')
        if self._environment is not None:
            if not self._environment.close():
                faults.add('weekly_environment_cleanup_unknown')
                return tuple(sorted(faults))
        if self.snapshot_owner is not None:
            faults.update(self.snapshot_owner.close())
        if primary is not None:raise primary
        return tuple(sorted(faults))


class EvidenceSnapshot(Snapshot):
    """Zero-byte stream case only; general source/input policy is unchanged."""
    def copy_empty_stream(self, name, path, expected_identity, digest):
        need(digest==hashlib.sha256(b'').hexdigest(), 'evidence_empty_digest')
        parent=self.tree.directory(str(Path(path).parent))
        source=self.tree.own(os.open(Path(path).name,READ_FLAGS,dir_fd=parent.fd))
        self.tree.edges.append((parent,Path(path).name,source))
        observed=os.fstat(source.fd)
        need(stat.S_ISREG(observed.st_mode) and observed.st_uid==os.geteuid()
             and observed.st_nlink==1 and not observed.st_mode & 0o7022
             and observed.st_size==0, 'evidence_empty_policy')
        need((observed.st_dev,observed.st_ino)==expected_identity, 'evidence_stream_replaced')
        initial=file_identity(observed)
        need(os.read(source.fd,1)==b'' and file_identity(os.fstat(source.fd))==initial,
             'evidence_empty_changed')
        self.sources[name]=(source,initial)
        target=self._destination(name)
        os.fchmod(target.fd,0o400);os.fsync(target.fd)
        observed=os.fstat(target.fd)
        need(observed.st_size==0 and stat.S_ISREG(observed.st_mode)
             and observed.st_uid==os.geteuid() and observed.st_nlink==1
             and stat.S_IMODE(observed.st_mode)==0o400, 'evidence_empty_policy')
        target.identity=identity(observed)
        readonly=self.tree.own(os.open(name,READ_FLAGS,dir_fd=self.directory.fd))
        need(file_identity(os.fstat(readonly.fd))==file_identity(observed), 'snapshot_identity_changed')
        target.close()
        self.tree.leases.remove(target)
        self.tree.edges=[edge for edge in self.tree.edges if edge[2] is not target]
        self.tree.edges.append((self.directory,name,readonly))
        self.files[name]=(readonly,file_identity(observed),digest,0)
        self._readback(name)
        os.fsync(self.directory.fd)


class EvidencePublication:
    """Consumed local export/failure controller, owned by the actual run.

    Metadata and stream identities are exported privately; raw stream files stay
    with their original owner. Work-product/grade export remains unavailable:
    no supported containment-release owner exists. Digests are not authentication.
    """
    def __init__(self, run):
        self.run = run
        self.state = 'new'
        self.files = None
        self.receipt = None

    def publish(self, output):
        need(self.state=='new','evidence_publication_consumed')
        try:
            self.state = 'publishing'
            run = self.run
            need(run._publication is self, 'evidence_publication_owner')
            paired = run._paired
            need(type(paired) is WeeklyPreparationOwner and any(r is run for r in paired.runs)
                 and run._paired_join is not None, 'evidence_prepared_owner_missing')
            paired.verify_run(run)
            # Frozen banks still retain failure-evidence custody; never unfreeze.
            paired.allocation.resources._lookup(paired.allocation.resources._memory,paired.allocation.ticket)
            run.resources._lookup(run.resources._memory,run._session_binding.allocation)
            need(run._sources_closed and run._state in ('closed','closed_unknown'),
                 'evidence_cleanup_pending')
            retention = run._evidence
            need(retention is not None and getattr(retention,'_paired_owner',None) is paired
                 and retention.binding is run._session_binding
                 and retention._paired_join is run._paired_join, 'evidence_stream_owner_missing')
            waits = []
            for owner in run._original_owners():
                state = owner.snapshot()
                need(not state['has_handles'] and state['release'] is not None
                     and state['release']['handles_detached'], 'evidence_cleanup_pending')
                need(not state['launch_attempted'] or state['wait'] is not None,
                     'evidence_wait_pending')
                release = dict(state['release'])
                release['cleanup'] = list(release['cleanup'])
                waits.append(dict(launch_attempted=state['launch_attempted'],wait=state['wait'],
                    release=release))
            # Use complete stream receipt; do not infer zero calls or grading from
            # missing/failed bytes. Store observations are separate from the
            # selected-input digest and from task acceptance.
            streams = retention.snapshot()
            streams['close_faults'] = list(streams['close_faults'])
            environment = getattr(run, '_weekly_environment', None)
            if environment is not None:
                from trial_sdk_weekly_owners import WeeklyEnvironmentOwner
                need(type(environment) is WeeklyEnvironmentOwner, 'weekly_corpus_owner')
            corpus_observation = (environment.corpus_observation(run)
                                  if environment is not None else None)
            from trial_sdk_selected_input import qualification
            self.receipt = dict(schema='cairn.sdk-private-evidence/1',join=run._paired_join,
                selected_overlay=paired.overlay, selected_resolved_corpus=paired.resolved_corpus,
                observed_corpus_sha256=None,
                store_corpus_observation=corpus_observation,
                streams=streams,native_owners=waits,
                selected_input=qualification(run),
                terminal_observed=run._terminal_metadata is not None,
                failure=run._failed,cleanup_faults=sorted(run._faults),
                containment='unavailable',work_product_export='unavailable',grade='ungraded')
            self.files = EvidenceSnapshot.__new__(EvidenceSnapshot)
            EvidenceSnapshot.__init__(self.files,output)
            self.receipt['stream_files'] = self._copy_streams(retention)
            self.receipt['failure_evidence'] = ('local_bytes_closed' if retention.state=='closed'
                                               else 'unknown_original_prefix_retained')
            need(canonical_size_sha256(self.receipt)[0] <= 65536, 'evidence_metadata_bound')
            seal_document(self.files,'evidence.json',self.receipt)
            self.files._verify_files()
            need(not self.files.close(),'evidence_close_unknown')
            self.state='closed'
        except BaseException as primary:
            self.state='unknown'
            self.run._fail(primary)
            try:
                if self.close():self.run._faults.add('evidence_publication_close_unknown')
            except BaseException:
                self.run._faults.add('evidence_publication_close_unknown')
            raise

    def _copy_streams(self, retention):
        if retention.state != 'closed':
            return dict(state='unknown', original_directory=getattr(retention,'path',None))
        result = {}
        for name, target in (('stdout','stream.jsonl'),('stderr','stderr.bin')):
            need(retention.written[name] <= 4*1024*1024, 'evidence_stream_bound')
            path=str(Path(retention.path)/(name+'.bin'))
            digest=retention.hashes[name].hexdigest()
            if retention.written[name]==0:
                self.files.copy_empty_stream(target,path,retention.identities[name],digest)
            else:
                self.files.copy(target,dict(path=path,sha256=digest),retention.written[name])
            identity = self.files._source_identity(target)
            need((identity['dev'],identity['ino'])==retention.identities[name],
                 'evidence_stream_replaced')
            result[name]=dict(path=target,bytes=retention.written[name],
                sha256=retention.hashes[name].hexdigest(),original_identity=identity)
        return result

    def close(self):
        if getattr(self,'files',None) is None:return ()
        return self.files.close()
