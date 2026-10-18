#!/usr/bin/env python3
"""External backups (Hetzner Storage Box, S3, B2, SFTP, WebDAV, ...) with
restic or rclone.

One target definition, two engines. rclone talks to the provider in both
cases:

* engine "rclone" mirrors every source 1:1 (optionally through an rclone
  crypt layer). Files that a run replaces or deletes are moved into a dated
  folder `.versions/<timestamp>/` on the target and purged after N days.
* engine "restic" keeps an encrypted, deduplicated archive with snapshots and
  reaches the provider through restic's rclone backend.

A local folder (second USB disk) is handed to both tools directly.

Everything secret - provider passwords, keys, the archive password - lives in
/data/remote_backup/, which the share sandbox masks (sharesandbox.JAIL_MASK_DIRS).
Only one job runs at a time; scheduled and manual runs share one queue.
"""
import collections
import copy
import ctypes
import datetime
import json
import os
import platform
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request

from flask import Blueprint, Response, jsonify, request

import safepath

bp = Blueprint("remote_backup", __name__)

ENGINES = ("restic", "rclone")
PROVIDERS = ("hetzner", "sftp", "s3", "b2", "webdav", "smb", "local", "rclone")
TARGET_FIELDS = {
    "hetzner": ("host", "port", "user", "password"),
    "sftp":    ("host", "port", "user", "password", "key_pem"),
    "s3":      ("s3_provider", "endpoint", "region", "access_key", "secret_key", "bucket"),
    "b2":      ("b2_account", "b2_key", "bucket"),
    "webdav":  ("url", "vendor", "user", "password"),
    "smb":     ("host", "user", "password", "domain", "share"),
    "local":   ("path",),
    "rclone":  ("rclone_conf",),
}
SECRET_TARGET_FIELDS = ("password", "key_pem", "secret_key", "b2_key", "rclone_conf")
MULTILINE_FIELDS = ("key_pem", "rclone_conf")
S3_PROVIDERS = ("Other", "AWS", "Wasabi", "Cloudflare", "Minio", "Scaleway",
                "IDrive", "DigitalOcean", "Ceph")
WEBDAV_VENDORS = ("other", "nextcloud", "owncloud")
FIELD_LABELS = {
    "host": "Server", "port": "Port", "user": "Benutzer", "password": "Passwort",
    "key_pem": "Privater Schlüssel", "s3_provider": "S3-Anbieter",
    "endpoint": "Endpoint", "region": "Region", "access_key": "Access Key",
    "secret_key": "Secret Key", "bucket": "Bucket", "b2_account": "Key-ID",
    "b2_key": "Application Key", "url": "URL", "vendor": "Anbieter",
    "domain": "Domäne", "share": "Freigabe", "path": "Ordner",
    "rclone_conf": "rclone-Konfiguration",
}
DEFAULT_RETENTION = {"daily": 7, "weekly": 4, "monthly": 12}
DEFAULT_EXCLUDES = [".upload-*.part"]
# Samba's recycle bin in every share (see app.py): deleted files, never backed up
RECYCLE_EXCLUDE = ".recycle"
# Parallel uploads (restic: rclone.connections, default 5; rclone: --transfers,
# default 4). A single SFTP stream rarely fills the line. Hetzner allows at
# most 10 connections per Storage Box, and rclone needs one or two on top.
DEFAULT_CONNECTIONS = 8
MAX_CONNECTIONS = 16
SCHEDULE_TYPES = ("manual", "daily", "weekly")
VERSIONS_DIR = ".versions"
TS_FMT = "%Y%m%d_%H%M%S"
RESTIC_HOST = "simplenas"           # fixed: the container hostname changes on every update
RESTIC_TAG = "simplenas"
START_DELAY = 120                   # let USB mounts come back before the first scheduled run
KIND_LABELS = {"backup": "Backup", "restore": "Wiederherstellung", "check": "Prüfung"}

_cfg = {"data_dir": "/data", "safe": None, "on_change": None}
_lock = threading.RLock()
_state = {}                         # job id -> runtime state of a queued/running job
_procs = {}                         # job id -> running Popen

# Backups run in the background: they get two cores less than the machine has
# (2 of 4 on a Pi 5) and the lowest CPU and disk priority, so Home Assistant,
# Samba and the other add-ons always come first. restic and rclone are Go
# programs and honour GOMAXPROCS.
BACKUP_CPU_CORES = max(1, (os.cpu_count() or 4) - 2)
# ioprio_set syscall per architecture - Python has no ionice of its own
_IOPRIO_SET = {"x86_64": 251, "aarch64": 30, "armv7l": 314, "armv6l": 314, "i686": 289, "i386": 289}


def _background_priority():
    """preexec_fn for restic/rclone: nice 19 and the idle I/O class."""
    try:
        os.nice(19)
    except OSError:
        pass
    nr = _IOPRIO_SET.get(platform.machine())
    if nr:
        try:
            # IOPRIO_WHO_PROCESS, this process, IOPRIO_CLASS_IDLE
            ctypes.CDLL(None, use_errno=True).syscall(nr, 1, 0, 3 << 13)
        except (OSError, AttributeError):
            pass
_queue = queue.Queue()
_threads = {}
_tools = {}

_NO_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_HOST_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,253}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SNAP_RE = re.compile(r"^(latest|[0-9a-f]{8,64})$")
_CONF_KEY_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_NO_REPO_RE = re.compile(r"does not exist|Is there a repository|repository not found", re.I)
_NOT_FOUND_RE = re.compile(r"directory not found|not found|no such file|does not exist", re.I)


class JobError(ValueError):
    """Invalid job settings. The message is shown to the admin."""


class RunError(RuntimeError):
    """A run failed. The message is shown to the admin."""


class _Cancelled(Exception):
    pass


def init(data_dir, safe, on_change=None):
    """safe(path) confines a client path to the file roots (raises PathError)."""
    _cfg.update(data_dir=data_dir, safe=safe, on_change=on_change)


def start():
    """Start the queue worker and the scheduler (only in the real add-on)."""
    _ensure_worker()
    if "sched" not in _threads:
        t = threading.Thread(target=_scheduler, daemon=True, name="remote-backup-sched")
        _threads["sched"] = t
        t.start()


# ─────────────────────────── storage ────────────────────────────

def _dir(*parts):
    return os.path.join(_cfg["data_dir"], "remote_backup", *parts)


def _jobs_file():
    return _dir("jobs.json")


def _write_private(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def load_jobs():
    try:
        with open(_jobs_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_jobs(jobs):
    with _lock:
        try:
            os.makedirs(_dir(), mode=0o700, exist_ok=True)
        except OSError:
            pass
        tmp = _jobs_file() + ".tmp"
        _write_private(tmp, json.dumps(jobs, indent=2))
        os.replace(tmp, _jobs_file())
    cb = _cfg.get("on_change")
    if cb:
        try:
            cb()
        except Exception as e:
            print(f"[REMOTE-BACKUP] Einstellungs-Sicherung fehlgeschlagen: {e}", flush=True)


def get_job(jid):
    return next((j for j in load_jobs() if j.get("id") == jid), None)


def _update_job(jid, **fields):
    with _lock:
        jobs = load_jobs()
        for j in jobs:
            if j.get("id") == jid:
                j.update(fields)
                save_jobs(jobs)
                return j
    return None


# ─────────────────────────── validation ─────────────────────────

def _text(value, label, maxlen=500, required=False, multiline=False, strip=True):
    s = "" if value is None else str(value)
    if multiline:
        s = s.replace("\r\n", "\n").replace("\r", "\n").strip()
        if re.search(r"[\x00-\x09\x0b-\x1f\x7f]", s):
            raise JobError(f"{label}: ungültige Zeichen")
    else:
        if strip:
            s = s.strip()
        if _NO_CTRL.search(s):
            raise JobError(f"{label}: ungültige Zeichen")
    if len(s) > maxlen:
        raise JobError(f"{label}: zu lang")
    if required and not s:
        raise JobError(f"{label} fehlt")
    return s


def _int(value, label, lo, hi, default):
    if value in (None, ""):
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise JobError(f"{label}: keine Zahl")
    if not lo <= n <= hi:
        raise JobError(f"{label}: erlaubt sind {lo} bis {hi}")
    return n


def _sub_name(src):
    """Folder name of a source on an rclone target: /share -> share,
    /media/usb1 -> media_usb1."""
    s = src.replace("\\", "/").strip("/")
    s = re.sub(r"^[A-Za-z]:", "", s).strip("/")    # Windows test paths
    return s.replace("/", "_") or "root"


def _remote_path(value):
    s = _text(value, "Ordner auf dem Ziel", 300)
    s = s.replace("\\", "/").rstrip("/")
    if any(part == ".." for part in s.split("/")):
        raise JobError("Ordner auf dem Ziel: '..' ist nicht erlaubt")
    return s


def _parse_rclone_conf(text):
    """One pasted rclone.conf section -> [(key, value)]. The header line is
    optional and gets replaced by our own section name."""
    opts, header = [], False
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith(("#", ";")):
            continue
        if s.startswith("["):
            if header or opts:
                raise JobError("rclone-Konfiguration: bitte nur einen Abschnitt einfügen")
            header = True
            continue
        if "=" not in s:
            raise JobError(f"rclone-Konfiguration: Zeile ohne '=': {s[:40]}")
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip()
        if not _CONF_KEY_RE.match(k):
            raise JobError(f"rclone-Konfiguration: ungültiger Schlüssel '{k[:40]}'")
        opts.append((k, v))
    if not any(k == "type" for k, _ in opts):
        raise JobError("rclone-Konfiguration: 'type = ...' fehlt")
    return opts


def _normalize_target(t, ex_t, sources):
    if not isinstance(t, dict):
        t = {}
    prov = t.get("provider") or ex_t.get("provider")
    if prov not in PROVIDERS:
        raise JobError("Bitte einen Anbieter wählen")
    same = ex_t.get("provider") == prov
    out = {"provider": prov}
    for f in TARGET_FIELDS[prov]:
        ml = f in MULTILINE_FIELDS
        v = _text(t.get(f), FIELD_LABELS.get(f, f), 20000 if ml else 500, multiline=ml,
                  strip=f not in SECRET_TARGET_FIELDS)
        # Secrets come back empty from the UI (they are never sent out): keep the stored one.
        if not v and f in SECRET_TARGET_FIELDS and same:
            v = ex_t.get(f, "")
        out[f] = v

    if prov in ("hetzner", "sftp", "smb"):
        if not out["host"]:
            raise JobError("Server fehlt")
        if not _HOST_RE.match(out["host"]):
            raise JobError("Server: nur Name oder IP-Adresse, ohne Protokoll und Pfad")
        if not out["user"]:
            raise JobError("Benutzer fehlt")
    if prov == "hetzner":
        if not out["password"]:
            raise JobError("Passwort fehlt")
        # 22 = plain SFTP, always on; 23 = only with "SSH support" switched on
        out["port"] = _int(out["port"], "Port", 22, 23, 22)
        if out["port"] not in (22, 23):
            raise JobError("Port: 22 oder 23")
    if prov == "sftp":
        out["port"] = _int(out["port"], "Port", 1, 65535, 22)
        if not out["password"] and not out["key_pem"]:
            raise JobError("Passwort oder privaten Schlüssel angeben")
        if out["key_pem"] and "PRIVATE KEY" not in out["key_pem"]:
            raise JobError("Privater Schlüssel: bitte die komplette Schlüsseldatei einfügen")
    if prov == "smb":
        if not out["share"] or "/" in out["share"]:
            raise JobError("Freigabe: nur der Name der Freigabe, z. B. backup")
    if prov == "s3":
        out["s3_provider"] = out["s3_provider"] or "Other"
        if out["s3_provider"] not in S3_PROVIDERS:
            raise JobError("Unbekannter S3-Anbieter")
        for f in ("access_key", "secret_key", "bucket"):
            if not out[f]:
                raise JobError(f"{FIELD_LABELS[f]} fehlt")
        if out["s3_provider"] != "AWS" and not out["endpoint"]:
            raise JobError("Endpoint fehlt")
    if prov == "b2":
        for f in ("b2_account", "b2_key", "bucket"):
            if not out[f]:
                raise JobError(f"{FIELD_LABELS[f]} fehlt")
    if prov in ("s3", "b2") and "/" in out["bucket"]:
        raise JobError("Bucket: nur der Name, Unterordner gehören in 'Ordner auf dem Ziel'")
    if prov == "webdav":
        if not re.match(r"^https?://", out["url"]):
            raise JobError("URL muss mit https:// oder http:// beginnen")
        out["vendor"] = out["vendor"] or "other"
        if out["vendor"] not in WEBDAV_VENDORS:
            raise JobError("Unbekannter WebDAV-Anbieter")
    if prov == "local":
        if not out["path"]:
            raise JobError("Zielordner fehlt")
        out["path"] = _cfg["safe"](out["path"])
        for s in sources:
            if safepath.is_within(s, out["path"]) or safepath.is_within(out["path"], s):
                raise JobError("Der Zielordner darf weder in einer Quelle liegen noch eine enthalten")
    if prov == "rclone":
        if not out["rclone_conf"]:
            raise JobError("rclone-Konfiguration fehlt")
        _parse_rclone_conf(out["rclone_conf"])
    return out


def _conn_key(job):
    """Everything that decides WHERE the data goes. A change means a new
    archive, so the password may be set again."""
    t = {k: v for k, v in (job.get("target") or {}).items() if k not in SECRET_TARGET_FIELDS}
    return json.dumps([job.get("engine"), t, job.get("remote_path"), job.get("encrypt")], sort_keys=True)


def normalize(body, existing=None, require_sources=True):
    """Validate a job from the UI. existing: the stored job when editing."""
    if not isinstance(body, dict):
        raise JobError("Ungültige Anfrage")
    ex = existing or {}
    name = _text(body.get("name"), "Name", 64, required=True)
    engine = body.get("engine") or ex.get("engine") or "restic"
    if engine not in ENGINES:
        raise JobError("Unbekanntes Backup-Programm")

    raw = body.get("sources", ex.get("sources", []))
    if isinstance(raw, str):
        raw = raw.split("\n")
    if not isinstance(raw, list):
        raise JobError("Quellen: ungültig")
    sources = []
    for s in raw:
        s = _text(s, "Quelle", 1000)
        if s:
            p = _cfg["safe"](s)
            if p not in sources:
                sources.append(p)
    if require_sources and not sources:
        raise JobError("Mindestens eine Quelle angeben")
    for a in sources:
        for b in sources:
            if a != b and safepath.is_within(b, a):
                raise JobError(f"{a} liegt schon in {b}")
    if engine == "rclone":
        subs = [_sub_name(s) for s in sources]
        if len(set(subs)) != len(subs):
            raise JobError("Zwei Quellen ergeben denselben Zielordner")

    target = _normalize_target(body.get("target") or {}, ex.get("target") or {}, sources)
    remote_path = _remote_path(body.get("remote_path", ex.get("remote_path", "simplenas")))
    encrypt = True if engine == "restic" else bool(body.get("encrypt", ex.get("encrypt", False)))

    raw_ex = body.get("excludes", ex.get("excludes", DEFAULT_EXCLUDES))
    if isinstance(raw_ex, str):
        raw_ex = raw_ex.split("\n")
    if not isinstance(raw_ex, list):
        raise JobError("Ausschlüsse: ungültig")
    excludes = []
    for e in raw_ex:
        e = _text(e, "Ausschluss", 200)
        if e and e not in excludes:
            excludes.append(e)
    if len(excludes) > 100:
        raise JobError("Höchstens 100 Ausschlüsse")

    ret_in = body.get("retention") or ex.get("retention") or {}
    if not isinstance(ret_in, dict):
        ret_in = {}
    retention = {k: _int(ret_in.get(k), f"Aufbewahrung ({k})", 0, 9999, DEFAULT_RETENTION[k])
                 for k in DEFAULT_RETENTION}
    keep_days = _int(body.get("keep_versions_days", ex.get("keep_versions_days")),
                     "Versionen behalten (Tage)", 0, 36500, 30)

    sch = body.get("schedule") or ex.get("schedule") or {}
    if not isinstance(sch, dict):
        sch = {}
    stype = sch.get("type") or "manual"
    if stype not in SCHEDULE_TYPES:
        raise JobError("Unbekannter Zeitplan")
    stime = str(sch.get("time") or "03:00")
    if not _TIME_RE.match(stime):
        raise JobError("Uhrzeit bitte als HH:MM")
    schedule = {"type": stype, "time": stime,
                "weekday": _int(sch.get("weekday"), "Wochentag", 0, 6, 0)}

    try:
        bw = float(body.get("bwlimit_mbit", ex.get("bwlimit_mbit", 0)) or 0)
    except (TypeError, ValueError):
        raise JobError("Upload-Limit: keine Zahl")
    if not 0 <= bw <= 100000:
        raise JobError("Upload-Limit: 0 bis 100000 Mbit/s")
    conns = _int(body.get("connections", ex.get("connections")), "Parallele Verbindungen",
                 1, MAX_CONNECTIONS, DEFAULT_CONNECTIONS)

    job = {k: v for k, v in ex.items() if k in ("id", "initialized", "last", "last_backup", "last_slot", "running_since")}
    job.update(name=name, engine=engine, sources=sources, target=target,
               remote_path=remote_path, encrypt=encrypt, excludes=excludes,
               retention=retention, keep_versions_days=keep_days,
               schedule=schedule, bwlimit_mbit=bw, connections=conns,
               notify=bool(body.get("notify", ex.get("notify", True))))

    if ex and _conn_key(job) != _conn_key(ex):
        job["initialized"] = False
    pw = _text(body.get("password"), "Passwort", 200, strip=False)
    if not pw:
        pw = ex.get("password", "")
    elif job.get("initialized") and pw != ex.get("password"):
        raise JobError("Das Passwort kann nach dem ersten Backup nicht mehr geändert werden - "
                       "sonst lassen sich die gesicherten Daten nicht mehr lesen")
    if encrypt and len(pw) < 8:
        raise JobError("Passwort für die Verschlüsselung: mindestens 8 Zeichen")
    job["password"] = pw

    if not ex or schedule != ex.get("schedule"):
        slot = last_slot(schedule, datetime.datetime.now())
        job["last_slot"] = slot.isoformat(timespec="minutes") if slot else ""
    return job


def public(job):
    """The job as the UI sees it: secrets blanked, runtime state added."""
    j = copy.deepcopy(job)
    t = j.get("target") or {}
    for f in SECRET_TARGET_FIELDS:
        if f in t:
            t["has_" + f] = bool(t[f])
            t[f] = ""
    j["has_password"] = bool(j.get("password"))
    j["password"] = ""
    st = dict(_state.get(j.get("id")) or {})   # copy first: the worker adds keys while we read
    j["state"] = {k: v for k, v in st.items() if k not in ("cancel", "summary")} or None
    nxt = next_slot(j.get("schedule") or {}, datetime.datetime.now())
    j["next_run"] = nxt.isoformat(timespec="minutes") if nxt else ""
    return j


# ─────────────────────────── schedule ───────────────────────────

def last_slot(schedule, now):
    """Most recent scheduled time <= now, or None for manual jobs."""
    stype = schedule.get("type")
    if stype not in ("daily", "weekly"):
        return None
    hh, mm = (int(x) for x in str(schedule.get("time") or "03:00").split(":"))
    cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if stype == "daily":
        if cand > now:
            cand -= datetime.timedelta(days=1)
    else:
        cand -= datetime.timedelta(days=(cand.weekday() - int(schedule.get("weekday") or 0)) % 7)
        if cand > now:
            cand -= datetime.timedelta(days=7)
    return cand


def next_slot(schedule, now):
    slot = last_slot(schedule, now)
    if slot is None:
        return None
    return slot + datetime.timedelta(days=1 if schedule.get("type") == "daily" else 7)


def _tick(now):
    """Queue every job whose scheduled time has passed since its last start.
    A run missed while Home Assistant was off is caught up once."""
    due = []
    with _lock:
        jobs = load_jobs()
        changed = False
        for j in jobs:
            slot = last_slot(j.get("schedule") or {}, now)
            if slot is None:
                continue
            s = slot.isoformat(timespec="minutes")
            if (j.get("last_slot") or "") < s:
                j["last_slot"] = s
                changed = True
                due.append(j)
        if changed:
            save_jobs(jobs)
    for j in due:
        try:
            enqueue(j["id"], "backup")
            print(f"[REMOTE-BACKUP] Zeitplan: '{j.get('name')}' eingereiht", flush=True)
        except RunError as e:
            print(f"[REMOTE-BACKUP] Zeitplan: '{j.get('name')}' uebersprungen: {e}", flush=True)
    return [j["id"] for j in due]


def _resume_interrupted():
    """A backup still marked as running at start-up was cut off by a restart
    or an update of the add-on. Queue it again - restic and rclone skip what
    already arrived. Runs the user cancelled are not marked, so they stay off."""
    resumed = []
    for j in load_jobs():
        if not j.get("running_since"):
            continue
        try:
            enqueue(j["id"], "backup", {"resumed": True})
            resumed.append(j["id"])
            print(f"[REMOTE-BACKUP] '{j.get('name')}' wurde unterbrochen - wird fortgesetzt", flush=True)
        except RunError:
            pass
    return resumed


def _scheduler():
    time.sleep(START_DELAY)
    try:
        _resume_interrupted()
    except Exception as e:
        print(f"[REMOTE-BACKUP] Fortsetzen fehlgeschlagen: {e}", flush=True)
    while True:
        try:
            _tick(datetime.datetime.now())
        except Exception as e:
            print(f"[REMOTE-BACKUP] Zeitplan-Fehler: {e}", flush=True)
        time.sleep(30)


# ─────────────────────────── tools + config ─────────────────────

def tools():
    """Installed versions of restic and rclone (None = missing)."""
    if not _tools:
        for name in ("restic", "rclone"):
            ver = None
            if shutil.which(name):
                try:
                    r = subprocess.run([name, "version"], capture_output=True, text=True, timeout=15)
                    m = re.search(r"v?(\d+\.\d+\.\d+)", r.stdout or "")
                    ver = m.group(1) if m else "?"
                except Exception:
                    ver = "?"
            _tools[name] = ver
    return dict(_tools)


def _obscure(secret):
    """rclone stores passwords 'obscured' in its config (reversible, but
    required by the format)."""
    r = subprocess.run(["rclone", "obscure", "-"], input=secret + "\n",
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        raise RunError("rclone obscure fehlgeschlagen: " + _tail(r.stderr))
    return r.stdout.strip()


def _base(job):
    """Where the data lives: 't_<id>:bucket/path', or a plain local path."""
    t = job["target"]
    prov = t["provider"]
    rp = job.get("remote_path", "")
    if prov == "local":
        return os.path.join(t["path"], rp.lstrip("/")) if rp else t["path"]
    pre = t.get("bucket") if prov in ("s3", "b2") else t.get("share") if prov == "smb" else ""
    path = "/".join(x for x in (pre, rp.lstrip("/") if pre else rp) if x)
    return f"t_{job['id']}:{path}"


def rclone_root(job):
    if job["engine"] == "rclone" and job.get("encrypt"):
        return f"c_{job['id']}:"
    return _base(job)


def restic_repo(job):
    base = _base(job)
    return base if job["target"]["provider"] == "local" else "rclone:" + base


def _join(root, *parts):
    parts = [p.strip("/") for p in parts if p and p.strip("/")]
    if not parts:
        return root
    sep = "" if root.endswith((":", "/")) else "/"
    return root + sep + "/".join(parts)


def build_rclone_conf(job, obscure=_obscure):
    t = job["target"]
    prov = t["provider"]
    name = f"t_{job['id']}"
    sections = []
    if prov == "hetzner":
        sections.append((name, [("type", "sftp"), ("host", t["host"]), ("user", t["user"]),
                                ("port", str(t.get("port") or 22)), ("pass", obscure(t["password"]))]))
    elif prov == "sftp":
        opts = [("type", "sftp"), ("host", t["host"]), ("user", t["user"]), ("port", str(t.get("port") or 22))]
        if t.get("password"):
            opts.append(("pass", obscure(t["password"])))
        if t.get("key_pem"):
            opts.append(("key_pem", t["key_pem"].replace("\n", "\\n")))
        sections.append((name, opts))
    elif prov == "s3":
        opts = [("type", "s3"), ("provider", t.get("s3_provider") or "Other"),
                ("access_key_id", t["access_key"]), ("secret_access_key", t["secret_key"])]
        if t.get("endpoint"):
            opts.append(("endpoint", t["endpoint"]))
        if t.get("region"):
            opts.append(("region", t["region"]))
        sections.append((name, opts))
    elif prov == "b2":
        # hard_delete: otherwise B2 only hides deleted files and prune frees nothing
        sections.append((name, [("type", "b2"), ("account", t["b2_account"]),
                                ("key", t["b2_key"]), ("hard_delete", "true")]))
    elif prov == "webdav":
        opts = [("type", "webdav"), ("url", t["url"]), ("vendor", t.get("vendor") or "other")]
        if t.get("user"):
            opts.append(("user", t["user"]))
        if t.get("password"):
            opts.append(("pass", obscure(t["password"])))
        sections.append((name, opts))
    elif prov == "smb":
        opts = [("type", "smb"), ("host", t["host"]), ("user", t["user"])]
        if t.get("password"):
            opts.append(("pass", obscure(t["password"])))
        if t.get("domain"):
            opts.append(("domain", t["domain"]))
        sections.append((name, opts))
    elif prov == "rclone":
        sections.append((name, _parse_rclone_conf(t["rclone_conf"])))
    if job["engine"] == "rclone" and job.get("encrypt"):
        sections.append((f"c_{job['id']}", [
            ("type", "crypt"), ("remote", _base(job)), ("password", obscure(job["password"])),
            ("filename_encryption", "standard"), ("directory_name_encryption", "true")]))
    out = []
    for sec, opts in sections:
        out.append(f"[{sec}]")
        out.extend(f"{k} = {v}" for k, v in opts)
        out.append("")
    return "\n".join(out)


def _prepare(job):
    """Write this job's rclone config and password file; return the env."""
    conf = _dir("run", f"{job['id']}.conf")
    _write_private(conf, build_rclone_conf(job))
    env = dict(os.environ)
    env["RCLONE_CONFIG"] = conf
    env["GOMAXPROCS"] = str(BACKUP_CPU_CORES)
    if job["engine"] == "restic":
        pw = _dir("run", f"{job['id']}.pw")
        _write_private(pw, job["password"])
        env["RESTIC_REPOSITORY"] = restic_repo(job)
        env["RESTIC_PASSWORD_FILE"] = pw
        env["RESTIC_CACHE_DIR"] = os.path.join(_cfg["data_dir"], "cache", "restic")
        env["RESTIC_PROGRESS_FPS"] = "0.2"
        env.pop("RESTIC_PASSWORD", None)
    return env


def _cleanup_run_files(jid):
    for ext in ("conf", "pw"):
        try:
            os.remove(_dir("run", f"{jid}.{ext}"))
        except OSError:
            pass


def _kib(job):
    """Upload limit in KiB/s (both tools take that unit), 0 = none."""
    return int(float(job.get("bwlimit_mbit") or 0) * 1_000_000 / 8 / 1024)


def _conns(job):
    try:
        n = int(job.get("connections") or DEFAULT_CONNECTIONS)
    except (TypeError, ValueError):
        n = DEFAULT_CONNECTIONS
    return max(1, min(MAX_CONNECTIONS, n))


def _restic_opts(job):
    """Parallel connections of restic's rclone backend (not for local)."""
    if job["target"]["provider"] == "local":
        return []
    return ["-o", f"rclone.connections={_conns(job)}"]


# ─────────────────────────── commands ───────────────────────────

def restic_backup_cmd(job):
    cmd = ["restic", "backup", "--json", "--host", RESTIC_HOST, "--tag", RESTIC_TAG]
    for e in [RECYCLE_EXCLUDE] + (job.get("excludes") or []):
        cmd += ["--exclude", e]
    if _kib(job):
        cmd += ["--limit-upload", str(_kib(job))]
    return cmd + _restic_opts(job) + list(job["sources"])


def restic_forget_cmd(job):
    r = job.get("retention") or DEFAULT_RETENTION
    if not any(r.get(k) for k in DEFAULT_RETENTION):
        return None                     # all 0 = keep every snapshot
    cmd = ["restic", "forget", "--prune", "--host", RESTIC_HOST, "--tag", RESTIC_TAG]
    for k in DEFAULT_RETENTION:
        if r.get(k):
            cmd += [f"--keep-{k}", str(r[k])]
    return cmd + _restic_opts(job)


def _rclone_common(job):
    cmd = ["--use-json-log", "--log-level", "NOTICE", "--stats", "5s",
           "--stats-log-level", "NOTICE", "--retries", "3", "--low-level-retries", "10",
           "--transfers", str(_conns(job))]
    if _kib(job):
        cmd += ["--bwlimit", f"{_kib(job)}K"]
    return cmd


def rclone_sync_cmds(job, ts):
    root = rclone_root(job)
    cmds = []
    for src in job["sources"]:
        sub = _sub_name(src)
        cmd = ["rclone", "sync", src, _join(root, sub),
               "--backup-dir", _join(root, VERSIONS_DIR, ts, sub), "--links"]
        for e in [f"{RECYCLE_EXCLUDE}/**"] + (job.get("excludes") or []):
            cmd += ["--exclude", e]
        cmds.append((src, cmd + _rclone_common(job)))
    return cmds


# ─────────────────────────── running ────────────────────────────

# rclone prints this NOTICE on every SFTP connect; it only buries the real error.
_NOISE_RE = re.compile(r"host key validation|known_hosts_file|#host-key-validation", re.I)
_EXPLAIN = (
    (re.compile(r"unable to authenticate|permission denied|auth(entication)? failed|401 unauthorized", re.I),
     "Anmeldung abgelehnt - Benutzer und Passwort prüfen. Hetzner Storage Box: Port 22 nehmen "
     "oder für Port 23 in der Hetzner Console \"SSH-Support\" einschalten."),
    (re.compile(r"connection refused", re.I),
     "Verbindung abgelehnt - Server oder Port falsch, oder der Zugang ist beim Anbieter nicht freigeschaltet."),
    (re.compile(r"no such host|name resolution|server misbehaving", re.I),
     "Server-Name nicht gefunden - Schreibweise prüfen."),
    (re.compile(r"i/o timeout|timed out|deadline exceeded", re.I),
     "Zeitüberschreitung - Server, Port oder Firewall prüfen."),
    (re.compile(r"wrong password or no key found", re.I),
     "Falsches Archiv-Passwort für das vorhandene Archiv."),
    (re.compile(r"InvalidAccessKeyId|SignatureDoesNotMatch|AccessDenied|bad_auth_token|unauthorized", re.I),
     "Zugangsdaten vom Anbieter abgelehnt - Key und Secret prüfen."),
    (re.compile(r"NoSuchBucket|bucket.{0,40}(not found|does not exist)", re.I),
     "Bucket nicht gefunden - bitte beim Anbieter anlegen."),
)


def _explain(text):
    for rx, hint in _EXPLAIN:
        if rx.search(text or ""):
            return hint
    return ""


def _tail(text, n=4):
    """Short error text for the UI: a plain-language hint when we know the
    cause, plus the last meaningful lines of the tool output."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip() and not _NOISE_RE.search(l)]
    detail = " | ".join(lines[-n:])[:500]
    hint = _explain(text)
    return f"{hint} (Details: {detail})" if hint and detail else (hint or detail)


def _fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


class _Log:
    MAX = 2 * 1024 * 1024

    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "w", encoding="utf-8")
        self.size = 0
        self.cut = False
        self.tail = collections.deque(maxlen=8)

    def write(self, text):
        text = str(text).rstrip()
        if not text:
            return
        self.tail.append(text)
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n"
        if self.size < self.MAX:
            self.f.write(line)
            self.f.flush()
            self.size += len(line)
        elif not self.cut:
            self.cut = True
            self.f.write("... Protokoll gekürzt\n")
            self.f.flush()

    def last(self):
        return _tail("\n".join(self.tail), 3)

    def close(self):
        try:
            self.f.close()
        except OSError:
            pass


def _set(jid, **fields):
    st = _state.get(jid)
    if st is not None:
        st.update(fields)


def _check_cancel(jid):
    if (_state.get(jid) or {}).get("cancel"):
        raise _Cancelled()


def _on_restic(jid, msg):
    """restic --json line -> progress (None) or a text for the log."""
    t = msg.get("message_type")
    if t == "status":
        done = msg.get("bytes_done", msg.get("bytes_restored"))
        _set(jid, percent=msg.get("percent_done"), bytes_done=done,
             bytes_total=msg.get("total_bytes"), eta=msg.get("seconds_remaining"))
        return None
    if t == "summary":
        _set(jid, summary=msg)
        if "data_added" in msg:
            return (f"Fertig: {msg.get('files_new', 0)} neue, {msg.get('files_changed', 0)} geänderte Dateien, "
                    f"{_fmt_bytes(msg.get('data_added'))} hochgeladen, Snapshot {msg.get('snapshot_id', '')[:8]}")
        return "Fertig"
    if t == "error":
        err = msg.get("error") or {}
        text = err.get("message") if isinstance(err, dict) else str(err)
        return f"Fehler: {msg.get('item', '')}: {text}"
    if t == "verbose_status":
        return None
    return json.dumps(msg)


def _on_rclone(jid, msg):
    stats = msg.get("stats")
    if isinstance(stats, dict):
        total = stats.get("totalBytes") or 0
        done = stats.get("bytes") or 0
        _set(jid, bytes_done=done, bytes_total=total, eta=stats.get("eta"),
             speed=stats.get("speed"), percent=(done / total) if total else None,
             transferred=stats.get("bytes"))
        return None
    level = msg.get("level", "")
    text = msg.get("msg", "")
    obj = msg.get("object")
    prefix = "Fehler: " if level == "error" else ""
    return f"{prefix}{obj + ': ' if obj else ''}{text}"


def _run_proc(jid, cmd, env, log, on_json=None):
    """Run one tool, stream its output into the log, return the exit code."""
    _check_cancel(jid)
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1, preexec_fn=_background_priority)
    _procs[jid] = proc
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if on_json and line.startswith("{"):
                try:
                    msg = json.loads(line)
                except ValueError:
                    msg = None
                if isinstance(msg, dict):
                    out = on_json(jid, msg)
                    if out:
                        log.write(out)
                    continue
            log.write(line)
        return proc.wait()
    finally:
        _procs.pop(jid, None)


def _quiet(cmd, env, timeout=300):
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout,
                          preexec_fn=_background_priority)


def _restic_repo_state(env, timeout=300):
    """'ok', 'missing', 'password' or ('error', text)."""
    r = _quiet(["restic", "cat", "config"], env, timeout)
    if r.returncode == 0:
        return "ok", ""
    err = (r.stderr or "") + (r.stdout or "")
    if r.returncode == 12 or "wrong password" in err.lower():
        return "password", err
    if r.returncode == 10 or _NO_REPO_RE.search(err):
        return "missing", err
    return "error", err


def _source_problem(src):
    """An unplugged USB disk leaves an empty mount point behind. Backing that
    up would make rclone delete the copy and let restic's retention rotate
    the good snapshots away - so an empty or missing source stops the run."""
    if not os.path.exists(src):
        return f"{src} fehlt"
    if os.path.isdir(src):
        try:
            if not os.listdir(src):
                return f"{src} ist leer (Laufwerk nicht eingebunden?)"
        except OSError as e:
            return f"{src} nicht lesbar: {e}"
    return None


def _do_backup(job, log):
    jid = job["id"]
    problems = [p for p in (_source_problem(s) for s in job["sources"]) if p]
    if problems:
        raise RunError("Abgebrochen, nichts gesichert: " + "; ".join(problems))
    env = _prepare(job)
    warning = ""
    if job["engine"] == "restic":
        _set(jid, phase="repo")
        state, err = _restic_repo_state(env)
        if state == "missing":
            log.write("Archiv existiert noch nicht - wird angelegt")
            r = _quiet(["restic", "init"], env)
            if r.returncode != 0:
                raise RunError("Archiv anlegen fehlgeschlagen: " + _tail(r.stderr or r.stdout))
            _update_job(jid, initialized=True)
        elif state == "password":
            raise RunError("Falsches Passwort für das vorhandene Archiv")
        elif state == "error":
            raise RunError("Ziel nicht erreichbar: " + _tail(err))
        _quiet(["restic", "unlock"], env)           # removes stale locks only
        _set(jid, phase="backup")
        rc = _run_proc(jid, restic_backup_cmd(job), env, log, _on_restic)
        _check_cancel(jid)
        if rc == 3:
            warning = "Einige Dateien konnten nicht gelesen werden (siehe Protokoll)"
        elif rc != 0:
            raise RunError(f"restic backup fehlgeschlagen (Exit {rc}): {log.last()}")
        s = (_state.get(jid) or {}).get("summary") or {}
        summary = (f"{s.get('files_new', 0)} neu, {s.get('files_changed', 0)} geändert, "
                   f"{_fmt_bytes(s.get('data_added'))} hochgeladen")
        forget = restic_forget_cmd(job)
        if forget:
            _set(jid, phase="prune", percent=None, bytes_done=None, bytes_total=None)
            rc = _run_proc(jid, forget, env, log)
            _check_cancel(jid)
            if rc != 0:
                warning = (warning + "; " if warning else "") + "Aufräumen alter Stände fehlgeschlagen"
        return summary, warning

    ts = datetime.datetime.now().strftime(TS_FMT)
    moved = 0
    for src, cmd in rclone_sync_cmds(job, ts):
        _set(jid, phase="sync", phase_arg=src, percent=None, bytes_done=None, bytes_total=None)
        rc = _run_proc(jid, cmd, env, log, _on_rclone)
        _check_cancel(jid)
        moved += (_state.get(jid) or {}).get("transferred") or 0
        if rc != 0:
            raise RunError(f"rclone sync {src} fehlgeschlagen (Exit {rc}): {log.last()}")
    if job.get("keep_versions_days"):
        _set(jid, phase="prune_versions", phase_arg="", percent=None)
        _prune_versions(job, env, log)
    return f"{_fmt_bytes(moved)} übertragen", warning


def _prune_versions(job, env, log):
    root = rclone_root(job)
    r = _quiet(["rclone", "lsf", "--dirs-only", _join(root, VERSIONS_DIR)], env)
    if r.returncode != 0:
        return                          # no .versions folder yet
    cutoff = datetime.datetime.now() - datetime.timedelta(days=int(job["keep_versions_days"]))
    for name in r.stdout.split():
        name = name.rstrip("/")
        try:
            stamp = datetime.datetime.strptime(name, TS_FMT)
        except ValueError:
            continue
        if stamp < cutoff:
            log.write(f"Lösche alte Versionen {name}")
            p = _quiet(["rclone", "purge", _join(root, VERSIONS_DIR, name)], env, 3600)
            if p.returncode != 0:
                log.write("Fehler beim Löschen: " + _tail(p.stderr))


def _do_check(job, log):
    if job["engine"] != "restic":
        raise RunError("Prüfen gibt es nur für restic")
    if not job.get("initialized"):
        raise RunError("Noch kein Backup vorhanden - erst \"Jetzt sichern\", dann prüfen")
    jid = job["id"]
    env = _prepare(job)
    _set(jid, phase="repo")
    # A backup cut off by a restart leaves its lock behind and `restic check`
    # needs an exclusive lock - it would fail with exit 11 until the next
    # backup. `unlock` removes stale locks only, never those of a live run.
    _quiet(["restic", "unlock"], env)
    rc = _run_proc(jid, ["restic", "check"] + _restic_opts(job), env, log)
    _check_cancel(jid)
    if rc != 0:
        raise RunError(f"Prüfung fehlgeschlagen (Exit {rc}): {log.last()}")
    return "Archiv in Ordnung"


def restore_cmd(job, snapshot, path, is_dir, target):
    if job["engine"] == "restic":
        cmd = ["restic", "restore", snapshot, "--target", target, "--json"]
        if path and path != "/":
            cmd += ["--include", path]
        return cmd + _restic_opts(job)
    root = rclone_root(job)
    rel = (path or "").strip("/")
    src = _join(root, rel) if rel else root
    # Old copies come back to where they were: .versions/<ts>/share/x -> share/x
    dest_rel = rel
    m = re.match(r"^\.versions/\d{8}_\d{6}/?(.*)$", rel)
    if m:
        dest_rel = m.group(1)
    dest = os.path.join(target, dest_rel) if dest_rel else target
    if rel and not is_dir:
        return ["rclone", "copyto", src, dest] + _rclone_common(job)
    cmd = ["rclone", "copy", src, dest, "--links"]
    if not rel:
        cmd += ["--exclude", f"/{VERSIONS_DIR}/**"]
    return cmd + _rclone_common(job)


def _do_restore(job, log, params):
    env = _prepare(job)
    target = params["target"]
    os.makedirs(target, exist_ok=True)
    cmd = restore_cmd(job, params.get("snapshot") or "latest", params.get("path") or "",
                      bool(params.get("is_dir", True)), target)
    _set(job["id"], phase="restore", phase_arg=target)
    rc = _run_proc(job["id"], cmd, env, log, _on_restic if job["engine"] == "restic" else _on_rclone)
    _check_cancel(job["id"])
    if rc != 0:
        raise RunError(f"Wiederherstellung fehlgeschlagen (Exit {rc}): {log.last()}")
    return f"Wiederhergestellt nach {target}"


def _notify(job, status, message):
    """Persistent notification in Home Assistant when a backup fails;
    removed again by the next successful run."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token or not job.get("notify"):
        return
    nid = f"simplenas_backup_{job['id']}"
    if status == "error":
        service, data = "create", {"notification_id": nid, "title": f"Simple NAS: Backup '{job['name']}' fehlgeschlagen",
                                   "message": message[:1000]}
    elif status in ("ok", "warning"):
        service, data = "dismiss", {"notification_id": nid}
    else:
        return
    req = urllib.request.Request(
        f"http://supervisor/core/api/services/persistent_notification/{service}",
        data=json.dumps(data).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as e:
        print(f"[REMOTE-BACKUP] Benachrichtigung fehlgeschlagen: {e}", flush=True)


def _execute(jid, kind, params):
    with _lock:
        st = _state.get(jid)
        if st and st.get("cancel"):
            _state.pop(jid, None)
            return
        job = get_job(jid)
        if not job:
            _state.pop(jid, None)
            return
        started = time.time()
        _state[jid] = {"running": True, "queued": False, "kind": kind, "phase": "start",
                       "started": started, "cancel": False}
    log = _Log(_dir("logs", f"{jid}.log"))
    log.write(f"{KIND_LABELS.get(kind, kind)} gestartet ({job['engine']}, Ziel {job['target']['provider']})")
    if params.get("resumed"):
        log.write("Fortsetzung nach einem Neustart - bereits Übertragenes wird übersprungen")
    if kind == "backup":
        # Cleared when the run ends in any way we see; still set at the next
        # start means the add-on went down mid-run (see _resume_interrupted).
        _update_job(jid, running_since=datetime.datetime.now().isoformat(timespec="seconds"))
    print(f"[REMOTE-BACKUP] {KIND_LABELS.get(kind, kind)} '{job['name']}' gestartet", flush=True)
    status, message = "ok", ""
    try:
        if kind == "backup":
            summary, warning = _do_backup(job, log)
            message = summary
            if warning:
                status, message = "warning", f"{warning} - {summary}"
        elif kind == "restore":
            message = _do_restore(job, log, params)
        elif kind == "check":
            message = _do_check(job, log)
    except _Cancelled:
        status, message = "cancelled", "Abgebrochen"
    except subprocess.TimeoutExpired:
        status, message = "error", "Zeitüberschreitung beim Zugriff auf das Ziel"
    except Exception as e:
        status, message = "error", str(e) or e.__class__.__name__
    finally:
        log.write(f"Ende: {status} - {message}")
        log.close()
        with _lock:
            _state.pop(jid, None)
    rec = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind,
           "status": status, "message": message[:1000], "duration": int(time.time() - started)}
    fields = {"last": rec, "running_since": ""}
    if kind == "backup":
        fields["last_backup"] = rec
        if status in ("ok", "warning"):
            fields["initialized"] = True
    _update_job(jid, **fields)
    print(f"[REMOTE-BACKUP] {KIND_LABELS.get(kind, kind)} '{job['name']}': {status} - {message}", flush=True)
    if kind == "backup":
        _notify(job, status, message)


def _worker():
    while True:
        jid, kind, params = _queue.get()
        try:
            _execute(jid, kind, params)
        except Exception as e:
            print(f"[REMOTE-BACKUP] Worker-Fehler: {e}", flush=True)
            with _lock:
                _state.pop(jid, None)


def _ensure_worker():
    if "worker" not in _threads:
        t = threading.Thread(target=_worker, daemon=True, name="remote-backup-worker")
        _threads["worker"] = t
        t.start()


def enqueue(jid, kind="backup", params=None):
    with _lock:
        st = _state.get(jid)
        if st:
            raise RunError("Läuft bereits" if st.get("running") else "Wartet bereits")
        _state[jid] = {"queued": True, "running": False, "kind": kind, "phase": "queued"}
    _ensure_worker()
    _queue.put((jid, kind, params or {}))


def cancel(jid):
    with _lock:
        st = _state.get(jid)
        if not st:
            return False
        st["cancel"] = True
        proc = _procs.get(jid)
    if proc and proc.poll() is None:
        proc.terminate()                # restic and rclone release their locks on SIGTERM

        def _kill():
            if proc.poll() is None:
                proc.kill()
        threading.Timer(20, _kill).start()
    return True


# ─────────────────────────── browsing ───────────────────────────

RELOCATED_EMPTY = ("Am neuen Ort liegen noch keine gesicherten Daten. Erst die vorhandenen Daten "
                   "dorthin verschieben (z. B. den Ordner beim Anbieter umbenennen), dann erneut testen. "
                   "Wer bewusst von vorn anfangen will, speichert einfach - das nächste Backup lädt dann alles neu hoch.")


def test_connection(job, relocated=False):
    """relocated: the target of a job that already has backups was changed.
    Then nothing is created there - an empty folder would hide the mistake
    and the next backup would silently start a second archive from scratch."""
    env = _prepare(job)
    try:
        if job["engine"] == "restic":
            state, err = _restic_repo_state(env, 90)
            if state == "ok":
                return "Verbindung ok - Archiv gefunden, Passwort stimmt"
            if state == "password":
                raise RunError("Archiv gefunden, aber das Passwort passt nicht")
            if state == "error":
                raise RunError("Ziel nicht erreichbar: " + _tail(err))
            if relocated:
                raise RunError(RELOCATED_EMPTY)
            if job["target"]["provider"] != "local":
                r = _quiet(["rclone", "mkdir", _base(job)], env, 90)
                if r.returncode != 0:
                    raise RunError("Ziel nicht erreichbar: " + _tail(r.stderr))
            return "Verbindung ok - das Archiv wird beim ersten Backup angelegt"
        root = rclone_root(job)
        if relocated:
            r = _quiet(["rclone", "lsf", "--max-depth", "1", root], env, 90)
            if r.returncode != 0 and _NOT_FOUND_RE.search(r.stderr or ""):
                raise RunError(RELOCATED_EMPTY)
            if r.returncode != 0:
                raise RunError("Ziel nicht erreichbar: " + _tail(r.stderr))
            if not r.stdout.strip():
                raise RunError(RELOCATED_EMPTY)
            return "Verbindung ok - gesicherte Daten gefunden"
        r = _quiet(["rclone", "mkdir", root], env, 90)
        if r.returncode == 0:
            r = _quiet(["rclone", "lsf", "--max-depth", "1", root], env, 90)
        if r.returncode != 0:
            raise RunError("Ziel nicht erreichbar: " + _tail(r.stderr))
        return "Verbindung ok"
    except subprocess.TimeoutExpired:
        raise RunError("Zeitüberschreitung - Server, Port oder Firewall prüfen")


def list_snapshots(job):
    if job["engine"] == "rclone":
        last = job.get("last_backup") or {}
        return [{"id": "latest", "short_id": "latest", "time": last.get("at", ""), "paths": job["sources"]}]
    env = _prepare(job)
    r = _quiet(["restic", "snapshots", "--json", "--host", RESTIC_HOST], env, 180)
    if r.returncode != 0:
        raise RunError("Stände lesen fehlgeschlagen: " + _tail(r.stderr))
    try:
        data = json.loads(r.stdout or "[]") or []
    except ValueError:
        raise RunError("Unerwartete Antwort von restic")
    out = [{"id": s.get("id", ""), "short_id": s.get("short_id") or s.get("id", "")[:8],
            "time": s.get("time", ""), "paths": s.get("paths") or [],
            "size": (s.get("summary") or {}).get("total_bytes_processed")} for s in data]
    out.sort(key=lambda s: s["time"], reverse=True)
    return out


def list_dir(job, snapshot, path):
    env = _prepare(job)
    if job["engine"] == "restic":
        path = "/" + (path or "").strip("/")
        r = _quiet(["restic", "ls", "--json", snapshot, path], env, 180)
        if r.returncode != 0:
            raise RunError("Inhalt lesen fehlgeschlagen: " + _tail(r.stderr))
        out = []
        for line in (r.stdout or "").splitlines():
            try:
                n = json.loads(line)
            except ValueError:
                continue
            if n.get("struct_type") != "node" or n.get("path") == path:
                continue
            parent = n.get("path", "").rsplit("/", 1)[0] or "/"
            if parent != path:
                continue                # older restic lists recursively
            out.append({"name": n.get("name"), "path": n.get("path"), "is_dir": n.get("type") == "dir",
                        "size": n.get("size"), "mtime": n.get("mtime")})
    else:
        rel = (path or "").strip("/")
        r = _quiet(["rclone", "lsjson", _join(rclone_root(job), rel)], env, 180)
        if r.returncode != 0:
            raise RunError("Inhalt lesen fehlgeschlagen: " + _tail(r.stderr))
        try:
            data = json.loads(r.stdout or "[]") or []
        except ValueError:
            raise RunError("Unerwartete Antwort von rclone")
        out = [{"name": e.get("Name"), "path": "/".join(x for x in (rel, e.get("Name")) if x),
                "is_dir": bool(e.get("IsDir")), "size": e.get("Size"), "mtime": e.get("ModTime")}
               for e in data]
    out.sort(key=lambda e: (not e["is_dir"], (e["name"] or "").lower()))
    return out


# ─────────────────────────── API ────────────────────────────────

def _err(msg, code=400):
    return jsonify({"error": msg}), code


def _body():
    return request.get_json(force=True, silent=True) or {}


def _job_or_404(jid):
    job = get_job(jid)
    if not job:
        return None, _err("Nicht gefunden", 404)
    return job, None


@bp.route("", methods=["GET"])
def api_list():
    return jsonify({"tools": tools(), "jobs": [public(j) for j in load_jobs()],
                    "s3_providers": list(S3_PROVIDERS), "webdav_vendors": list(WEBDAV_VENDORS)})


@bp.route("", methods=["POST"])
def api_create():
    try:
        job = normalize(_body())
    except JobError as e:
        return _err(str(e))
    with _lock:
        jobs = load_jobs()
        ids = {j.get("id") for j in jobs}
        jid = secrets.token_hex(4)
        while jid in ids:
            jid = secrets.token_hex(4)
        job = {"id": jid, "initialized": False, **job}
        jobs.append(job)
        save_jobs(jobs)
    print(f"[REMOTE-BACKUP] Job '{job['name']}' angelegt ({job['engine']} -> {job['target']['provider']})", flush=True)
    return jsonify(public(job)), 201


@bp.route("/<jid>", methods=["PUT"])
def api_update(jid):
    with _lock:
        jobs = load_jobs()
        idx = next((i for i, j in enumerate(jobs) if j.get("id") == jid), None)
        if idx is None:
            return _err("Nicht gefunden", 404)
        try:
            job = normalize(_body(), jobs[idx])
        except JobError as e:
            return _err(str(e))
        job["id"] = jid
        jobs[idx] = job
        save_jobs(jobs)
    return jsonify(public(job))


@bp.route("/<jid>", methods=["DELETE"])
def api_delete(jid):
    if jid in _state:
        return _err("Job läuft gerade - erst abbrechen", 409)
    with _lock:
        jobs = load_jobs()
        rest = [j for j in jobs if j.get("id") != jid]
        if len(rest) == len(jobs):
            return _err("Nicht gefunden", 404)
        save_jobs(rest)
    _cleanup_run_files(jid)
    try:
        os.remove(_dir("logs", f"{jid}.log"))
    except OSError:
        pass
    return jsonify({"ok": True})


@bp.route("/test", methods=["POST"])
def api_test():
    body = _body()
    existing = get_job(str(body.get("id") or "")) if body.get("id") else None
    try:
        job = normalize(body, existing, require_sources=False)
    except JobError as e:
        return _err(str(e))
    job["id"] = existing["id"] if existing else "probe" + secrets.token_hex(3)
    relocated = bool(existing and existing.get("initialized") and _conn_key(job) != _conn_key(existing))
    try:
        return jsonify({"ok": True, "message": test_connection(job, relocated)})
    except (RunError, OSError) as e:
        return _err(str(e))
    finally:
        if not existing:
            _cleanup_run_files(job["id"])


def _enqueue_response(jid, kind, params=None, message="Gestartet"):
    if not get_job(jid):
        return _err("Nicht gefunden", 404)
    try:
        enqueue(jid, kind, params)
    except RunError as e:
        return _err(str(e), 409)
    return jsonify({"ok": True, "message": message})


@bp.route("/<jid>/run", methods=["POST"])
def api_run(jid):
    return _enqueue_response(jid, "backup", message="Backup gestartet")


@bp.route("/<jid>/check", methods=["POST"])
def api_check(jid):
    return _enqueue_response(jid, "check", message="Prüfung gestartet")


@bp.route("/<jid>/cancel", methods=["POST"])
def api_cancel(jid):
    if not cancel(jid):
        return _err("Läuft nicht", 409)
    return jsonify({"ok": True})


@bp.route("/<jid>/log", methods=["GET"])
def api_log(jid):
    if not get_job(jid):
        return _err("Nicht gefunden", 404)
    try:
        with open(_dir("logs", f"{jid}.log"), encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            text = f.read()
    except OSError:
        text = ""
    return jsonify({"text": text})


@bp.route("/<jid>/password", methods=["GET"])
def api_password(jid):
    job, err = _job_or_404(jid)
    if err:
        return err
    print(f"[REMOTE-BACKUP] Passwort von '{job['name']}' angezeigt", flush=True)
    return jsonify({"password": job.get("password", "")})


@bp.route("/<jid>/notfallblatt", methods=["GET"])
def api_notfallblatt(jid):
    """Printable emergency sheet with the archive password - same access
    level as /password above (admin session); never cached."""
    import notfallblatt         # lazy: notfallblatt imports this module
    job, err = _job_or_404(jid)
    if err:
        return err
    print(f"[REMOTE-BACKUP] Notfallblatt von '{job['name']}' angezeigt", flush=True)
    page = notfallblatt.render(job, request.args.get("lang", "de"), datetime.datetime.now())
    return Response(page, mimetype="text/html", headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"})


@bp.route("/<jid>/snapshots", methods=["GET"])
def api_snapshots(jid):
    job, err = _job_or_404(jid)
    if err:
        return err
    try:
        return jsonify(list_snapshots(job))
    except subprocess.TimeoutExpired:
        return _err("Zeitüberschreitung beim Lesen der Stände", 504)
    except RunError as e:
        return _err(str(e), 502)


@bp.route("/<jid>/ls", methods=["GET"])
def api_ls(jid):
    job, err = _job_or_404(jid)
    if err:
        return err
    snap = request.args.get("snapshot", "latest")
    if not _SNAP_RE.match(snap):
        return _err("Ungültiger Stand")
    try:
        path = _text(request.args.get("path", ""), "Pfad", 4000)
        return jsonify(list_dir(job, snap, path))
    except JobError as e:
        return _err(str(e))
    except subprocess.TimeoutExpired:
        return _err("Zeitüberschreitung beim Lesen", 504)
    except RunError as e:
        return _err(str(e), 502)


@bp.route("/<jid>/restore", methods=["POST"])
def api_restore(jid):
    job, err = _job_or_404(jid)
    if err:
        return err
    body = _body()
    snap = str(body.get("snapshot") or "latest")
    if not _SNAP_RE.match(snap):
        return _err("Ungültiger Stand")
    try:
        path = _text(body.get("path"), "Pfad", 4000)
        target = _text(body.get("target"), "Zielordner", 1000, required=True)
    except JobError as e:
        return _err(str(e))
    if any(part == ".." for part in path.replace("\\", "/").split("/")):
        return _err("Ungültiger Pfad")
    target = _cfg["safe"](target)
    params = {"snapshot": snap, "path": path, "is_dir": bool(body.get("is_dir", True)), "target": target}
    return _enqueue_response(jid, "restore", params, "Wiederherstellung gestartet")
