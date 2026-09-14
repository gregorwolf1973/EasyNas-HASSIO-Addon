#!/usr/bin/env python3
"""Glue between the privileged admin process and the unprivileged share worker.

Step 9 of the sharing plan runs the public site as a SEPARATE process that is
dropped to `nobody`, stripped of the dangerous capabilities, and locked into a
mount namespace where the Home Assistant config, the SSL store, other add-ons'
configs, backups, and the secret files under /data are simply not there. A
code-execution hole in the internet-facing code then reaches only the shared
folders and its own handful of state files - not the host.

The two processes no longer share memory, so three things move to files in
/data (which the worker keeps, hidden or not, in its jail):

- the rate-limiter snapshot the admin "Sperren" card shows (worker writes,
  admin reads)
- unlock requests (admin appends, worker applies)
- the access log (worker writes; the admin process ships it to the CrowdSec
  path under /config, which the worker cannot see)

This module is import-safe on any OS (the Windows test box has no fcntl) and
holds no Flask or waitress imports, so it is cheap to pull into both processes.
"""
import json
import os
import time

try:
    import fcntl
except ImportError:            # Windows test host
    fcntl = None

SNAPSHOT_FILE = "share_locks.json"
UNLOCK_FILE = "share_unlock.json"
OPTS_FILE = "share_worker_opts.json"
LOG_FILE = "share_access.log"

# /data stays live inside the jail (so writes and admin edits are shared), but
# these secret files/dirs in it are masked with an empty file / empty tmpfs, so
# leaking the log or a link never leaks the admin password or the Samba db.
JAIL_MASK_FILES = ("admin_auth.json", "options.json")
JAIL_MASK_DIRS = ("samba",)
# Top-level trees blanked with an empty tmpfs inside the jail.
JAIL_HIDE_TREES = ("/config", "/ssl", "/addon_configs", "/backup")

# Files the worker must be able to read or write in /data (used only to seed
# them before launch, since a bind mount is no longer involved).
JAIL_SEED_FILES = (
    "share_links.json", "share_accounts.json", "share_auth.json", "shares.json",
    "share_counters.json", SNAPSHOT_FILE, UNLOCK_FILE, OPTS_FILE, LOG_FILE,
)


def _p(data_dir, name):
    return os.path.join(data_dir, name)


def curate_options(opt_reader, extra):
    """Options the worker may read: every share_* key (none are secret) plus a
    few the parent computes. Never the admin password."""
    out = {}
    for key in SANDBOX_OPT_KEYS:
        out[key] = opt_reader(key, None)
    out.update(extra)
    return out


# The share_* options create_share_app / accesslog / uploads actually read.
SANDBOX_OPT_KEYS = (
    "share_bind", "share_port", "share_public_url", "share_allowed_roots",
    "share_trusted_proxies", "share_cookie_secure", "share_session_hours",
    "share_log_max_mb", "share_zip_max_gb", "share_zip_max_files", "share_zip_compress",
    "share_max_upload_mb", "share_upload_blocked_ext", "share_upload_allowed_ext",
    "share_clamav_enabled", "share_clamav_host", "share_clamav_port",
    "share_clamav_timeout", "share_clamav_on_error", "share_clamav_large_file",
    "collabora_enabled", "collabora_url", "collabora_internal_url", "collabora_wopi_url",
    "collabora_verify_tls", "collabora_verify_proof",
)


def _atomic_write(path, obj):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_options(data_dir, opts):
    _atomic_write(_p(data_dir, OPTS_FILE), opts)


def read_options(data_dir):
    try:
        with open(_p(data_dir, OPTS_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_snapshot(data_dir, snap):
    """Worker: publish {ts, locks, counters, link_locked} for the admin card."""
    snap = dict(snap)
    snap["ts"] = round(time.time(), 3)
    try:
        _atomic_write(_p(data_dir, SNAPSHOT_FILE), snap)
    except OSError:
        pass


def read_snapshot(data_dir, max_age=30):
    """Admin: the worker's snapshot, or None when it is missing or stale
    (worker down). Stale rather than wrong keeps the card honest."""
    try:
        with open(_p(data_dir, SNAPSHOT_FILE), encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(snap, dict):
        return None
    if max_age and time.time() - float(snap.get("ts", 0)) > max_age:
        return None
    return snap


def _locked_file(path, mode):
    f = open(path, mode)
    if fcntl is not None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    return f


def queue_unlock(data_dir, key):
    """Admin: add one limiter key for the worker to clear. flock-guarded so a
    click and the worker's drain never race a lost write."""
    path = _p(data_dir, UNLOCK_FILE)
    with _locked_file(path, "a+") as f:
        f.seek(0)
        try:
            keys = json.load(f)
            if not isinstance(keys, list):
                keys = []
        except ValueError:
            keys = []
        if key not in keys:
            keys.append(key)
        f.seek(0)
        f.truncate()
        json.dump(keys, f)


def drain_unlocks(data_dir):
    """Worker: return the queued keys and empty the queue atomically."""
    path = _p(data_dir, UNLOCK_FILE)
    if not os.path.exists(path):
        return []
    try:
        with _locked_file(path, "r+") as f:
            try:
                keys = json.load(f)
            except ValueError:
                keys = []
            f.seek(0)
            f.truncate()
            json.dump([], f)
    except OSError:
        return []
    return [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else []
