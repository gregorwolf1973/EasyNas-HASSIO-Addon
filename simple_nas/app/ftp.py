#!/usr/bin/env python3
"""FTP: the Samba shares for devices that only speak FTP (IP cameras, scanners, printers).

- Served by pyftpdlib on its own port, off by default. Plain FTP without TLS - meant for the home
  network only, never forward the port to the internet.
- The root / lists the shares the logged-in user may open and that have FTP ticked in the share
  dialog (services.py); /<share>/... is the share itself.
  Same rules as in Samba: valid users and groups, read-only shares stay read-only.
- Login with the Samba users and passwords (NT hash, see webdav.py). Public shares need a login too,
  anonymous FTP does not exist here. Failed logins count in the same limiter as WebDAV.
- Deleting moves into the share's .recycle folder when the recycle bin is on, like Samba does.
- Symlinks pointing outside the share are refused, as in WebDAV.
"""

import logging
import os
import random
import shutil
import threading
import time

from pyftpdlib.authorizers import AuthenticationFailed
from pyftpdlib.filesystems import AbstractedFS
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.log import config_logging
from pyftpdlib.servers import ThreadedFTPServer

from ratelimit import AUTHFAIL_IP, AUTHFAIL_USER, LIMITER, LOCK_BASE, LOCK_CAP
from services import offers
from webdav import RECYCLE_EXCLUDE, _bin_target, mounted_points, share_allows, share_available

ROOT_DIR = "/run/simple-nas-ftp"         # empty stand-in for "/", the list of shares
READ_PERMS = "elr"                      # cd, list, download
WRITE_PERMS = "adfmwMT"                 # append, delete, mkdir/rmdir, rename, upload, chmod, mtime
DEFAULT_PASSIVE = "30000-30010"


def parse_passive_ports(text):
    """'30000-30010' -> range; empty or broken -> the default range."""
    try:
        low, high = (int(p) for p in str(text or DEFAULT_PASSIVE).split("-", 1))
        if 1024 <= low <= high <= 65535:
            return list(range(low, high + 1))
    except ValueError:
        pass
    low, high = (int(p) for p in DEFAULT_PASSIVE.split("-"))
    return list(range(low, high + 1))


class ShareSet:
    """The active shares, reread when shares.json changes or refresh() is called (mount, unmount)."""

    def __init__(self, shares_file, load_shares, load_groups):
        self._shares_file = shares_file
        self._load_shares = load_shares
        self.load_groups = load_groups
        self._lock = threading.Lock()
        self._key = None
        self._active = {}
        self._generation = 0

    def refresh(self):
        with self._lock:
            self._generation += 1

    def active(self):
        try:
            mtime = os.stat(self._shares_file).st_mtime_ns
        except OSError:
            mtime = 0
        key = (mtime, self._generation)
        with self._lock:
            if key != self._key:
                mounts = mounted_points()
                self._active = {s["name"]: s for s in self._load_shares()
                                if s.get("name") and "/" not in s["name"] and offers(s, "ftp")
                                and share_available(s.get("path", ""), mounts)}
                self._key = key
            return dict(self._active)

    def names(self):
        return sorted(self.active())

    def for_user(self, user):
        groups = self.load_groups()
        return {n: s for n, s in self.active().items() if share_allows(s, user, groups)}


class SambaAuthorizer:
    """pyftpdlib authorizer: Samba passwords, share rules from shares.json, lockouts as in WebDAV."""

    def __init__(self, shares, passwords, limiter=LIMITER, log=lambda *a, **k: None, recycle_on=lambda: False,
                 recycle_dir=".recycle"):
        self.shares = shares
        self.passwords = passwords
        self.limiter = limiter
        self.log = log
        self.recycle_on = recycle_on
        self.recycle_dir = recycle_dir

    def validate_authentication(self, username, password, handler):
        ip = getattr(handler, "remote_ip", "?")
        ip_key, user_key = f"authfail:ip:{ip}", f"authfail:user:{(username or '').lower()}"
        if self.limiter.locked(ip_key, *AUTHFAIL_IP) or (username and self.limiter.locked(user_key, *AUTHFAIL_USER)):
            self.log("rate_limited", ip=ip, user=username, detail="ftp locked")
            raise AuthenticationFailed("Too many failed logins, try again later.")
        if username and username != "anonymous" and self.passwords.check(username, password or ""):
            return
        time.sleep(random.uniform(0.15, 0.35))
        for key, (limit, window) in ((ip_key, AUTHFAIL_IP), (user_key, AUTHFAIL_USER)):
            if key == user_key and not username:
                continue
            self.limiter.record(key)
            if self.limiter.count(key, window) >= limit:
                dur = self.limiter.lock(key, LOCK_BASE, LOCK_CAP)
                self.log("rate_limited", ip=ip, detail=f"ftp lock {key.split(':', 1)[1]} {dur // 60} min")
        self.log("auth_fail", ip=ip, user=username, detail="ftp")
        raise AuthenticationFailed("Authentication failed.")

    def get_home_dir(self, username):
        return ROOT_DIR

    def has_user(self, username):
        return True

    def get_perms(self, username):
        return READ_PERMS + WRITE_PERMS     # the upper bound; has_perm decides per path

    def has_perm(self, username, perm, path=None):
        if path is None:
            return perm in READ_PERMS + WRITE_PERMS
        found = shares_of(path, self.shares.for_user(username))
        if not found:                        # the root listing: look only
            return perm in READ_PERMS
        if perm in READ_PERMS:
            return True
        path = os.path.normpath(path)
        if perm in "df" and any(path == os.path.normpath(s["path"]) for s in found):
            return False                     # the share folder itself stays
        # nested shares: writable if one of them is - over Samba it would be the same
        return any(s.get("writable", True) for s in found)

    def get_msg_login(self, username):
        return "Simple NAS - welcome."

    def get_msg_quit(self, username):
        return "Goodbye."

    def impersonate_user(self, username, password):
        pass                                 # runs as root like Samba (force user = root)

    def terminate_impersonation(self, username):
        pass


def _inside(path, root):
    root = os.path.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def shares_of(path, shares):
    """All shares a real path lies in (shares may be nested, e.g. /media and /media/camera)."""
    path = os.path.normpath(path)
    return [s for s in shares.values() if _inside(path, s["path"])]


def share_of(path, shares, prefer=None):
    """The share to show a real path in: `prefer` (the share the client is browsing), else the deepest."""
    found = shares_of(path, shares)
    for share in found:
        if share["name"] == prefer:
            return share
    return max(found, key=lambda s: len(os.path.normpath(s["path"])), default=None)


class SharesFS(AbstractedFS):
    """Virtual root with one folder per share; below that the real share folders."""

    def _shares(self):
        return self.cmd_channel.authorizer.shares.for_user(self.cmd_channel.username)

    def _share_name(self):
        return self.cwd.strip("/").split("/", 1)[0] or None

    def ftp2fs(self, ftppath):
        parts = self.ftpnorm(ftppath).strip("/").split("/", 1)
        if not parts[0]:
            return ROOT_DIR
        share = self._shares().get(parts[0])
        if share is None:
            return os.path.join(ROOT_DIR, parts[0])     # does not exist: "No such file"
        return os.path.normpath(os.path.join(share["path"], parts[1] if len(parts) > 1 else ""))

    def fs2ftp(self, fspath):
        path = os.path.normpath(fspath)
        if path == ROOT_DIR:
            return "/"
        if os.path.dirname(path) == ROOT_DIR:
            return "/" + os.path.basename(path)
        share = share_of(path, self._shares(), prefer=self._share_name())
        if share is None:
            return "/"
        rel = os.path.relpath(path, os.path.normpath(share["path"]))
        return "/" + share["name"] + ("" if rel == "." else "/" + rel)

    def validpath(self, path):
        path = os.path.normpath(path)
        if path == ROOT_DIR or os.path.dirname(path) == ROOT_DIR:
            return True
        real = os.path.realpath(path)
        for share in self._shares().values():
            root = os.path.realpath(share["path"])
            if real == root or real.startswith(root.rstrip("/") + "/"):
                return True
        return False

    def _real(self, path):
        """ROOT_DIR/<share> (what a listing of / joins together) -> the share folder."""
        path = os.path.normpath(path)
        if os.path.dirname(path) == ROOT_DIR:
            share = self._shares().get(os.path.basename(path))
            if share is not None:
                return share["path"]
        return path

    def chdir(self, path):
        # no os.chdir: the process is shared with the web interface and other FTP sessions
        real = self._real(path)
        if not os.path.isdir(real):
            raise OSError(2, "No such directory")
        if not os.access(real, os.X_OK):
            raise OSError(13, "Permission denied")
        self.cwd = self.fs2ftp(path)

    def listdir(self, path):
        if os.path.normpath(path) == ROOT_DIR:
            return sorted(self._shares())
        return os.listdir(self._real(path))

    listdirinfo = listdir

    def stat(self, path):
        return os.stat(self._real(path))

    def lstat(self, path):
        return os.lstat(self._real(path))

    def isdir(self, path):
        return os.path.isdir(self._real(path))

    def isfile(self, path):
        return os.path.isfile(self._real(path))

    def islink(self, path):
        return os.path.islink(self._real(path))

    def lexists(self, path):
        return os.path.lexists(self._real(path))

    def getsize(self, path):
        return os.path.getsize(self._real(path))

    def getmtime(self, path):
        return os.path.getmtime(self._real(path))

    def remove(self, path):
        if not self._recycle(path):
            os.remove(path)

    def rmdir(self, path):
        os.rmdir(path)                       # only empty folders; their files went to the bin already

    def _recycle(self, path):
        """Move a deleted file into <share>/.recycle when the bin is on; False = delete for good."""
        auth = self.cmd_channel.authorizer
        if not auth.recycle_on():
            return False
        share = share_of(path, self._shares(), prefer=self._share_name())
        if share is None or share.get("timemachine"):
            return False
        root = os.path.normpath(share["path"])
        rel = os.path.relpath(path, root)
        name = os.path.basename(path)
        if rel.split(os.sep, 1)[0] == auth.recycle_dir or name.lower().endswith(RECYCLE_EXCLUDE) \
                or name.startswith("~$"):
            return False
        bin_root = os.path.join(root, auth.recycle_dir)
        if os.path.islink(bin_root):
            return False
        target = _bin_target(bin_root, rel)
        os.makedirs(os.path.dirname(target), mode=0o777, exist_ok=True)
        shutil.move(path, target)
        now = time.time()
        try:
            os.utime(target, (now, now))     # the purge counts the days from here
        except OSError:
            pass
        return True


class _Handler(FTPHandler):
    abstracted_fs = SharesFS
    banner = "Simple NAS FTP ready."
    permit_foreign_addresses = False
    permit_privileged_ports = False
    timeout = 300
    use_sendfile = False


def make_handler(authorizer, passive_ports=None, masquerade_address=None):
    handler = type("SimpleNasFTPHandler", (_Handler,), {})
    handler.authorizer = authorizer
    handler.passive_ports = passive_ports or parse_passive_ports(None)
    if masquerade_address:
        handler.masquerade_address = masquerade_address
    return handler


def serve(handler, port, host="0.0.0.0"):
    """ThreadedFTPServer on its own port, in a thread. Returns the server or raises OSError."""
    os.makedirs(ROOT_DIR, exist_ok=True)
    os.chmod(ROOT_DIR, 0o555)
    # pyftpdlib logs every connection and transfer; a camera would flood the add-on log.
    # Failed logins and lockouts are logged by SambaAuthorizer. Without a handler of its own pyftpdlib
    # would set up logging at INFO when the loop starts.
    if logging.getLogger("pyftpdlib").handlers:
        logging.getLogger("pyftpdlib").setLevel(logging.WARNING)
    else:
        config_logging(level=logging.WARNING)
    server = ThreadedFTPServer((host, port), handler)
    server.max_cons = 32
    server.max_cons_per_ip = 8
    threading.Thread(target=server.serve_forever, kwargs={"handle_exit": False}, daemon=True,
                     name="ftp").start()
    return server
