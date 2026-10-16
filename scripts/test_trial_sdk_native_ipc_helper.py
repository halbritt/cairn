"""Only an owned holder or wrong-sender for ordinary host qualification."""
import os
import socket
import sys

def main():
    if sys.argv[1] == 'hold':
        if os.read(0, 1) != b'x':
            return 2
    elif sys.argv[1] == 'send':
        with socket.socket(fileno=int(sys.argv[2])) as peer:
            if peer.send(b'b' * 32) != 32:
                return 3
    else:
        return 4
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
