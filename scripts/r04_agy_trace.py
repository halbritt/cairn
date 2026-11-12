"""Read selected kernel evidence from the disposable native Agy trace."""
import ast
import json
import re
from collections import Counter


def decoded_strings(line):
    return re.sub(r'"(?:[^"\\]|\\.)*"',
                  lambda match: json.dumps(ast.literal_eval(match[0]).encode('latin1').decode()), line)


def exec_lines(trace):
    return '\n'.join(decoded_strings(line) for line in trace.splitlines()
                     if 'execve(' in line or '<... execve resumed>' in line)


def calls(trace, names, *, pid=None, complete=True):
    pending = {}
    owners = None if pid is None else ({str(pid)} if isinstance(pid, (str, int)) else {str(item) for item in pid})
    for line in trace.splitlines():
        match = re.match(r'^(\d+)\s+', line)
        if not match or (owners is not None and match[1] not in owners):
            continue
        owner, event = match[1], line[match.end():]
        resumed = re.match(r'<\.\.\. (\w+) resumed>(.*)$', event)
        if resumed:
            name, suffix = resumed.groups()
            if name not in names:
                continue
            if owner not in pending or pending[owner][0] != name:
                raise AssertionError('Unpaired kernel syscall resume')
            event = pending.pop(owner)[1] + suffix
        else:
            start = re.match(r'(\w+)\(', event)
            if not start or start[1] not in names:
                continue
            name = start[1]
            if event.endswith(' <unfinished ...>'):
                if owner in pending:
                    raise AssertionError('Overlapping unfinished kernel calls')
                pending[owner] = name, event.removesuffix(' <unfinished ...>')
                continue
        yield owner, name, event
    if complete and pending:
        raise AssertionError('Unfinished kernel syscall evidence')


def io_bytes(trace, pid, fd, operation):
    pieces = []
    for _, _, event in calls(trace, {operation}, pid=pid):
        if not event.startswith(operation + '(' + str(fd) + ','):
            continue
        match = re.match(operation + r'\((\d+), ("(?:[^"\\]|\\.)*"), \d+\)\s+= (-?\d+)(?: .*)?$', event)
        if not match:
            if re.match(operation + r'\(' + str(fd) + r',', event) and not re.search(r'= -1\b', event):
                raise AssertionError('Unrecognized or truncated kernel I/O')
            continue
        if int(match[1]) != fd or int(match[3]) < 0:
            continue
        raw = ast.literal_eval(match[2]).encode('latin1')
        count = int(match[3])
        if count > len(raw) or (operation == 'read' and count != len(raw)):
            raise AssertionError('Kernel I/O byte count does not match payload')
        pieces.append(raw[:count])
    return b''.join(pieces)


def thread_pids(trace, leader):
    edges = []
    for owner, _, event in calls(trace, {'clone', 'clone3'}, complete=False):
        child = re.search(r'= (\d+)$', event)
        if child and 'CLONE_THREAD' in event:
            edges.append((owner, child[1]))
    members = {str(leader)}
    while True:
        expanded = members | {child for parent, child in edges if parent in members}
        if expanded == members:
            return sorted(members, key=int)
        members = expanded


def hook_writes(trace, native_threads):
    records = []
    for owner, _, event in calls(trace, {'write'}, pid=native_threads, complete=False):
        match = re.match(r'write\((\d+), ("(?:[^"\\]|\\.)*"), \d+\)\s+= (\d+)$', event)
        if not match:
            continue
        raw = ast.literal_eval(match[2]).encode('latin1')[:int(match[3])]
        if not raw.startswith(b'{'):
            continue
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue  # Other descriptors carry arbitrary files and wire chunks.
        if isinstance(payload, dict) and {'conversationId', 'workspacePaths', 'hookEventName'} <= payload.keys():
            if int(match[3]) > len(ast.literal_eval(match[2]).encode('latin1')):
                raise AssertionError('Truncated native hook write')
            records.append(dict(pid=owner, fd=int(match[1]), payload=payload))
    return records


def exits(trace, pid):
    return re.findall(r'^' + str(pid) + r'\s+\+\+\+ (exited with \d+|killed by \w+).*\+\+\+$',
                      trace, flags=re.MULTILINE)


def joined_pids(trace):
    issued = set(re.findall(r'^(\d+)\s+', trace, flags=re.MULTILINE))
    for _, _, event in calls(trace, {'clone', 'clone3', 'fork', 'vfork'}):
        child = re.search(r'= ([1-9]\d*)$', event)
        if child:
            issued.add(child[1])
    closed = set(re.findall(r'^(\d+)\s+\+\+\+ (?:exited with|killed by) ', trace, flags=re.MULTILINE))
    if issued - closed:
        raise AssertionError('Traced processes not joined: ' + ','.join(sorted(issued - closed)))
    return sorted(issued, key=int)


def opened_paths(trace, pid):
    paths = []
    for _, _, event in calls(trace, {'openat'}, pid=pid, complete=False):
        match = re.match(r'openat\([^,]+, ("(?:[^"\\]|\\.)*"), .*\)\s+= (\d+)$', event)
        if match:
            paths.append(ast.literal_eval(match[1]))
    return paths


def file_read(trace, pid, basename):
    events = list(calls(trace, {'openat', 'read', 'close'}, pid=pid))
    matches = []
    for index, (_, _, event) in enumerate(events):
        match = re.match(r'openat\([^,]+, ("(?:[^"\\]|\\.)*"), .*\)\s+= (-?\d+)(.*)$', event)
        if match and ast.literal_eval(match[1]).rsplit('/', 1)[-1] == basename:
            matches.append((index, int(match[2]), match[3].strip()))
    if len(matches) != 1:
        raise AssertionError('Expected exactly one selected file open')
    index, fd, error = matches[0]
    if fd < 0:
        return dict(open_error=error, raw=b'')
    selected = []
    closed = False
    for owner, name, event in events[index + 1:]:
        if name == 'close' and re.match(r'close\(' + str(fd) + r'\)\s+= 0$', event):
            closed = True
            break
        selected.append(owner + ' ' + event)
    if not closed:
        raise AssertionError('Selected file descriptor was not closed')
    reads = [event for _, _, event in calls('\n'.join(selected), {'read'}, pid=pid)
             if event.startswith('read(' + str(fd) + ',') and not re.search(r'= -1\b', event)]
    if not reads or not re.search(r', "", \d+\)\s+= 0$', reads[-1]):
        raise AssertionError('Selected file read did not reach EOF')
    return dict(open_error=None, raw=io_bytes('\n'.join(selected), pid, fd, 'read'))


def walked_path(trace, pid, basename):
    events = list(calls(trace, {'openat', 'close'}, pid=pid))
    opens = {}
    for index, (_, name, event) in enumerate(events):
        if name == 'openat':
            match = re.match(r'openat\(([^,]+), ("(?:[^"\\]|\\.)*"), (.*)\)\s+= (-?\d+)(.*)$', event)
            if match:
                opens[index] = dict(parent=match[1], path=ast.literal_eval(match[2]),
                                    flags=match[3], fd=int(match[4]), error=match[5].strip())
    finals = [index for index, record in opens.items() if record['path'] == basename]
    if len(finals) != 1:
        raise AssertionError('Expected one walked selected file open')
    index = finals[0]
    record = opens[index]
    if not {'O_RDONLY', 'O_NOFOLLOW', 'O_NONBLOCK', 'O_CLOEXEC'} <= set(record['flags'].split('|')):
        raise AssertionError('Selected file open flags changed')
    components = [record['path']]
    while record['parent'] != 'AT_FDCWD':
        parent_fd = int(record['parent'])
        ancestors = [prior for prior, candidate in opens.items() if prior < index and candidate['fd'] == parent_fd]
        if not ancestors:
            raise AssertionError('Selected file parent descriptor is unbound')
        parent_index = max(ancestors)
        if any(re.match(r'close\(' + str(parent_fd) + r'\)\s+= 0$', events[n][2])
               for n in range(parent_index + 1, index)):
            raise AssertionError('Selected file parent descriptor was already closed')
        index, record = parent_index, opens[parent_index]
        if 'O_DIRECTORY' not in record['flags'] or (record['path'] != '/' and 'O_NOFOLLOW' not in record['flags']):
            raise AssertionError('Selected file ancestor flags changed')
        components.append(record['path'])
    if components[-1] != '/':
        raise AssertionError('Selected file walk did not start at root')
    return '/' + '/'.join(reversed(components[:-1]))


def dns_question(raw):
    if len(raw) < 12 or raw[2] & 0xF8 or int.from_bytes(raw[4:6], 'big') != 1:
        raise AssertionError('Invalid DNS query header')
    offset, labels = 12, []
    while offset < len(raw) and raw[offset]:
        size = raw[offset]
        if size > 63 or offset + size + 1 >= len(raw):
            raise AssertionError('Invalid DNS query label')
        labels.append(raw[offset + 1:offset + size + 1].decode('ascii'))
        offset += size + 1
    offset += 1
    if offset + 4 > len(raw) or int.from_bytes(raw[offset + 2:offset + 4], 'big') != 1:
        raise AssertionError('Incomplete DNS question')
    return dict(name='.'.join(labels), qtype=int.from_bytes(raw[offset:offset + 2], 'big'))


def loopback_connections(trace, *, provider_port):
    parent = {}
    for owner, _, event in calls(trace, {'clone', 'clone3'}):
        child = re.search(r'= (\d+)$', event)
        if child and 'CLONE_THREAD' in event:
            parent[child[1]] = owner

    def group(owner):
        while owner in parent:
            owner = parent[owner]
        return owner

    network, missing_nss, queries = [], [], []
    dns_fds = set()
    for owner, operation, event in calls(trace, {'connect', 'close', 'write', 'read'}):
        fd_match = re.match(r'\w+\((\d+),?', event)
        if not fd_match:
            continue
        descriptor = group(owner), int(fd_match[1])
        if operation == 'close':
            if re.search(r'= 0$', event):
                dns_fds.discard(descriptor)
            continue
        if operation == 'connect':
            event = decoded_strings(event)
            if 'AF_UNIX' in event:
                if not re.fullmatch(r'connect\(\d+, \{sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"\}, 110\)\s+= -1 ENOENT \(No such file or directory\)', event):
                    raise AssertionError('Unexpected local socket connect: ' + event)
                missing_nss.append(dict(pid=owner, call=event))
                continue
            port = re.search(r'htons\((\d+)\)', event)
            valid = ('AF_INET6' in event and 'inet_pton(AF_INET6, "::1"' in event or
                     'AF_INET,' in event and 'inet_addr("127.0.0.1")' in event)
            if not valid or port is None or int(port[1]) not in (53, provider_port):
                raise AssertionError('Non-loopback or unselected-port connect attempt: ' + event)
            network.append(dict(pid=owner, port=int(port[1]), call=event))
            if int(port[1]) == 53:
                dns_fds.add(descriptor)
            else:
                dns_fds.discard(descriptor)
        elif descriptor in dns_fds:
            count = re.search(r'\)\s+= (-?\d+)(?: .*)?$', event)
            if count and int(count[1]) > 0:
                if operation == 'read':
                    raise AssertionError('Unexpected DNS response in empty namespace')
                raw = io_bytes(owner + ' ' + event, owner, descriptor[1], 'write')
                question = dns_question(raw)
                queries.append(dict(pid=owner, fd=descriptor[1], **question))
    counts = Counter((query['name'], query['qtype']) for query in queries)
    return dict(scope='connect(2) and writes on connected DNS descriptors only; wrapper lo-only namespace supplies egress isolation',
                network_connects=network, unavailable_local_nss=missing_nss, unanswered_dns_queries=queries,
                dns_counts=[dict(name=name, qtype=qtype, count=count) for (name, qtype), count in sorted(counts.items())])
