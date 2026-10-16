"""Owned endpoint regression guard, not hostile-code isolation."""
import sys

def guard(event,args):
    if event == 'socket.__new__':
        if args[1] != 1 or args[2] & 15 != 5:
            raise RuntimeError('unselected_socket')
    elif event in {'socket.bind','socket.connect','socket.getaddrinfo'}:
        raise RuntimeError('unselected_network')
    elif event == 'ctypes.dlopen' and args[0] is not None:
        raise RuntimeError('unselected_library')
    elif event == 'ctypes.dlsym' and args[1] != 'prctl':
        raise RuntimeError('unselected_symbol')
    elif event.startswith('subprocess.') or event in {'os.system','os.fork','os.forkpty','os.posix_spawn','os.exec','os.chdir','os.putenv','os.unsetenv'}:
        raise RuntimeError('unselected_process')
sys.addaudithook(guard)
