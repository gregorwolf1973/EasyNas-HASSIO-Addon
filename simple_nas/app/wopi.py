#!/usr/bin/env python3
"""Collabora Online glue: discovery, access tokens, proof keys, locks.

Collabora never stores documents. It opens and saves them through WOPI calls
to the host that embedded it - here the public share site. This module holds
everything that is not HTTP routing, so it stays Flask-free and testable:

- discovery: which file types Collabora handles and the editor URL for each,
  plus the RSA proof key Collabora signs its WOPI calls with
- access tokens: HMAC-signed, bound to one link, one file, one account and the
  link/account auth epochs, short-lived; the file id is derived from the same
  pair so a token for one file never opens another
- proof check: RSA PKCS#1 v1.5 / SHA-256 in pure Python (no extra package in
  the image), so only the configured Collabora can call the WOPI endpoints
- locks: persisted in /data so a worker restart does not orphan an open editor
"""

import base64
import hashlib
import hmac
import json
import os
import re
import ssl
import struct
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlsplit

LOCKS_FILE = "wopi_locks.json"
LOCK_SECONDS = 30 * 60            # WOPI: a lock expires after 30 minutes without refresh
MAX_LOCK_LEN = 1024
PROOF_MAX_AGE = 20 * 60           # WOPI: reject proofs older than 20 minutes
DISCOVERY_TTL = 12 * 3600
DISCOVERY_RETRY = 60
TOKEN_VERSION = "1"


class WopiError(ValueError):
    pass


# ── helpers ──────────────────────────────────────────────────────────────────

def _b64e(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s):
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def origin_of(url):
    """'https://Host:443/x' -> 'https://host:443'; '' when not an http(s) URL."""
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if p.scheme not in ("http", "https") or not p.hostname:
        return ""
    host = p.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"{p.scheme}://{host}" + (f":{p.port}" if p.port else "")


def iso_mtime(mtime):
    """LastModifiedTime as Collabora echoes it back in X-COOL-WOPI-Timestamp.
    Deterministic for one mtime, so comparing strings is exact."""
    whole = int(mtime)
    micro = int(round((mtime - whole) * 1_000_000))
    if micro >= 1_000_000:
        whole, micro = whole + 1, micro - 1_000_000
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(whole)) + f".{micro:06d}Z"


def file_id(link_id, rel):
    """Stable id per (link, file): every editor of the same file on the same
    link joins one Collabora session. Reveals neither the path nor the token."""
    return hashlib.sha256(f"wopi\0{link_id}\0{rel}".encode()).hexdigest()[:40]


# ── discovery ────────────────────────────────────────────────────────────────

_PLACEHOLDER = re.compile(r"<[^>]*>")


def parse_discovery(xml_text, public_base):
    """{'actions': {ext: {action: urlsrc}}, 'proof': {...} or None}.

    Collabora writes its urlsrc with whatever host the discovery request used.
    Fetched over an internal address that host is useless to a browser, so the
    origin is always replaced by the public Collabora URL."""
    root = ET.fromstring(xml_text)
    pub = origin_of(public_base)
    actions = {}
    for action in root.iter("action"):
        ext = (action.get("ext") or "").lower().lstrip(".")
        name = action.get("name") or ""
        src = action.get("urlsrc") or ""
        if not ext or not name or not src:
            continue
        src = _PLACEHOLDER.sub("", src)
        if pub:
            p = urlsplit(src)
            src = pub + p.path + ("?" + p.query if p.query else "?")
        actions.setdefault(ext, {})[name] = src
    proof = None
    pk = next(iter(root.iter("proof-key")), None)
    if pk is not None and pk.get("modulus") and pk.get("exponent"):
        proof = {
            "modulus": pk.get("modulus"), "exponent": pk.get("exponent"),
            "oldmodulus": pk.get("oldmodulus") or pk.get("modulus"),
            "oldexponent": pk.get("oldexponent") or pk.get("exponent"),
        }
    return {"actions": actions, "proof": proof}


def fetch_discovery(url, verify_tls=True, timeout=8):
    ctx = ssl.create_default_context()
    if not verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "SimpleNAS-WOPI"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:   # noqa: S310 - admin-configured URL
        return r.read(2 * 1024 * 1024).decode("utf-8", "replace")


class Discovery:
    """Cached discovery. A failure is retried after a minute, not on every
    request, so an unreachable Collabora never slows down the file listing."""

    def __init__(self, fetch=fetch_discovery, clock=time.time):
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._data = None
        self._ts = 0.0
        self._failed_ts = 0.0
        self._key = None
        self.error = ""

    def get(self, public_url, internal_url="", verify_tls=True, force=False):
        public_url = str(public_url or "").strip().rstrip("/")
        if not public_url:
            return None
        base = (str(internal_url or "").strip().rstrip("/") or public_url)
        key = (public_url, base, bool(verify_tls))
        now = self._clock()
        with self._lock:
            if key != self._key:
                self._key, self._data, self._ts, self._failed_ts = key, None, 0.0, 0.0
            fresh = self._data is not None and now - self._ts < DISCOVERY_TTL
            if fresh and not force:
                return self._data
            if not force and self._failed_ts and now - self._failed_ts < DISCOVERY_RETRY:
                return self._data            # stale data beats none; None if never loaded
            try:
                data = parse_discovery(self._fetch(base + "/hosting/discovery", verify_tls), public_url)
                if not data["actions"]:
                    raise WopiError("Discovery enthaelt keine Dateitypen")
            except Exception as e:           # network, TLS, XML - all mean "not now"
                self._failed_ts = now
                self.error = f"{type(e).__name__}: {e}"
                return self._data
            self._data, self._ts, self._failed_ts, self.error = data, now, 0.0, ""
            return data


def pick_action(discovery, filename, want_write):
    """(action name, urlsrc) for this file, or (None, None) if Collabora cannot
    open it. Writers get 'edit'; readers get 'view' where one exists, else the
    edit URL (CheckFileInfo's UserCanWrite=false keeps it read-only)."""
    if not discovery:
        return None, None
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    acts = discovery["actions"].get(ext) or {}
    order = ("edit", "view") if want_write else ("view", "edit")
    for name in order:
        if name in acts:
            return name, acts[name]
    return None, None


def editor_url(urlsrc, wopi_src, lang="de"):
    sep = "" if urlsrc.endswith(("?", "&")) else ("&" if "?" in urlsrc else "?")
    return f"{urlsrc}{sep}WOPISrc={quote(wopi_src, safe='')}&lang={quote(lang)}&closebutton=1"


# ── access tokens ────────────────────────────────────────────────────────────

def _token_key(secret):
    return hmac.new(str(secret).encode(), b"simplenas-wopi-token", hashlib.sha256).digest()


def make_token(secret, link_id, rel, user, can_write, link_epoch, user_epoch, ttl, origin="", now=None):
    """origin: the page that frames Collabora, returned as PostMessageOrigin."""
    now = int(now if now is not None else time.time())
    claims = {"v": TOKEN_VERSION, "l": link_id, "p": rel, "u": user or "", "w": 1 if can_write else 0,
              "le": int(link_epoch or 1), "ue": int(user_epoch or 0), "o": origin or "", "e": now + int(ttl)}
    body = _b64e(json.dumps(claims, separators=(",", ":"), ensure_ascii=False).encode())
    sig = _b64e(hmac.new(_token_key(secret), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}", claims["e"]


def read_token(secret, token, now=None):
    """Claims dict, or None for anything forged, malformed or expired."""
    if not token or len(token) > 4096 or token.count(".") != 1:
        return None
    body, sig = token.split(".")
    want = _b64e(hmac.new(_token_key(secret), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, want):
        return None
    try:
        claims = json.loads(_b64d(body))
    except (ValueError, TypeError):
        return None
    if not isinstance(claims, dict) or claims.get("v") != TOKEN_VERSION:
        return None
    now = now if now is not None else time.time()
    if not isinstance(claims.get("e"), int) or now > claims["e"]:
        return None
    if not isinstance(claims.get("l"), str) or not isinstance(claims.get("p"), str):
        return None
    if not isinstance(claims.get("le"), int) or not isinstance(claims.get("ue"), int):
        return None
    return claims


# ── proof keys ───────────────────────────────────────────────────────────────

_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _b64int(s):
    return int.from_bytes(base64.b64decode(s), "big")


def rsa_verify_sha256(modulus_b64, exponent_b64, message, signature):
    """RSASSA-PKCS1-v1_5 with SHA-256. Pure Python: one modular exponentiation
    and a constant-time compare against the padded digest."""
    try:
        n, e = _b64int(modulus_b64), _b64int(exponent_b64)
    except (ValueError, TypeError):
        return False
    k = (n.bit_length() + 7) // 8
    if not signature or len(signature) != k or n < 2 ** 1023:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if k < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def proof_message(access_token, url, timestamp):
    tok = access_token.encode()
    u = url.upper().encode()
    return (struct.pack(">i", len(tok)) + tok + struct.pack(">i", len(u)) + u
            + struct.pack(">i", 8) + struct.pack(">q", int(timestamp)))


def ticks_to_unix(ticks):
    return int(ticks) / 10_000_000 - 62135596800


def verify_proof(proof, access_token, urls, timestamp, sig_b64, old_sig_b64, now=None):
    """True when Collabora really signed one of the candidate URLs.

    urls holds every spelling the request could have been signed under (the
    configured WOPI base vs. the Host header behind a proxy); Collabora signed
    the one it called. Current key with either signature, or the old key with
    the current signature, covers a key rotation in progress."""
    try:
        ts = int(timestamp)
        sig = base64.b64decode(sig_b64 or "")
        old_sig = base64.b64decode(old_sig_b64) if old_sig_b64 else b""
    except (ValueError, TypeError):
        return False
    now = now if now is not None else time.time()
    if abs(now - ticks_to_unix(ts)) > PROOF_MAX_AGE:
        return False
    for url in dict.fromkeys(u for u in urls if u):
        msg = proof_message(access_token, url, ts)
        if rsa_verify_sha256(proof["modulus"], proof["exponent"], msg, sig):
            return True
        if old_sig and rsa_verify_sha256(proof["modulus"], proof["exponent"], msg, old_sig):
            return True
        if rsa_verify_sha256(proof["oldmodulus"], proof["oldexponent"], msg, sig):
            return True
    return False


# ── locks ────────────────────────────────────────────────────────────────────

class LockTable:
    """path -> (lock id, expiry). Persisted as JSON so a restarted worker keeps
    honouring the lock an open editor holds; one process writes it."""

    def __init__(self, data_dir, clock=time.time):
        self._path = os.path.join(data_dir, LOCKS_FILE)
        self._clock = clock
        self._mutex = threading.Lock()

    def _load(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(d, dict):
            return {}
        now = self._clock()
        return {p: v for p, v in d.items()
                if isinstance(v, dict) and isinstance(v.get("id"), str) and float(v.get("exp", 0)) > now}

    def _save(self, d):
        tmp = f"{self._path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, self._path)
        except OSError:
            pass

    def get(self, path):
        with self._mutex:
            v = self._load().get(path)
            return v["id"] if v else ""

    def lock(self, path, lock_id, old_lock=None):
        """(ok, current). Same id refreshes; with old_lock this is
        UnlockAndRelock and only succeeds when old_lock is the holder."""
        if not lock_id or len(lock_id) > MAX_LOCK_LEN:
            return False, ""
        with self._mutex:
            d = self._load()
            cur = d.get(path, {}).get("id", "")
            if old_lock is not None:
                if cur != old_lock:
                    return False, cur
            elif cur and cur != lock_id:
                return False, cur
            d[path] = {"id": lock_id, "exp": self._clock() + LOCK_SECONDS}
            self._save(d)
            return True, lock_id

    def refresh(self, path, lock_id):
        with self._mutex:
            d = self._load()
            cur = d.get(path, {}).get("id", "")
            if not cur or cur != lock_id:
                return False, cur
            d[path]["exp"] = self._clock() + LOCK_SECONDS
            self._save(d)
            return True, cur

    def unlock(self, path, lock_id):
        with self._mutex:
            d = self._load()
            cur = d.get(path, {}).get("id", "")
            if not cur or cur != lock_id:
                return False, cur
            d.pop(path, None)
            self._save(d)
            return True, ""
