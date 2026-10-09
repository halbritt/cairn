"""Outer owner for fixed disposable service controllers, never a provider launcher."""
import dataclasses
import errno
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
import uuid

from trial_external_supervision import AttemptResult, FIXTURE, FixtureRequest, encoded


def manager_properties(unit):
    # Only this exclusively requested UUID unit; query is independently bounded.
    query = subprocess.run(['systemctl','--user','show',unit,'--property=MainPID,InvocationID,ControlGroup,ExecMainCode,ExecMainStatus'],
        capture_output=True, timeout=.3, env={'PATH':'/usr/bin:/bin','XDG_RUNTIME_DIR':f'/run/user/{os.getuid()}'})
    if query.returncode or len(query.stdout) > 8192:
        raise ValueError('service_status_unknown')
    return dict(line.split('=',1) for line in query.stdout.decode('ascii').splitlines())


def service_fixture(request, retained_root, workspace_root, *, loss_stage=None):
    """Record manager-observed controller termination and anchored whole-unit release.

    `systemd-run` is a client proxy; its wait never masquerades as controller
    wait. Actual service termination uses pinned invocation metadata. A lost
    controller's inner actor/capture/sink facts stay unknown even after release.
    """
    result = AttemptResult(status='unknown',failures=['controller_observation_lost'])
    service_fact = dict(waitid_code=None,waitid_status=None,returncode=None,release=None,
                        basis='systemd_manager_exec_status', invocation_id=None)
    result.containment = dict(released=None, resources=None, service=service_fact)
    if type(request) is not FixtureRequest or loss_stage not in {None,'intent','capture','parser','sink'}:
        result.status='refused_prelaunch'
        result.failures=['fixture_service_request_required']
        return result
    try:
        request.validate()
    except ValueError:
        result.status='refused_prelaunch'
        result.failures=['fixture_service_request_required']
        return result
    result.bindings=dict(run_id=request.run_id,occurrence_id=request.occurrence_id,
        request_sha256=request.request_sha256,task_sha256=request.task_sha256,
        oracle_sha256=request.oracle_sha256,config_sha256=request.config_sha256,
        source_manifest_sha256=None,workspace_sha256=None,fixture_only=True)
    started=time.monotonic()
    deadline=started+request.wall_seconds
    unit='cairn-fixture-owned-'+str(uuid.uuid4())+'.service'
    command=['systemd-run','--user','--wait','--pipe','--unit='+unit,
        '--property=Delegate=yes','--property=DelegateSubgroup=controller',
        '--property=KillMode=control-group','--property=RemainAfterExit=yes','--property=RuntimeMaxSec='+str(request.wall_seconds),
        '--property=MemoryMax=1G','--property=MemorySwapMax=0','--property=TasksMax=64',
        '/usr/bin/python3',str(FIXTURE/'service_main.py'),unit,str(retained_root),str(workspace_root),
        encoded(dataclasses.asdict(request)).decode(),loss_stage or 'none',str(deadline)]
    process=None
    group_fd=events_fd=kill_fd=pidfd=None
    body=None
    buffers={'stdout':bytearray(),'stderr':bytearray()}
    ready=None
    phase='service_start'
    try:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            close_fds=True,env={'PATH':'/usr/bin:/bin','XDG_RUNTIME_DIR':f'/run/user/{os.getuid()}'})
        for stream in [process.stdin,process.stdout,process.stderr]:os.set_blocking(stream.fileno(),False)
        with selectors.DefaultSelector() as poller:
            poller.register(process.stdout,selectors.EVENT_READ,'stdout')
            poller.register(process.stderr,selectors.EVENT_READ,'stderr')
            while time.monotonic()<deadline:
                for key,_ in poller.select(.01):
                    chunk=os.read(key.fd,65536)
                    if not chunk:
                        poller.unregister(key.fileobj)
                        continue
                    buffers[key.data].extend(chunk)
                    if len(buffers[key.data])>65536:raise ValueError('service_control_bounds')
                lines=[json.loads(line) for line in buffers['stdout'].split(b'\n')[:-1]]
                if ready is None and lines:
                    ready=lines[0]
                    phase='bootstrap_status'
                    properties=manager_properties(unit)
                    phase='bootstrap_identity'
                    pid=ready['pid']
                    if ready['kind']!='SERVICE_READY' or ready['unit']!=unit or str(pid)!=properties['MainPID'] or ready['invocation_id']!=properties['InvocationID']:
                        raise ValueError('service_identity_unknown')
                    relative=properties['ControlGroup']
                    if not relative.endswith('/'+unit) or ready['membership']!='0::'+relative+'/controller':
                        raise ValueError('service_membership_unknown')
                    before=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
                    pidfd=os.pidfd_open(pid)
                    group_fd=os.open('/sys/fs/cgroup'+relative,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                    events_fd=os.open('cgroup.events',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=group_fd)
                    kill_fd=os.open('cgroup.kill',os.O_WRONLY|os.O_NOFOLLOW,dir_fd=group_fd)
                    after=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
                    if before!=after or int(after)!=ready['start_ticks'] or Path(f'/proc/{pid}/cgroup').read_text().strip()!=ready['membership']:
                        raise ValueError('service_process_identity_unknown')
                    service_fact['invocation_id']=ready['invocation_id']
                    os.write(process.stdin.fileno(),b'G')
                    process.stdin.close()
                for line in lines[1:]:
                    if line.get('kind')=='RESULT':body=line['result']
                if ready is not None:
                    phase='actual_service_status'
                    properties=manager_properties(unit)
                    if properties.get('InvocationID')==ready['invocation_id'] and properties.get('ExecMainCode') in {'1','2','3'}:
                        code,status=int(properties['ExecMainCode']),int(properties['ExecMainStatus'])
                        service_fact.update(waitid_code=code,waitid_status=status,returncode=status if code==1 else -status)
                if not poller.get_map() or (body is not None and service_fact['returncode'] is not None):break
        if group_fd is not None:
            phase='service_release'
            # A complete returned body is still followed by independent release;
            # PREPARED and the client proxy's own return are irrelevant here.
            if service_fact['returncode'] is None or time.monotonic()>=deadline:
                try:os.write(kill_fd,b'1')
                except OSError as exc:
                    if exc.errno!=errno.ENODEV:raise
            release_deadline=max(deadline,time.monotonic()+.15)
            while time.monotonic()<release_deadline:
                try:
                    values=dict(line.split() for line in os.pread(events_fd,8192,0).decode('ascii').splitlines())
                    if values.get('populated')=='0':
                        service_fact['release']=True
                        service_fact['release_basis']='held_cgroup_populated_zero'
                        break
                except OSError as exc:
                    # The same held kernel cgroup has been removed, not an
                    # arbitrary path lookup miss or a reconstructed new unit.
                    if exc.errno==errno.ENODEV and os.readlink(f'/proc/self/fd/{group_fd}')=='/sys/fs/cgroup'+relative+' (deleted)':
                        service_fact['release']=True
                        service_fact['release_basis']='held_cgroup_kernel_removed'
                        break
                    if exc.errno!=errno.ENODEV:
                        raise
                    # Kernfs files can become inactive before directory
                    # removal is visible. Keep unknown and observe again.
                time.sleep(.005)
            properties=manager_properties(unit)
            if ready is not None and properties.get('InvocationID')==ready['invocation_id'] and properties.get('ExecMainCode') in {'1','2','3'}:
                code,status=int(properties['ExecMainCode']),int(properties['ExecMainStatus'])
                service_fact.update(waitid_code=code,waitid_status=status,returncode=status if code==1 else -status)
        if body is not None:
            result=AttemptResult(**body)
            result.containment['service']=service_fact
            if service_fact['release'] is not True or service_fact['returncode']!=0:
                result.status='unknown'
                result.fail('controller_service_terminal_unknown')
        else:
            result.containment['released']=service_fact['release']
    except (OSError,ValueError,KeyError,TypeError,RecursionError,subprocess.TimeoutExpired):
        result.fail('service_owner_unknown')
        result.fail(phase+'_unknown')
    finally:
        # On observer error, stop only the pinned owned domain or identity. No
        # systemctl kill by reconstructed name and no signals to unbound PIDs.
        if body is None and kill_fd is not None:
            try:os.write(kill_fd,b'1')
            except OSError:pass
        elif ready is not None and body is None and pidfd is not None:
            try:signal.pidfd_send_signal(pidfd,signal.SIGKILL)
            except OSError:pass
        for fd in [events_fd,kill_fd,group_fd,pidfd]:
            if fd is not None:os.close(fd)
        if ready is not None and body is not None:
            try:
                props=manager_properties(unit)
                if props.get('InvocationID')==ready['invocation_id']:
                    subprocess.run(['systemctl','--user','stop',unit],stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,timeout=.3,env={'PATH':'/usr/bin:/bin','XDG_RUNTIME_DIR':f'/run/user/{os.getuid()}'})
            except (OSError,ValueError,subprocess.TimeoutExpired):pass
        if process is not None:
            # A refused bootstrap has no actor grant; closing its own gate pipe
            # lets the owned service finish before fixture metadata cleanup.
            process.stdin.close()
            try:process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                process.kill()  # This directly owned proxy, not the service PID.
                process.wait(timeout=.5)
            for stream in [process.stdin,process.stdout,process.stderr]:stream.close()
        if ready is not None:
            # Reset only this still-pinned failed invocation. Unit effects are
            # owned fixture cleanup, never an existing service repair.
            try:
                props=manager_properties(unit)
                if props.get('InvocationID')==ready['invocation_id']:
                    subprocess.run(['systemctl','--user','reset-failed',unit],stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,timeout=.3,env={'PATH':'/usr/bin:/bin','XDG_RUNTIME_DIR':f'/run/user/{os.getuid()}'})
            except (OSError,ValueError,subprocess.TimeoutExpired):pass
    result.elapsed_seconds=time.monotonic()-started
    if body is None:
        if ready is None:result.fail('service_bootstrap_unobserved')
        if service_fact['returncode'] is None:result.fail('service_actual_wait_unknown')
        if service_fact['release'] is not True:result.fail('service_release_unknown')
    return result
