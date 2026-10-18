#!/usr/bin/env python3
"""WebDAV: the Samba shares over HTTP(S) (issue #12), served by WsgiDAV on its own port.

- Every active share appears as /<share name>, with the same rules as in Samba: valid users and
  groups, read-only shares stay read-only. Public shares still need a login - WebDAV is often put
  behind a reverse proxy on the internet, where "no password" would mean "everybody".
- Login with the Samba user and password. Samba only keeps the NT hash (MD4 of the UTF-16 password),
  so the password a client sends is hashed the same way and compared - existing users work at once.
- Failed logins count per IP and per user name with the same limits and escalating lockouts as the
  share site; failures go to the access log (CrowdSec reads it).
- Deleting moves into the share's .recycle folder when the recycle bin is on, like Samba does.
- Symlinks are never followed (WsgiDAV default), so nothing outside a share is reachable.
- The root / lists the shares the logged-in user may open, so http://HA-IP:PORT/ is enough.
"""

import hmac
import ipaddress
import os
import random
import shutil
import struct
import subprocess
import threading
import time

from wsgidav.dav_error import HTTP_FORBIDDEN, DAVError
from wsgidav.dav_provider import DAVCollection, DAVProvider
from wsgidav.dc.base_dc import BaseDomainController
from wsgidav.fs_dav_provider import FileResource, FilesystemProvider, FolderResource
from wsgidav.wsgidav_app import WsgiDAVApp

from ratelimit import AUTHFAIL_IP, AUTHFAIL_USER, LIMITER, LOCK_BASE, LOCK_CAP

REALM_FALLBACK = "Simple NAS"
PASSWORD_TTL = 30                    # seconds; app.py also drops the cache when a password changes
MAX_UPLOAD = 64 * 1024 ** 3          # waitress spools the request body first, then WsgiDAV writes it
RECYCLE_EXCLUDE = (".tmp", ".temp", ".part", ".crdownload")  # deleted for good, as in smb.conf


# ── NT hash ───────────────────────────────────────────────────────────────
def md4(data: bytes) -> bytes:
    """MD4 (RFC 1320). OpenSSL 3 only offers it through the legacy provider, so hashlib often lacks it."""
    mask = 0xFFFFFFFF

    def rot(x, n):
        x &= mask
        return ((x << n) | (x >> (32 - n))) & mask

    message = bytearray(data) + b"\x80"
    message += b"\x00" * ((56 - len(message) % 64) % 64)
    message += struct.pack("<Q", (8 * len(data)) & 0xFFFFFFFFFFFFFFFF)
    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
    rounds = (
        (lambda x, y, z: (x & y) | (~x & z), range(16), (3, 7, 11, 19), 0),
        (lambda x, y, z: (x & y) | (x & z) | (y & z), (0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15),
         (3, 5, 9, 13), 0x5A827999),
        (lambda x, y, z: x ^ y ^ z, (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15), (3, 9, 11, 15), 0x6ED9EBA1),
    )
    for offset in range(0, len(message), 64):
        words = struct.unpack("<16I", message[offset:offset + 64])
        r = list(h)
        for fn, order, shifts, const in rounds:
            for i, k in enumerate(order):
                t = (0, 3, 2, 1)[i % 4]                 # a, d, c, b, a, d, ...
                x, y, z = r[(t + 1) % 4], r[(t + 2) % 4], r[(t + 3) % 4]
                r[t] = rot(r[t] + fn(x, y, z) + words[k] + const, shifts[i % 4])
        h = [(a + b) & mask for a, b in zip(h, r)]
    return struct.pack("<4I", *h)


def nt_hash(password: str) -> str:
    return md4(password.encode("utf-16-le")).hex().upper()


def parse_pdbedit(output: str) -> dict:
    """`pdbedit -L -w` (smbpasswd format) -> {user: NT hash}; disabled accounts are left out."""
    users = {}
    for line in output.splitlines():
        parts = line.split(":")
        if len(parts) < 5:
            continue
        name, nthash, flags = parts[0], parts[3].upper(), parts[4]
        if "D" in flags or len(nthash) != 32 or any(c not in "0123456789ABCDEF" for c in nthash):
            continue
        users[name] = nthash
    return users


class SambaPasswords:
    """NT hashes from Samba's password database, briefly cached."""

    def __init__(self, reader=None, ttl=PASSWORD_TTL, clock=time.monotonic):
        self._reader = reader or self._pdbedit
        self._ttl = ttl
        self._clock = clock
        self._cache = None
        self._loaded = 0.0
        self._lock = threading.Lock()

    @staticmethod
    def _pdbedit():
        proc = subprocess.run(["pdbedit", "-L", "-w"], capture_output=True, text=True, timeout=10)
        return proc.stdout

    def invalidate(self):
        with self._lock:
            self._cache = None

    def _users(self):
        with self._lock:
            if self._cache is None or self._clock() - self._loaded > self._ttl:
                try:
                    self._cache = parse_pdbedit(self._reader())
                except Exception as e:
                    print(f"[WEBDAV] Samba-Passwoerter nicht lesbar: {e}", flush=True)
                    self._cache = {}
                self._loaded = self._clock()
            return self._cache

    def check(self, user: str, password: str) -> bool:
        stored = self._users().get(user)
        given = nt_hash(password)
        # always compare, so a wrong user name takes as long as a wrong password
        return hmac.compare_digest(stored or "0" * 32, given) and stored is not None


# ── who may use which share ───────────────────────────────────────────────
def share_allows(share: dict, user: str, groups: list) -> bool:
    """Same rule as smb.conf: public -> everybody (but logged in here), else the listed users and
    groups, nothing listed -> every Samba user."""
    if share.get("public"):
        return True
    users = share.get("users") or []
    allowed_groups = set(share.get("groups") or [])
    if not users and not allowed_groups:
        return True
    if user in users:
        return True
    return any(g.get("name") in allowed_groups and user in (g.get("members") or []) for g in groups)


def mounted_points():
    points = set()
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    points.add(parts[1].replace("\\040", " "))
    except OSError:
        pass
    return points


def share_available(path: str, mounts=None) -> bool:
    """Like generate_smb_conf.share_available, without creating anything: below /media and /mnt
    a share only counts when a drive is really mounted there, never the empty mount point."""
    if not path or not os.path.isdir(path):
        return False
    if not (path.startswith("/media/") or path.startswith("/mnt/")):
        return True
    mounts = mounted_points() if mounts is None else mounts
    check = os.path.realpath(path)
    while check not in ("/", ""):
        if check in mounts:
            return True
        check = os.path.dirname(check)
    return False


def client_ip(environ, trusted_nets) -> str:
    """The visitor's address; proxy headers only count from a trusted proxy (share_trusted_proxies)."""
    remote = environ.get("REMOTE_ADDR") or "?"
    try:
        ip = ipaddress.ip_address(remote)
    except ValueError:
        return remote
    if any(ip in net for net in trusted_nets):
        cf = (environ.get("HTTP_CF_CONNECTING_IP") or "").strip()
        if cf:
            return cf
        xff = environ.get("HTTP_X_FORWARDED_FOR") or ""
        if xff:
            return xff.split(",")[-1].strip() or remote
    return remote


def trusted_networks(entries):
    nets = []
    for entry in entries or []:
        try:
            nets.append(ipaddress.ip_network(str(entry), strict=False))
        except ValueError:
            pass
    return nets


# ── recycle bin on DELETE ─────────────────────────────────────────────────
def _bin_target(bin_root: str, rel: str) -> str:
    """Path in the bin; an existing name gets "Copy #n of ..." like vfs_recycle (recycle:versions)."""
    target = os.path.join(bin_root, rel)
    if not os.path.lexists(target):
        return target
    folder, name = os.path.split(target)
    n = 1
    while os.path.lexists(os.path.join(folder, f"Copy #{n} of {name}")):
        n += 1
    return os.path.join(folder, f"Copy #{n} of {name}")


class _Recycling:
    """Mixin for file and folder resources: DELETE moves into <share>/.recycle when the bin is on."""

    def handle_delete(self):
        provider = self.provider
        if provider.readonly:
            raise DAVError(HTTP_FORBIDDEN)
        if not provider.recycle_on():
            return False
        root = provider.root_folder_path
        rel = os.path.relpath(self._file_path, root)
        first = rel.split(os.sep, 1)[0]
        name = os.path.basename(self._file_path)
        if (first == provider.recycle_dir or rel == "." or
                (not self.is_collection and (name.lower().endswith(RECYCLE_EXCLUDE) or name.startswith("~$")))):
            return False                    # inside the bin or a temp file: delete for good
        bin_root = os.path.join(root, provider.recycle_dir)
        if os.path.islink(bin_root):
            return False
        target = _bin_target(bin_root, rel)
        os.makedirs(os.path.dirname(target), mode=0o777, exist_ok=True)
        shutil.move(self._file_path, target)
        now = time.time()
        try:
            os.utime(target, (now, now))    # the purge counts the days from here
        except OSError:
            pass
        self.remove_all_properties(recursive=True)
        self.remove_all_locks(recursive=True)
        return True


class RecyclingFile(_Recycling, FileResource):
    pass


class RecyclingFolder(_Recycling, FolderResource):
    pass


class ShareProvider(FilesystemProvider):
    def __init__(self, share: dict, recycle_on, recycle_dir: str):
        super().__init__(share["path"], readonly=not share.get("writable", True),
                         fs_opts={"follow_symlinks": False, "shadow_map": {}})
        self.share = share
        self.recycle_on = lambda: recycle_on() and not share.get("timemachine")
        self.recycle_dir = recycle_dir

    def get_resource_inst(self, path, environ):
        res = super().get_resource_inst(path, environ)
        if res is None:
            return None
        cls = RecyclingFolder if res.is_collection else RecyclingFile
        return cls(path, environ, res._file_path)


# ── root: the shares this user may open ──────────────────────────────────
class _ShareEntry(DAVCollection):
    """Entry in the root listing; the share itself is served by its own provider."""

    def get_member_names(self):
        return []


class _Root(DAVCollection):
    def __init__(self, environ, names):
        super().__init__("/", environ)
        self._names = names

    def get_member_names(self):
        return list(self._names)

    def get_member(self, name):
        return _ShareEntry("/" + name, self.environ) if name in self._names else None


class RootProvider(DAVProvider):
    def __init__(self, shares: dict, load_groups):
        super().__init__()
        self._shares = shares
        self._load_groups = load_groups

    def is_readonly(self):
        return True

    def get_resource_inst(self, path, environ):
        if path.strip("/"):
            return None                     # not a share: 404
        user = environ.get("wsgidav.auth.user_name") or ""
        groups = self._load_groups()
        return _Root(environ, [n for n, s in sorted(self._shares.items()) if share_allows(s, user, groups)])


# ── login ────────────────────────────────────────────────────────────────
class SambaDomainController(BaseDomainController):
    """Basic auth against the Samba users. Digest is impossible without the clear password."""

    def __init__(self, wsgidav_app, config):
        super().__init__(wsgidav_app, config)
        self.nas = config["simple_nas"]

    def get_domain_realm(self, path_info, environ):
        return self._calc_realm_from_path_provider(path_info, environ) or REALM_FALLBACK

    def require_authentication(self, realm, environ):
        return True

    def supports_http_digest_auth(self):
        return False

    def basic_auth_user(self, realm, user_name, password, environ):
        nas = self.nas
        ip = client_ip(environ, nas["trusted_nets"]())
        ip_key, user_key = f"authfail:ip:{ip}", f"authfail:user:{(user_name or '').lower()}"
        limiter = nas["limiter"]
        if limiter.locked(ip_key, *AUTHFAIL_IP) or (user_name and limiter.locked(user_key, *AUTHFAIL_USER)):
            nas["log"]("rate_limited", ip=ip, user=user_name, detail="webdav locked")
            return False
        share = nas["shares"].get(realm.strip("/"))   # None: the root listing or an unknown path
        ok = bool(user_name) and nas["passwords"].check(user_name, password or "")
        if ok and (share is None or share_allows(share, user_name, nas["groups"]())):
            return True
        if ok:                               # right password, but not this share: no lockout
            nas["log"]("auth_fail", ip=ip, user=user_name, detail=f"webdav share {realm.strip('/')} not allowed")
            return False
        time.sleep(random.uniform(0.15, 0.35))
        for key, (limit, window) in ((ip_key, AUTHFAIL_IP), (user_key, AUTHFAIL_USER)):
            if key == user_key and not user_name:
                continue
            limiter.record(key)
            if limiter.count(key, window) >= limit:
                dur = limiter.lock(key, LOCK_BASE, LOCK_CAP)
                nas["log"]("rate_limited", ip=ip, detail=f"webdav lock {key.split(':', 1)[1]} {dur // 60} min")
        nas["log"]("auth_fail", ip=ip, user=user_name, detail="webdav", ua=environ.get("HTTP_USER_AGENT"))
        return False


# ── the WSGI app, rebuilt when the shares change ─────────────────────────
class _DavApp(WsgiDAVApp):
    def resolve_provider(self, path):
        """WsgiDAV 4.3 compares share paths in lower case but looks the provider up by the lower-case
        key, so a share named "Fotos" was never found. Hand back the share as it was registered."""
        share, provider = super().resolve_provider(path)
        if share is not None and provider is None:
            for key, candidate in self.provider_map.items():
                if key.lower() == share:
                    return key, candidate
        return share, provider


def build_app(shares, *, passwords, load_groups, recycle_on, recycle_dir=".recycle", trusted_nets=lambda: [],
              limiter=LIMITER, log=lambda *a, **k: None):
    active = {}
    mapping = {}
    mounts = mounted_points()
    for share in shares:
        name, path = share.get("name"), share.get("path", "")
        if not name or not share_available(path, mounts):
            continue
        active[name] = share
        mapping[f"/{name}"] = ShareProvider(share, recycle_on, recycle_dir)
    mapping["/"] = RootProvider(active, load_groups)
    config = {
        "provider_mapping": mapping,
        "http_authenticator": {"domain_controller": SambaDomainController, "accept_basic": True,
                               "accept_digest": False, "default_to_digest": False, "trusted_auth_header": None},
        "simple_nas": {"shares": active, "passwords": passwords, "groups": load_groups, "limiter": limiter,
                       "trusted_nets": trusted_nets, "log": log},
        "verbose": 1,
        "logging": {"enable": False},
        "property_manager": True,          # macOS Finder and Windows store properties
        "lock_storage": True,              # both need LOCK to write
        "dir_browser": {"enable": True, "icon": False, "response_trailer": "Simple NAS", "show_user": True,
                        "davmount": False, "ms_sharepoint_support": False},
    }
    return _DavApp(config), sorted(active)


class ReloadingApp:
    """Rebuilds the WsgiDAV app when shares.json changes or refresh() is called (mount, unmount)."""

    def __init__(self, shares_file, load_shares, **build_args):
        self._shares_file = shares_file
        self._load_shares = load_shares
        self._build_args = build_args
        self._lock = threading.Lock()
        self._key = None
        self._app = None
        self.share_names = []
        self._generation = 0

    def refresh(self):
        with self._lock:
            self._generation += 1

    def names(self):
        self._current()
        return list(self.share_names)

    def _current(self):
        try:
            mtime = os.stat(self._shares_file).st_mtime_ns
        except OSError:
            mtime = 0
        key = (mtime, self._generation)
        with self._lock:
            if key != self._key or self._app is None:
                self._app, self.share_names = build_app(self._load_shares(), **self._build_args)
                self._key = key
            return self._app

    def __call__(self, environ, start_response):
        return self._current()(environ, start_response)


def serve(app, port, host="0.0.0.0"):
    """waitress on its own port, in a thread. Returns the server (for tests) or raises OSError."""
    from waitress import create_server

    server = create_server(app, host=host, port=port, threads=8, ident=None, channel_timeout=600,
                           max_request_body_size=MAX_UPLOAD, asyncore_use_poll=True)
    threading.Thread(target=server.run, daemon=True, name="webdav").start()
    return server
