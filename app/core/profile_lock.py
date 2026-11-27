"""Small POSIX profile lock interoperable with the legacy Qt lock-file header."""

import os
import socket
import stat
import sys
from pathlib import Path


class ProfileLock:
    def __init__(self, path):
        self.path = Path(path)
        self._identity = None

    def tryLock(self, timeout=0):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        guard = self.path.with_name(self.path.name + '.guard')
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        try:
            with os.fdopen(os.open(guard, flags, 0o600), 'a') as mutex:
                fcntl.flock(mutex, fcntl.LOCK_EX)
                if self.path.exists() or self.path.is_symlink():
                    info = self.path.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                        return False
                    lines = self.path.read_text().splitlines()
                    if len(lines) < 3 or lines[2] != socket.gethostname():
                        return False
                    pid = int(lines[0])
                    if pid <= 0:
                        return False
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        if self.path.lstat().st_ino != info.st_ino:
                            return False
                        self.path.unlink()
                    else:
                        return False
                with os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
                    file.write(f'{os.getpid()}\n{Path(sys.executable).resolve().name}\n{socket.gethostname()}\n')
                    file.flush()
                    self._identity = os.fstat(file.fileno()).st_ino
                return True
        except (OSError, ValueError, UnicodeError):
            return False

    def unlock(self):
        if self._identity is not None:
            try:
                if self.path.lstat().st_ino == self._identity:
                    self.path.unlink()
            except OSError:
                pass
            self._identity = None
