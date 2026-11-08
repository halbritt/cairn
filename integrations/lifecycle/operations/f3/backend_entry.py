"""Inactive operational entry. No command-line/environment enablement exists."""
import json


def serve_once(executor,stdin,stdout,stderr):
    """Future source-owned composition only; default main never calls this function."""
    if executor is None:
        stderr.write(b'{"status":"REFUSED_UNBOUND"}\n');return 2
    # At most4097 bytes retained. A peer delaying EOF can outlive Root's wait;
    # K2 requires UNKNOWN, not an inferred cancellation or a replay.
    data=stdin.read(4097)
    reply=executor.execute_wire(data)
    stdout.write(reply.stdout);stdout.flush();stderr.write(reply.stderr);stderr.flush()
    return reply.returncode


def main():
    print(json.dumps({'status':'REFUSED_UNBOUND','backend':'fixed-SSH/systemctl candidate',
                      'live_grant':False,'effects_launched':0},sort_keys=True))
    return 2


if __name__=='__main__':raise SystemExit(main())
