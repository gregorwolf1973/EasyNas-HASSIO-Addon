#!/usr/bin/env python3
"""Storage and validation for web-sharing links and share accounts.

Deliberately Flask-free so the public share site (a separate app) and the
admin API can both use it. app.py hands in its load_json/save_json via init().

Files in /data:
  share_links.json     - mirrored to the reinstall-safe backup
  share_accounts.json  - mirrored, mode 0600 (password hashes)
  share_counters.json  - NOT mirrored: written on every download, and the
                         mirror copies all of /data on each write
"""

import hashlib
import os
import re
import secrets
import time

from werkzeug.security import check_password_hash, generate_password_hash

import safepath

# Unambiguous alphabet: people read these links aloud and retype them.
TOKEN_ALPHABET = "abcdefghijkmnopqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ2345679"
TOKEN_LEN = 22
_TOKEN_RE = re.compile(f"^[{TOKEN_ALPHABET}]{{{TOKEN_LEN}}}$")

USERNAME_RE = re.compile(r"^[a-zA-Z0-9._@-]{2,64}$")
MODES = ("download", "upload", "both")
ACCESS = ("public", "password", "users")
UPLOAD_SUBDIRS = ("none", "by-date", "by-user")

# Never a share root, whatever the configuration says.
SHARE_DENY = ("/", "/data", "/ssl", "/config", "/etc", "/proc", "/sys", "/dev",
              "/var", "/run", "/addon_configs", "/backup", "/boot", "/root")
DEFAULT_SHARE_ROOTS = ["/media", "/mnt", "/share"]

_data_dir = "/data"
_load = None
_save = None


class ShareError(ValueError):
    """Validation failure; the message is safe to show to the admin."""


def init(data_dir, load_json, save_json):
    global _data_dir, _load, _save
    _data_dir, _load, _save = data_dir, load_json, save_json


def _path(name):
    return os.path.join(_data_dir, name)


LINKS = "share_links.json"
ACCOUNTS = "share_accounts.json"
COUNTERS = "share_counters.json"


# ── tokens ───────────────────────────────────────────────────────────────────

def new_token():
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(TOKEN_LEN))


def token_shape_ok(token):
    return bool(token) and bool(_TOKEN_RE.match(token))


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


_index = {"mtime": None, "map": {}}


def _token_index():
    """sha256(token) -> link id, rebuilt when the links file changes.

    The hash is constant-time in the secret; the dict lookup afterwards works
    on a digest that says nothing about the token.
    """
    p = _path(LINKS)
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        mtime = -1
    if _index["mtime"] != mtime:
        _index["map"] = {_digest(l["token"]): l["id"] for l in _load(p, []) if l.get("token")}
        _index["mtime"] = mtime
    return _index["map"]


# ── counters ─────────────────────────────────────────────────────────────────

def _counters():
    c = _load(_path(COUNTERS), {})
    return c if isinstance(c, dict) else {}


def _save_counters(c):
    _save(_path(COUNTERS), c, mirror=False)


def record_download(link_id, ip=""):
    c = _counters()
    e = c.setdefault(link_id, {"download_count": 0, "uploaded_bytes": 0})
    e["download_count"] = int(e.get("download_count", 0)) + 1
    e["last_access"] = int(time.time())
    e["last_ip"] = ip
    _save_counters(c)


def record_upload(link_id, nbytes, ip=""):
    c = _counters()
    e = c.setdefault(link_id, {"download_count": 0, "uploaded_bytes": 0})
    e["uploaded_bytes"] = int(e.get("uploaded_bytes", 0)) + int(nbytes)
    e["last_access"] = int(time.time())
    e["last_ip"] = ip
    _save_counters(c)


def reset_counters(link_id):
    c = _counters()
    c.pop(link_id, None)
    _save_counters(c)


def _with_counters(link):
    e = _counters().get(link["id"], {})
    out = dict(link)
    out["download_count"] = int(e.get("download_count", 0))
    out["uploaded_bytes"] = int(e.get("uploaded_bytes", 0))
    out["last_access"] = int(e.get("last_access", 0))
    out["last_ip"] = e.get("last_ip", "")
    return out


# ── validation ───────────────────────────────────────────────────────────────

def validate_root(root, share_name="", file="", shares=(), allowed_roots=None):
    """Real path of a link root, or ShareError.

    1. resolve symlinks
    2. if tied to a Samba share, must sit under that share's path
    3. must sit under one of the allowed share roots
    4. never under the deny list
    5. must exist (and the single file, if given)
    Called on create/edit AND on every public request, so a deleted or
    re-pointed Samba share breaks its links instead of re-aiming them.
    """
    if not root or not str(root).strip():
        raise ShareError("Pfad fehlt")
    real = safepath.real(str(root).strip())
    for d in SHARE_DENY:
        if safepath.is_within(safepath.real(d), real) and safepath.real(d) != safepath.real("/"):
            raise ShareError(f"{d} darf nicht freigegeben werden")
    if real == safepath.real("/"):
        raise ShareError("Das Wurzelverzeichnis darf nicht freigegeben werden")
    if share_name:
        share = next((s for s in shares if s.get("name") == share_name), None)
        if share is None:
            raise ShareError(f"Samba-Freigabe „{share_name}“ gibt es nicht mehr")
        if not safepath.is_within(safepath.real(share.get("path", "")), real):
            raise ShareError(f"Pfad liegt nicht in der Freigabe „{share_name}“")
    roots = allowed_roots or DEFAULT_SHARE_ROOTS
    if not any(safepath.is_within(safepath.real(r), real) for r in roots):
        raise ShareError("Pfad liegt außerhalb der für Freigaben erlaubten Ordner")
    if not os.path.isdir(real):
        raise ShareError("Ordner nicht gefunden oder nicht eingehängt")
    if file:
        if "/" in file or "\\" in file or file in (".", "..") or "\x00" in file:
            raise ShareError("Ungültiger Dateiname")
        if not os.path.isfile(os.path.join(real, file)):
            raise ShareError("Datei nicht gefunden")
    return real


def _int(v, name, lo=0):
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        raise ShareError(f"{name} muss eine Zahl sein")
    if n < lo:
        raise ShareError(f"{name} darf nicht negativ sein")
    return n


def _normalise_link(body, existing=None, shares=(), allowed_roots=None):
    """Validated link dict from an admin request. existing = link being edited."""
    e = existing or {}
    out = dict(e)
    name = str(body.get("name", e.get("name", ""))).strip()
    if not name:
        raise ShareError("Name fehlt")
    out["name"] = name[:120]

    share_name = str(body.get("share", e.get("share", "")) or "").strip()
    root = str(body.get("root", e.get("root", "")) or "").strip().rstrip("/") or "/"
    file = str(body.get("file", e.get("file", "")) or "").strip()
    validate_root(root, share_name, file, shares, allowed_roots)
    out["share"], out["root"], out["file"] = share_name, root, file

    mode = body.get("mode", e.get("mode", "download"))
    if mode not in MODES:
        raise ShareError("Ungültiger Modus")
    if file and mode != "download":
        raise ShareError("Ein Einzeldatei-Link kann nur zum Herunterladen sein")
    out["mode"] = mode

    access = body.get("access", e.get("access", "password"))
    if access not in ACCESS:
        raise ShareError("Ungültige Zugriffsart")
    out["access"] = access

    pw = body.get("password")
    if access == "password":
        if pw:
            out["password_hash"] = generate_password_hash(str(pw))
        elif not e.get("password_hash"):
            raise ShareError("Passwort fehlt")
    else:
        out["password_hash"] = ""

    users = body.get("users", e.get("users", [])) or []
    if not isinstance(users, list):
        raise ShareError("users muss eine Liste sein")
    users = [str(u).strip() for u in users if str(u).strip()]
    if access == "users":
        known = {a["username"] for a in list_accounts()}
        unknown = [u for u in users if u not in known]
        if unknown:
            raise ShareError(f"Unbekanntes Konto: {', '.join(unknown)}")
    out["users"] = users

    out["expires"] = _int(body.get("expires", e.get("expires", 0)), "Ablauf")
    out["max_downloads"] = _int(body.get("max_downloads", e.get("max_downloads", 0)), "Download-Limit")
    out["upload_quota_mb"] = _int(body.get("upload_quota_mb", e.get("upload_quota_mb", 0)), "Upload-Kontingent")
    out["max_file_mb"] = _int(body.get("max_file_mb", e.get("max_file_mb", 0)), "Maximale Dateigröße")
    sub = body.get("upload_subdir", e.get("upload_subdir", "by-date"))
    if sub not in UPLOAD_SUBDIRS:
        raise ShareError("Ungültige Upload-Ablage")
    out["upload_subdir"] = sub
    out["allow_subdirs"] = bool(body.get("allow_subdirs", e.get("allow_subdirs", True)))
    out["allow_zip"] = bool(body.get("allow_zip", e.get("allow_zip", True)))
    out["enabled"] = bool(body.get("enabled", e.get("enabled", True)))
    out["notes"] = str(body.get("notes", e.get("notes", "")) or "")[:500]
    return out


# ── links ────────────────────────────────────────────────────────────────────

def _raw_links():
    l = _load(_path(LINKS), [])
    return l if isinstance(l, list) else []


def _save_links(links):
    _save(_path(LINKS), links)


def list_links():
    return [_with_counters(l) for l in _raw_links()]


def get_link(link_id):
    for l in _raw_links():
        if l.get("id") == link_id:
            return _with_counters(l)
    return None


def resolve_token(token):
    """Link for a token, or None. Shape check first so scanners never reach the index."""
    if not token_shape_ok(token):
        return None
    link_id = _token_index().get(_digest(token))
    return get_link(link_id) if link_id else None


def create_link(body, created_by="admin", shares=(), allowed_roots=None):
    links = _raw_links()
    link = _normalise_link(body, None, shares, allowed_roots)
    existing_tokens = {l.get("token") for l in links}
    token = new_token()
    while token in existing_tokens:
        token = new_token()
    link.update({
        "id": secrets.token_hex(4),
        "token": token,
        "auth_epoch": 1,
        "created": int(time.time()),
        "created_by": created_by,
    })
    links.append(link)
    _save_links(links)
    return _with_counters(link)


def _auth_relevant_changed(old, new):
    return (old.get("password_hash") != new.get("password_hash")
            or old.get("access") != new.get("access")
            or sorted(old.get("users", [])) != sorted(new.get("users", [])))


def update_link(link_id, body, shares=(), allowed_roots=None):
    links = _raw_links()
    for i, l in enumerate(links):
        if l.get("id") == link_id:
            new = _normalise_link(body, l, shares, allowed_roots)
            if _auth_relevant_changed(l, new):
                new["auth_epoch"] = int(l.get("auth_epoch", 1)) + 1   # kick live sessions
            links[i] = new
            _save_links(links)
            return _with_counters(new)
    raise ShareError("Link nicht gefunden")


def delete_link(link_id):
    links = _raw_links()
    left = [l for l in links if l.get("id") != link_id]
    if len(left) == len(links):
        raise ShareError("Link nicht gefunden")
    _save_links(left)
    reset_counters(link_id)


def rotate_token(link_id):
    links = _raw_links()
    for l in links:
        if l.get("id") == link_id:
            l["token"] = new_token()
            l["auth_epoch"] = int(l.get("auth_epoch", 1)) + 1
            _save_links(links)
            return _with_counters(l)
    raise ShareError("Link nicht gefunden")


def disable_links_for_share(share_name):
    """When a Samba share is deleted, its links must not silently re-aim."""
    links = _raw_links()
    n = 0
    for l in links:
        if l.get("share") == share_name and l.get("enabled"):
            l["enabled"] = False
            n += 1
    if n:
        _save_links(links)
    return n


def link_is_live(link, now=None):
    """Enabled, not expired, download limit not reached."""
    now = now or time.time()
    if not link.get("enabled"):
        return False
    if link.get("expires") and now > link["expires"]:
        return False
    if link.get("max_downloads") and link.get("download_count", 0) >= link["max_downloads"]:
        return False
    return True


# ── accounts ─────────────────────────────────────────────────────────────────

def _raw_accounts():
    a = _load(_path(ACCOUNTS), [])
    return a if isinstance(a, list) else []


def _save_accounts(accounts):
    p = _path(ACCOUNTS)
    _save(p, accounts)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _public(acc):
    out = {k: v for k, v in acc.items() if k != "password_hash"}
    return out


def list_accounts():
    return [_public(a) for a in _raw_accounts()]


def get_account(username):
    key = (username or "").strip().lower()
    for a in _raw_accounts():
        if a["username"].lower() == key:
            return a
    return None


def create_account(username, password, display_name="", created_by="admin"):
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise ShareError("Benutzername: 2–64 Zeichen, Buchstaben, Ziffern, . _ @ -")
    if not password or len(password) < 8:
        raise ShareError("Passwort: mindestens 8 Zeichen")
    if get_account(username):
        raise ShareError("Konto existiert bereits")
    accounts = _raw_accounts()
    acc = {
        "username": username,
        "password_hash": generate_password_hash(password),
        "display_name": (display_name or "").strip()[:80],
        "enabled": True,
        "auth_epoch": 1,
        "created": int(time.time()),
        "created_by": created_by,
        "last_login": 0,
    }
    accounts.append(acc)
    _save_accounts(accounts)
    return _public(acc)


def update_account(username, body):
    accounts = _raw_accounts()
    for a in accounts:
        if a["username"].lower() == (username or "").strip().lower():
            bump = False
            if body.get("password"):
                if len(body["password"]) < 8:
                    raise ShareError("Passwort: mindestens 8 Zeichen")
                a["password_hash"] = generate_password_hash(body["password"])
                bump = True
            if "display_name" in body:
                a["display_name"] = str(body["display_name"] or "").strip()[:80]
            if "enabled" in body:
                new_enabled = bool(body["enabled"])
                if a.get("enabled", True) and not new_enabled:
                    bump = True
                a["enabled"] = new_enabled
            if bump:
                a["auth_epoch"] = int(a.get("auth_epoch", 1)) + 1
            _save_accounts(accounts)
            return _public(a)
    raise ShareError("Konto nicht gefunden")


def delete_account(username):
    accounts = _raw_accounts()
    key = (username or "").strip().lower()
    left = [a for a in accounts if a["username"].lower() != key]
    if len(left) == len(accounts):
        raise ShareError("Konto nicht gefunden")
    _save_accounts(left)
    # drop it from every link's user list
    links = _raw_links()
    changed = False
    for l in links:
        if key in [u.lower() for u in l.get("users", [])]:
            l["users"] = [u for u in l["users"] if u.lower() != key]
            changed = True
    if changed:
        _save_links(links)


def check_account_password(username, password):
    acc = get_account(username)
    if not acc or not acc.get("enabled", True):
        return None
    if check_password_hash(acc.get("password_hash", ""), password or ""):
        return acc
    return None


def touch_login(username):
    accounts = _raw_accounts()
    for a in accounts:
        if a["username"].lower() == (username or "").lower():
            a["last_login"] = int(time.time())
            _save_accounts(accounts)
            return
