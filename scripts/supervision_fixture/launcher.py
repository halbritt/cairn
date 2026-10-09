"""Join only a controller-created cgroup before exec; never migrate the controller."""
import os
import resource
import sys

fd = int(sys.argv[1])
os.write(fd, str(os.getpid()).encode())
os.close(fd)
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_FSIZE, (64*1024*1024, 64*1024*1024))
os.execvp(sys.argv[2], sys.argv[2:])
