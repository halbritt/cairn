"""Pure one-use SDK tuple mapping. Hash scratch is externally reserved."""
import dataclasses
import hashlib
import json
import re
from trial_sdk_launch_profile import ENV, FLAGS

class MappingError(ValueError):
    pass

@dataclasses.dataclass(frozen=True)
class TupleDigest:
    bytes: int
    sha256: str

@dataclasses.dataclass(frozen=True)
class LaunchPlan:
    argv: tuple
    cwd: str
    env: tuple
    requires_initialize_instruction: bool
    raw: TupleDigest
    with_print: TupleDigest
    native: TupleDigest

class LaunchMapper:
    """A trusted serialized owner consumes at most one tuple, even on refusal.

    reserve(n) returns True with n bytes or False with none, never a partial
    grant or a post-commit exception. release(n) returns the exact reservation
    synchronously. Either uncertainty refuses the plan; there is no retry.
    Unicode/reference working allocations remain under the aggregate process
    memory limit. This object performs no process/IPC/config or authentication.
    """
    def __init__(self, selected_session, reserve, release):
        if (type(selected_session) is not str or re.fullmatch(
                '[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', selected_session) is None):
            raise MappingError('launch_session')
        if not callable(reserve) or not callable(release):
            raise MappingError('launch_credit_boundary')
        self._session = selected_session
        self._reserve, self._release = reserve, release
        self._consumed = False
        self.cleanup_unknown = False

    def consume(self, sdk_tuple):
        if self._consumed:
            raise MappingError('launch_mapping_consumed')
        self._consumed = True
        args, env, in_argv = self._validate(sdk_tuple)
        effective = ('-p', *args)
        raw = self._digest('/unavailable/claude', args, '/work', env)
        printed = self._digest('/unavailable/claude', effective, '/work', env)
        native = self._digest('/native/claude', effective, '/work', env)
        return LaunchPlan(('/native/claude', *effective), '/work', env,
                          not in_argv, raw, printed, native)

    def _validate(self, request):
        if (type(request) is not dict or set(request) != {'command','args','cwd','env'}
                or request['command'] != '/unavailable/claude' or type(request['command']) is not str
                or request['cwd'] != '/work' or type(request['cwd']) is not str
                or type(request['args']) is not list or not 0 < len(request['args']) <= 63
                or type(request['env']) is not dict or len(request['env']) != len(ENV)):
            raise MappingError('launch_tuple')
        args, given_env = request['args'], request['env']
        if any(type(k) is not str or type(v) is not str for k,v in given_env.items()):
            raise MappingError('launch_environment')
        if any(given_env.get(k) != v for k,v in ENV):
            raise MappingError('launch_environment')
        if any(type(t) is not str or len(t) > 4096 for t in args):
            raise MappingError('launch_arguments')
        expected = {key: self._session if key == '--session-id' else value
                    for key,value in FLAGS[1:]}
        seen = set()
        at = 0
        while at < len(args):
            token = args[at]
            key, separator, value = token.partition('=')
            if key not in expected or key in seen:
                raise MappingError('launch_arguments')
            seen.add(key)
            want = expected[key]
            if separator:
                if want is None or value != want:
                    raise MappingError('launch_arguments')
            elif want is not None:
                at += 1
                if at == len(args) or args[at] != want:
                    raise MappingError('launch_arguments')
            at += 1
        if any(key not in seen for key,_ in FLAGS[1:20]):
            raise MappingError('launch_arguments')
        # Accepted values are fixed ASCII profile text or the selected ASCII UUID.
        # No untrusted text remains to encode, and no full raw tuple is allocated.
        return tuple(args), tuple(ENV), '--append-system-prompt' in seen

    def _digest(self, command, args, cwd, env):
        digest, count = hashlib.sha256(), 0
        def put(token):
            nonlocal count
            count += len(token)
            if count > 32768:
                raise MappingError('launch_tuple_bound')
            digest.update(token)
        def quoted(text):
            # The admitted fixed profile contains no non-ASCII or escapable text.
            size = len(text) + 2
            if size > 512:
                raise MappingError('launch_hash_piece_bound')
            try:
                granted = self._reserve(size)
            except Exception:
                raise MappingError('launch_scratch_unknown') from None
            if granted is not True:
                if granted is not False:
                    raise MappingError('launch_scratch_unknown')
                raise MappingError('launch_scratch_credit')
            raw = encoded = None
            primary = None
            try:
                encoded = json.dumps(text, ensure_ascii=False)
                raw = encoded.encode('ascii')
                if len(raw) != size:
                    raise MappingError('launch_hash_piece_bound')
                put(raw)
            except BaseException as exc:
                primary = exc
                raise
            finally:
                raw = encoded = None
                try:
                    self._release(size)
                except Exception:
                    self.cleanup_unknown = True
                    if primary is None:
                        raise MappingError('launch_scratch_release') from None
        put(b'[');quoted(command);put(b',[')
        for i,arg in enumerate(args):
            if i:put(b',')
            quoted(arg)
        put(b'],');quoted(cwd);put(b',[')
        for i,(key,value) in enumerate(env):
            if i:put(b',')
            put(b'[');quoted(key);put(b',');quoted(value);put(b']')
        put(b']]')
        return TupleDigest(count,digest.hexdigest())

if __name__ == '__main__':
    raise SystemExit('SOURCE_ONLY')
